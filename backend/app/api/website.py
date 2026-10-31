"""Website: the clinic site's content (read from the live site) and the SEO agent's audits."""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import Strict
from app.db import get_db
from app.models import User, WebsiteAudit
from app.security.auth import require
from app.security.permissions import has_perm
from app.services import settings_store, timeutil, website
from app.services import events as ev

router = APIRouter(prefix="/api/website", tags=["website"])


def audit_view(a: WebsiteAudit | None, full: bool = False) -> dict[str, Any] | None:
    if a is None:
        return None
    out: dict[str, Any] = {"id": a.id, "status": a.status, "base_url": a.base_url, "started_at": timeutil.iso(a.started_at),
                           "finished_at": timeutil.iso(a.finished_at) if a.finished_at else None, "started_by": a.started_by,
                           "summary": json.loads(a.summary_json or "{}"), "error": a.error, "has_plan": bool(a.ai_plan)}
    if full:
        pages = json.loads(a.pages_json or "[]")
        out["issues"] = json.loads(a.issues_json or "[]")
        out["pages"] = [{k: p.get(k) for k in ("url", "status", "ms", "bytes", "title", "description", "h1", "words", "schema", "canonical", "imgs_no_alt")} for p in pages]
        out["ai_plan"] = a.ai_plan
    return out


@router.get("")
def overview(user: User = Depends(require("website.view")), db: Session = Depends(get_db)):
    latest = db.scalar(select(WebsiteAudit).order_by(WebsiteAudit.id.desc()).limit(1))
    done = db.scalar(select(WebsiteAudit).where(WebsiteAudit.status == "done").order_by(WebsiteAudit.id.desc()).limit(1))
    history = db.scalars(select(WebsiteAudit).where(WebsiteAudit.status == "done").order_by(WebsiteAudit.id.desc()).limit(10)).all()
    return {"settings": website.get_settings(db), "running": latest is not None and latest.status == "running", "latest": audit_view(done, full=True),
            "history": [{"id": h.id, "finished_at": timeutil.iso(h.finished_at), "score": json.loads(h.summary_json or "{}").get("score")} for h in history],
            "can_run": has_perm(user, "website.run")}


@router.get("/pages")
def page_content(url: str, user: User = Depends(require("website.view")), db: Session = Depends(get_db)):
    done = db.scalar(select(WebsiteAudit).where(WebsiteAudit.status == "done").order_by(WebsiteAudit.id.desc()).limit(1))
    if done is None:
        raise HTTPException(status_code=404, detail="Run the first audit to read the site.")
    page = next((p for p in json.loads(done.pages_json) if p["url"] == url), None)
    if page is None:
        raise HTTPException(status_code=404, detail="That page was not found in the last audit.")
    return {"item": page}


@router.post("/audit", status_code=202)
def run_audit(user: User = Depends(require("website.run")), db: Session = Depends(get_db)):
    if db.scalar(select(WebsiteAudit.id).where(WebsiteAudit.status == "running").limit(1)):
        raise HTTPException(status_code=409, detail="An audit is already running.")
    a = website.start(db, user.display_name)
    return {"item": audit_view(a)}


@router.post("/audits/{aid}/plan")
def plan(aid: int, user: User = Depends(require("website.run")), db: Session = Depends(get_db)):
    from app.assistant import ai
    a = db.get(WebsiteAudit, aid)
    if a is None or a.status != "done":
        raise HTTPException(status_code=404, detail="No finished audit with that number.")
    try:
        text = website.ai_plan(db, a)
    except ai.AiFailed as e:
        raise HTTPException(status_code=502, detail=f"The AI could not write the plan just now ({e}).")
    a = db.get(WebsiteAudit, aid)
    a.ai_plan = text
    ev.record(db, ev.ADMIN, "website_plan", f"{user.display_name} asked the SEO agent for a fix plan", user, "setting", website.KEY)
    db.commit()
    return {"ai_plan": text}


@router.post("/audits/{aid}/knowledge")
def knowledge(aid: int, user: User = Depends(require("website.run")), db: Session = Depends(get_db)):
    from app.assistant import ai
    a = db.get(WebsiteAudit, aid)
    if a is None or a.status != "done":
        raise HTTPException(status_code=404, detail="No finished audit with that number.")
    try:
        n = website.ai_knowledge(db, a, user)
    except (ai.AiFailed, ValueError) as e:
        raise HTTPException(status_code=502, detail=f"The AI could not read the site just now ({type(e).__name__}).")
    return {"added": n}


class SettingsIn(Strict):
    url: str = Field(min_length=8, max_length=200, pattern=r"^https://[A-Za-z0-9.\-]+(/.*)?$")
    canonical: str = Field(default="", max_length=200, pattern=r"^(https://[A-Za-z0-9.\-]+/?)?$")


@router.put("/settings")
def put_settings(body: SettingsIn, user: User = Depends(require("admin.settings")), db: Session = Depends(get_db)):
    cfg = website.get_settings(db)
    cfg.update(url=body.url.rstrip("/"), canonical=body.canonical.rstrip("/"))
    settings_store.put_raw(db, website.KEY, cfg, by=user.display_name)
    db.commit()
    return {"settings": cfg}
