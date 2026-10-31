"""Operations > Phone: the call log, who handled each call (a person or the assistant), and call-centre numbers."""
from __future__ import annotations

import re
from collections import Counter
from datetime import date, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.schemas import Strict
from app.db import get_db
from app.models import Contact, PhoneCall, User
from app.security.auth import require
from app.security.permissions import has_perm
from app.services import crm
from app.services import events as ev
from app.services import timeutil
from app.services.csvutil import to_csv

router = APIRouter(prefix="/api", tags=["phone"])

RESULT_LABELS = {
    "answered": "Answered", "booked": "Booking made", "callback_task": "Callback task raised", "voicemail": "Voicemail",
    "missed": "Missed", "emergency_routed": "Emergency routed", "info_given": "Information given",
}
Direction = Literal["inbound", "outbound", "unknown"]
Handled = Literal["human", "ai", "none"]
Result = Literal["answered", "booked", "callback_task", "voicemail", "missed", "emergency_routed", "info_given"]
CallType = Literal["inquiry", "patient", "unknown"]


class CallFilters:
    def __init__(
        self,
        from_: date | None = Query(default=None, alias="from"),
        to: date | None = None,
        direction: Direction | None = None,
        handled_by: Handled | None = None,
        result: Result | None = None,
        call_type: CallType | None = None,
        contact_id: int | None = None,
        q: str | None = Query(default=None, max_length=80),
    ):
        self.frm, self.to, self.direction, self.handled_by = from_, to, direction, handled_by
        self.result, self.call_type, self.contact_id, self.q = result, call_type, contact_id, q


def _statement(f: CallFilters):
    stmt = select(PhoneCall)
    if f.frm:
        stmt = stmt.where(PhoneCall.started_at >= crm.local_day_start(f.frm))
    if f.to:
        stmt = stmt.where(PhoneCall.started_at < crm.local_day_start(f.to + timedelta(days=1)))
    if f.direction:
        stmt = stmt.where(PhoneCall.direction == f.direction)
    if f.handled_by:
        stmt = stmt.where(PhoneCall.handled_by == f.handled_by)
    if f.result:
        stmt = stmt.where(PhoneCall.result == f.result)
    if f.call_type:
        stmt = stmt.where(PhoneCall.call_type == f.call_type)
    if f.contact_id is not None:
        stmt = stmt.where(PhoneCall.contact_id == f.contact_id)
    if f.q and f.q.strip():
        q = f.q.strip()
        like = crm.like_pattern(q)
        conds = [PhoneCall.short_note.like(like, escape="\\"), PhoneCall.agent_label.like(like, escape="\\"),
                 PhoneCall.contact_id.in_(select(Contact.id).where(Contact.display_name.like(like, escape="\\")))]
        digits = re.sub(r"\D", "", q)
        if len(digits) >= 3:
            conds.append(PhoneCall.phone.like("%" + digits + "%"))
        stmt = stmt.where(or_(*conds))
    return stmt


def call_dicts(db: Session, rows: list[PhoneCall]) -> list[dict[str, Any]]:
    cids = {r.contact_id for r in rows if r.contact_id}
    contacts = {c.id: c for c in db.scalars(select(Contact).where(Contact.id.in_(cids))).all()} if cids else {}
    out = []
    for r in rows:
        c = contacts.get(r.contact_id)
        out.append({
            "id": r.id, "started_at": timeutil.iso(r.started_at), "direction": r.direction, "handled_by": r.handled_by,
            "agent_label": r.agent_label, "contact": {"id": c.id, "display_name": c.display_name, "kind": c.kind} if c else None,
            "phone_display": crm.phone_display(r.phone) if r.phone else "", "duration_seconds": r.duration_seconds,
            "result": r.result, "result_label": RESULT_LABELS.get(r.result, r.result), "call_type": r.call_type,
            "short_note": r.short_note, "has_transcript": bool(r.transcript), "has_recording": bool(r.recording_id), "source": r.source,
        })
    return out


@router.get("/phone/calls")
def list_calls(
    f: CallFilters = Depends(),
    page: int = Query(default=1, ge=1, le=100000),
    per_page: int = Query(default=50, ge=10, le=100),
    user: User = Depends(require("phone.view")),
    db: Session = Depends(get_db),
):
    base = _statement(f)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = db.scalars(base.order_by(PhoneCall.started_at.desc(), PhoneCall.id.desc()).limit(per_page).offset((page - 1) * per_page)).all()
    return {"items": call_dicts(db, list(rows)), "total": total, "page": page, "per_page": per_page,
            "can_transcript": has_perm(user, "phone.transcript"), "can_listen": has_perm(user, "phone.listen")}


@router.get("/phone/summary")
def summary(days: int = Query(default=7, ge=1, le=90), user: User = Depends(require("phone.view")), db: Session = Depends(get_db)):
    today = timeutil.today_local()
    start = crm.local_day_start(today - timedelta(days=days - 1))
    rows = db.scalars(select(PhoneCall).where(PhoneCall.started_at >= start)).all()
    inbound = [r for r in rows if r.direction == "inbound"]
    by_hour = [0] * 24
    by_day: dict[date, Counter] = {today - timedelta(days=i): Counter() for i in range(days)}
    for r in rows:
        loc = timeutil.local(r.started_at)
        by_hour[loc.hour] += 1
        if loc.date() in by_day:
            by_day[loc.date()][r.handled_by] += 1
    handled = [r for r in rows if r.handled_by != "none"]
    ai = sum(1 for r in handled if r.handled_by == "ai")
    talk = [r.duration_seconds for r in handled if r.duration_seconds > 0]
    return {
        "days": days, "total": len(rows), "inbound": len(inbound), "outbound": sum(1 for r in rows if r.direction == "outbound"),
        "missed": sum(1 for r in inbound if r.handled_by == "none"),
        "ai_handled": ai, "human_handled": len(handled) - ai,
        "ai_share_pct": round(100 * ai / len(handled)) if handled else 0,
        "avg_seconds": round(sum(talk) / len(talk)) if talk else 0,
        "unmatched": sum(1 for r in rows if r.contact_id is None and r.handled_by != "none"),
        "by_hour": by_hour,
        "by_day": [{"date": d.isoformat(), "human": c["human"], "ai": c["ai"], "none": c["none"]} for d, c in sorted(by_day.items())],
        "by_result": dict(Counter(r.result for r in rows)),
        "results": [[k, v] for k, v in RESULT_LABELS.items()],
    }


@router.get("/phone/calls/{call_id}")
def get_call(call_id: int, user: User = Depends(require("phone.view")), db: Session = Depends(get_db)):
    r = db.get(PhoneCall, call_id)
    if r is None:
        raise HTTPException(status_code=404, detail="No such call")
    item = call_dicts(db, [r])[0]
    item["transcript"] = None
    if r.transcript and has_perm(user, "phone.transcript"):
        item["transcript"] = r.transcript
        crm.log_access(db, user, r.contact_id, "view", detail="phone transcript")
        ev.record(db, ev.PHONE, "transcript_opened", f"{user.display_name} opened a call transcript", user, "phone_call", r.id, publish=False)
        db.commit()
    return {"item": item}


class CallPatch(Strict):
    short_note: str | None = Field(default=None, max_length=240)
    contact_id: int | None = None
    call_type: CallType | None = None


@router.patch("/phone/calls/{call_id}")
def patch_call(call_id: int, body: CallPatch, user: User = Depends(require("contacts.edit")), db: Session = Depends(get_db)):
    r = db.get(PhoneCall, call_id)
    if r is None:
        raise HTTPException(status_code=404, detail="No such call")
    ch = body.model_dump(exclude_unset=True)
    if "contact_id" in ch and ch["contact_id"] is not None:
        c = db.get(Contact, ch["contact_id"])
        if c is None or c.erased_at is not None:
            raise HTTPException(status_code=400, detail="That contact was not found.")
        r.call_type = "patient" if c.kind == "patient" else "inquiry"
    if "contact_id" in ch:
        r.contact_id = ch["contact_id"]
    if "short_note" in ch and ch["short_note"] is not None:
        r.short_note = ch["short_note"]
    if "call_type" in ch and ch["call_type"] is not None:
        r.call_type = ch["call_type"]
    ev.record(db, ev.PHONE, "call_updated", f"{user.display_name} updated a call note", user, "phone_call", r.id)
    db.commit()
    return {"item": call_dicts(db, [r])[0]}


@router.get("/export/phone.csv")
def export_calls(f: CallFilters = Depends(), user: User = Depends(require("phone.export")), db: Session = Depends(get_db)):
    rows = db.scalars(_statement(f).order_by(PhoneCall.started_at.desc()).limit(5000)).all()
    items = call_dicts(db, list(rows))
    header = ["when", "direction", "handled_by", "agent", "contact", "result", "duration_seconds", "call_type", "note"]
    data = [[i["started_at"], i["direction"], i["handled_by"], i["agent_label"], (i["contact"] or {}).get("display_name", ""),
             i["result_label"], i["duration_seconds"], i["call_type"], i["short_note"]] for i in items]
    crm.log_access(db, user, None, "export", detail=f"phone {len(data)} calls")
    db.commit()
    return Response(to_csv(header, data), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="phone-calls.csv"', "Cache-Control": "no-store"})
