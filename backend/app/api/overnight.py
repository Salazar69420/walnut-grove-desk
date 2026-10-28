"""Overnight list (every after-hours conversation) and the staff actions on it."""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import case, select, update
from sqlalchemy.orm import Session

from app.adapters.messaging import DryRunSms
from app.aftercare import service as svc
from app.aftercare import templates as T
from app.db import get_db
from app.models import AppointmentRequest, Callback, Conversation, EmergencyRouting, OpenSlot, User
from app.security.auth import require
from app.services import events as ev
from app.services import settings_store, timeutil

router = APIRouter(prefix="/api/overnight", tags=["overnight"])

_KIND_ORDER = case((Conversation.kind == "emergency", 0), (Conversation.kind == "booking", 1), else_=2)
_STATUS_ORDER = case((Conversation.status == "new", 0), else_=1)


def item(c: Conversation, now=None) -> dict[str, Any]:
    now = now or timeutil.now_utc()
    try:
        msgs = json.loads(c.messages_json or "[]")
    except ValueError:
        msgs = []
    age = (now - c.created_at).total_seconds()
    at = timeutil.clock_text(c.created_at) if age < 36 * 3600 else timeutil.display_time(c.created_at, now)
    return {
        "id": c.id, "client_label": c.client_label, "pet_label": c.pet_label,
        "channel": "Call" if c.channel == "call" else "Text", "at_display": at,
        "created_at": timeutil.iso(c.created_at), "kind": c.kind, "status": c.status,
        "preferred_slot": c.preferred_slot, "summary": c.summary, "messages": msgs,
        "is_test": c.is_test, "source": c.source,
    }


def summary(db: Session) -> dict[str, int]:
    rows = db.scalars(select(Conversation)).all()
    return {
        "total": len(rows),
        "booking": sum(1 for r in rows if r.kind == "booking"),
        "callback": sum(1 for r in rows if r.kind == "callback"),
        "emergency": sum(1 for r in rows if r.kind == "emergency"),
        "pending": sum(1 for r in rows if r.status == "new"),
        "emergency_pending": sum(1 for r in rows if r.kind == "emergency" and r.status == "new"),
    }


def ordered(db: Session) -> list[Conversation]:
    return list(db.scalars(select(Conversation).order_by(_STATUS_ORDER, _KIND_ORDER, Conversation.created_at.desc(), Conversation.id.desc())))


@router.get("")
def list_overnight(user: User = Depends(require("overnight.view")), db: Session = Depends(get_db)):
    now = timeutil.now_utc()
    return {"items": [item(c, now) for c in ordered(db)], "summary": summary(db)}


def _get(db: Session, cid: int, kind: str) -> Conversation:
    c = db.get(Conversation, cid)
    if c is None:
        raise HTTPException(status_code=404, detail="No such conversation")
    if c.kind != kind:
        raise HTTPException(status_code=400, detail=f"That is not a{'n' if kind[0] in 'aeiou' else ''} {kind} item")
    return c


def _move(db: Session, c: Conversation, from_status: str, to_status: str) -> None:
    res = db.execute(update(Conversation).where(Conversation.id == c.id, Conversation.status == from_status).values(status=to_status))
    if res.rowcount == 0:
        db.refresh(c)
        raise HTTPException(status_code=409, detail=f"Already {_past(c.status)}")
    db.refresh(c)


def _past(status: str) -> str:
    return {"new": "waiting", "confirmed": "confirmed", "entered": "entered in ImproMed", "called": "moved to callbacks",
            "handled": "handled"}.get(status, status)


@router.post("/{cid}/confirm")
def confirm(cid: int, user: User = Depends(require("overnight.manage")), db: Session = Depends(get_db)):
    c = _get(db, cid, "booking")
    _move(db, c, "new", "confirmed")
    req = db.scalar(select(AppointmentRequest).where(AppointmentRequest.conversation_id == c.id))
    slot_text = c.preferred_slot or "a time to be arranged"
    if req is not None:
        req.status = "confirmed"
        if req.slot_start is not None:
            slot = db.scalar(select(OpenSlot).where(OpenSlot.start == req.slot_start))
            if slot is not None:
                slot.taken = True
    clinic = settings_store.get(db, "clinic_name")
    DryRunSms(db, "conversation", c.id, user.display_name).send(
        c.client_label, str(T.confirmation_sms(clinic, slot_text, c.pet_label.split(" (")[0] or None)))
    ev.record(db, ev.AFTER_HOURS, "booking_confirmed", f"{user.display_name} confirmed {c.client_label} for {slot_text}",
              user, "conversation", c.id)
    db.commit()
    return {"item": item(c)}


@router.post("/{cid}/entered")
def entered(cid: int, user: User = Depends(require("overnight.manage")), db: Session = Depends(get_db)):
    c = _get(db, cid, "booking")
    _move(db, c, "confirmed", "entered")
    req = db.scalar(select(AppointmentRequest).where(AppointmentRequest.conversation_id == c.id))
    if req is not None:
        req.status = "entered"
    ev.record(db, ev.AFTER_HOURS, "booking_entered", f"{c.client_label}: booking entered in ImproMed", user, "conversation", c.id)
    db.commit()
    return {"item": item(c)}


@router.post("/{cid}/to-callback")
def to_callback(cid: int, user: User = Depends(require("overnight.manage")), db: Session = Depends(get_db)):
    c = _get(db, cid, "booking")
    _move(db, c, "new", "called")
    req = db.scalar(select(AppointmentRequest).where(AppointmentRequest.conversation_id == c.id))
    if req is not None:
        req.status = "called"
    cb = Callback(
        client_label=c.client_label, pet_label=c.pet_label, what=f"Wants to book. {c.summary}", kind="Booking",
        via="Call" if c.channel == "call" else "Text", received_at=c.created_at, priority="normal", status="new",
        source="overnight", conversation_id=c.id,
    )
    db.add(cb)
    db.flush()
    ev.record(db, ev.CALLBACK_LIST, "booking_to_callback", f"{user.display_name} moved {c.client_label} to callbacks",
              user, "callback", cb.id)
    ev.queue_publish(db, "overnight", c.id, None)
    db.commit()
    return {"item": item(c)}


@router.post("/{cid}/followed-up")
def followed_up(cid: int, user: User = Depends(require("overnight.followup")), db: Session = Depends(get_db)):
    c = _get(db, cid, "emergency")
    _move(db, c, "new", "handled")
    er = db.scalar(select(EmergencyRouting).where(EmergencyRouting.conversation_id == c.id))
    if er is not None:
        er.followed_up_by = user.display_name
        er.followed_up_at = timeutil.now_utc()
    ev.record(db, ev.AFTER_HOURS, "emergency_followed_up",
              f"{user.display_name} followed up with {c.client_label} after an emergency routing", user, "conversation", c.id)
    db.commit()
    return {"item": item(c)}


@router.post("/{cid}/ack")
def ack(cid: int, user: User = Depends(require("overnight.manage")), db: Session = Depends(get_db)):
    c = _get(db, cid, "callback")
    _move(db, c, "new", "handled")
    ev.record(db, ev.AFTER_HOURS, "callback_seen", f"{user.display_name} saw the overnight callback from {c.client_label}",
              user, "conversation", c.id)
    db.commit()
    return {"item": item(c)}
