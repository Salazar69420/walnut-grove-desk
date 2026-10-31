"""Automations (Operations > Automations): ideas to start from, the person's own automations, running them, and email setup."""
from __future__ import annotations

import json
import re
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import Strict
from app.db import get_db
from app.models import Automation, AutomationRun, User
from app.security import crypto
from app.security.auth import require
from app.security.permissions import has_perm
from app.services import automations as A
from app.services import events as ev
from app.services import settings_store, sms_mode, timeutil

router = APIRouter(prefix="/api/automations", tags=["automations"])
TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def view(a: Automation) -> dict[str, Any]:
    return {"id": a.id, "name": a.name, "enabled": a.enabled, "trigger_type": a.trigger_type, "trigger": json.loads(a.trigger_json or "{}"), "action": a.action,
            "config": json.loads(a.action_json or "{}"), "template": a.template, "last_run_at": timeutil.iso(a.last_run_at) if a.last_run_at else None,
            "last_ok": a.last_ok, "last_result": a.last_result, "run_count": a.run_count}


@router.get("")
def overview(user: User = Depends(require("automations.view")), db: Session = Depends(get_db)):
    em = A.email_settings(db)
    return {"items": [view(a) for a in db.scalars(select(Automation).order_by(Automation.id)).all()],
            "templates": A.TEMPLATES, "events": A.EVENTS, "reports": A.REPORTS, "days": A.DAYS,
            "staff": [{"id": u.id, "name": u.display_name} for u in db.scalars(select(User).where(User.active.is_(True)).order_by(User.display_name)).all()],
            "text_numbers": sms_mode.get(db)["test_numbers"], "texting_mode": sms_mode.get(db)["mode"],
            "email": {"ready": bool(em["host"] and em["from_addr"]), "host": em["host"], "port": em["port"], "username": em["username"], "from_addr": em["from_addr"],
                      "security": em["security"], "has_password": bool(em["password_enc"])},
            "can_edit": has_perm(user, "automations.edit")}


class AutoIn(Strict):
    name: str = Field(min_length=3, max_length=120)
    trigger_type: Literal["schedule", "event"]
    trigger: dict[str, Any]
    action: Literal["report", "chat", "task", "text_client", "seo_audit"]
    config: dict[str, Any] = Field(default_factory=dict)
    template: str = Field(default="", max_length=40)
    enabled: bool = True


def _check(body: AutoIn) -> None:
    t = body.trigger
    if body.trigger_type == "schedule":
        days = t.get("days") or []
        if not days or any(d not in A.DAYS for d in days) or not TIME.match(str(t.get("time", ""))):
            raise HTTPException(status_code=400, detail="Pick at least one day and a time like 07:45.")
    elif t.get("event") not in A.EVENTS:
        raise HTTPException(status_code=400, detail="Pick what should start it.")
    c = body.config
    if body.action == "report":
        if c.get("report") not in A.REPORTS or c.get("channel") not in ("chat", "sms", "email"):
            raise HTTPException(status_code=400, detail="Pick a report and where to send it.")
        if c["channel"] == "sms" and not c.get("to"):
            raise HTTPException(status_code=400, detail="Pick the phone number to text it to.")
        if c["channel"] == "email" and "@" not in str(c.get("to") or ""):
            raise HTTPException(status_code=400, detail="Add the email address to send it to.")
    if body.action in ("chat", "text_client") and not str(c.get("text") or "").strip():
        raise HTTPException(status_code=400, detail="Write the message.")
    if body.action == "text_client" and body.trigger_type != "event":
        raise HTTPException(status_code=400, detail="Texting a client only works when something happens with that client.")
    if body.action == "task" and not str(c.get("title") or "").strip():
        raise HTTPException(status_code=400, detail="Give the task a title.")


@router.post("", status_code=201)
def create(body: AutoIn, user: User = Depends(require("automations.edit")), db: Session = Depends(get_db)):
    _check(body)
    a = Automation(name=body.name.strip(), enabled=body.enabled, trigger_type=body.trigger_type, trigger_json=json.dumps(body.trigger), action=body.action,
                   action_json=json.dumps(body.config), template=body.template, created_by=user.id, created_at=timeutil.now_utc())
    db.add(a)
    db.flush()
    ev.record(db, ev.ADMIN, "automation_created", f"{user.display_name} set up the automation \"{a.name}\"", user, "setting", f"automation:{a.id}")
    db.commit()
    return {"item": view(a)}


@router.put("/{aid}")
def update(aid: int, body: AutoIn, user: User = Depends(require("automations.edit")), db: Session = Depends(get_db)):
    a = db.get(Automation, aid)
    if a is None:
        raise HTTPException(status_code=404, detail="No such automation")
    _check(body)
    a.name, a.enabled, a.trigger_type, a.trigger_json, a.action, a.action_json = body.name.strip(), body.enabled, body.trigger_type, json.dumps(body.trigger), body.action, json.dumps(body.config)
    ev.record(db, ev.ADMIN, "automation_changed", f"{user.display_name} changed the automation \"{a.name}\" ({'on' if a.enabled else 'off'})", user, "setting", f"automation:{a.id}")
    db.commit()
    return {"item": view(a)}


@router.delete("/{aid}")
def delete(aid: int, user: User = Depends(require("automations.edit")), db: Session = Depends(get_db)):
    a = db.get(Automation, aid)
    if a is None:
        raise HTTPException(status_code=404, detail="No such automation")
    ev.record(db, ev.ADMIN, "automation_deleted", f"{user.display_name} deleted the automation \"{a.name}\"", user, "setting", f"automation:{a.id}")
    db.delete(a)
    db.commit()
    return {"ok": True}


@router.post("/{aid}/run")
def run_now(aid: int, user: User = Depends(require("automations.edit")), db: Session = Depends(get_db)):
    a = db.get(Automation, aid)
    if a is None:
        raise HTTPException(status_code=404, detail="No such automation")
    if a.action in ("text_client", "task") and a.trigger_type == "event":
        raise HTTPException(status_code=409, detail="This one runs when its event happens, for the client involved.")
    db.info["automation"] = True
    ok, result = A.run(db, a, {"details": "(a test run)", "name": "", "first_name": "there"}, by=f"run by {user.display_name}")
    return {"ok": ok, "result": result, "item": view(db.get(Automation, aid))}


@router.get("/{aid}/runs")
def runs(aid: int, user: User = Depends(require("automations.view")), db: Session = Depends(get_db)):
    rows = db.scalars(select(AutomationRun).where(AutomationRun.automation_id == aid).order_by(AutomationRun.id.desc()).limit(30)).all()
    return {"items": [{"at": timeutil.iso(r.at), "ok": r.ok, "detail": r.detail} for r in rows]}


@router.get("/preview/{report}")
def preview(report: str, user: User = Depends(require("automations.view")), db: Session = Depends(get_db)):
    if report not in A.REPORTS:
        raise HTTPException(status_code=404, detail="No such report")
    title, text = A.build_report(db, report)
    return {"title": title, "text": text}


class EmailIn(Strict):
    host: str = Field(min_length=3, max_length=120, pattern=r"^[A-Za-z0-9.\-]+$")
    port: int = Field(default=587, ge=1, le=65535)
    username: str = Field(default="", max_length=200)
    password: str | None = Field(default=None, max_length=300)
    from_addr: str = Field(min_length=5, max_length=200, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    security: Literal["starttls", "ssl"] = "starttls"


@router.put("/email/settings")
def email_settings(body: EmailIn, user: User = Depends(require("admin.settings")), db: Session = Depends(get_db)):
    cfg = A.email_settings(db)
    cfg.update(host=body.host, port=body.port, username=body.username, from_addr=body.from_addr, security=body.security)
    if body.password:
        cfg["password_enc"] = crypto.encrypt(body.password)
    settings_store.put_raw(db, A.EMAIL_KEY, cfg, by=user.display_name)
    ev.record(db, ev.ADMIN, "email_settings_changed", f"{user.display_name} set up email for automations", user, "setting", A.EMAIL_KEY)
    db.commit()
    return {"ok": True}


class EmailTest(Strict):
    to: str = Field(min_length=5, max_length=200, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@router.post("/email/test")
def email_test(body: EmailTest, user: User = Depends(require("admin.settings")), db: Session = Depends(get_db)):
    try:
        return {"result": A.send_email(db, body.to, "Walnut Grove Desk: test email", "Email from automations works.")}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(e) if isinstance(e, RuntimeError) else f"The mail server refused it ({type(e).__name__}). Check the settings.")
