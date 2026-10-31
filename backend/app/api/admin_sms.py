"""Texting mode (admin) and the delivery status of one text."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy.orm import Session

from app.api import admin_telus
from app.api.schemas import Strict
from app.db import get_db
from app.models import Message, User
from app.security.auth import require
from app.services import events as ev
from app.services import autoreply, settings_store, sms_mode

router = APIRouter(prefix="/api", tags=["sms"])

LIVE_PHRASE = "SEND REAL TEXTS"


class ModeIn(Strict):
    mode: Literal["practice", "test_numbers_only", "live"]
    test_numbers: list[str] = Field(default_factory=list, max_length=20)
    confirm: str | None = Field(default=None, max_length=40)
    auto_reply: bool | None = None


def _view(db: Session) -> dict[str, Any]:
    m = sms_mode.get(db)
    return {**m, "auto_reply": autoreply.is_on(db), "bridge_ok": admin_telus.bridge_ok(), "sender_ready": sms_mode.sender_ready(), "live_phrase": LIVE_PHRASE}


@router.get("/admin/sms")
def get_mode(user: User = Depends(require("admin.telus")), db: Session = Depends(get_db)):
    return _view(db)


@router.put("/admin/sms")
def put_mode(body: ModeIn, user: User = Depends(require("admin.telus")), db: Session = Depends(get_db)):
    if body.mode != "practice" and not sms_mode.sender_ready():
        raise HTTPException(status_code=409, detail="The sending helper is not set up on this server yet.")
    if body.mode == "live" and (body.confirm or "").strip() != LIVE_PHRASE:
        raise HTTPException(status_code=400, detail=f'To turn on live texting, type {LIVE_PHRASE} to confirm.')
    sms_mode.put(db, body.mode, body.test_numbers, user.display_name)
    if body.auto_reply is not None:
        settings_store.put_raw(db, autoreply.KEY, bool(body.auto_reply), by=user.display_name)
    ev.record(db, ev.ADMIN, "sms_mode_changed", f"{user.display_name} set texting to {body.mode.replace('_', ' ')}", user, "setting", "sms_mode", {"mode": body.mode})
    db.commit()
    return _view(db)


@router.get("/messages/{message_id}/delivery")
def delivery(message_id: int, user: User = Depends(require("messages.view")), db: Session = Depends(get_db)):
    m = db.get(Message, message_id)
    if m is None:
        raise HTTPException(status_code=404, detail="No such message")
    if sms_mode.refresh(db, [m]):
        db.commit()
    return {"state": m.delivery_state, "code": m.delivery_code, "text": sms_mode.CODE_TEXT.get(m.delivery_code or "", None)}


# ---------------------------------------------------------------- corrections: teach the assistant once, keep it in the knowledge base
class CorrectIn(Strict):
    answer: str = Field(min_length=2, max_length=2000)
    title: str | None = Field(default=None, min_length=2, max_length=200)
    category: Literal["clinic_info", "fees", "patient_knowledge", "patient_communication", "inquiry_communication", "specials"] = "patient_communication"


@router.post("/messages/{message_id}/correct")
def correct(message_id: int, body: CorrectIn, user: User = Depends(require("knowledge.suggest")), db: Session = Depends(get_db)):
    """A person says what the assistant should have answered. It becomes knowledge: approved at once when an owner says it, else pending."""
    from sqlalchemy import select
    from app.security.permissions import has_perm
    from app.services import knowledge as kn
    m = db.get(Message, message_id)
    if m is None or m.direction != "out" or m.sender_kind != "ai":
        raise HTTPException(status_code=400, detail="Only a reply from the assistant can be corrected.")
    q = db.scalar(select(Message).where(Message.contact_id == m.contact_id, Message.direction == "in", Message.channel.in_(("sms", "email")),
                                        Message.id < m.id).order_by(Message.id.desc()).limit(1))  # the text it was answering came in just before it
    question = (q.body if q else "").strip()
    title = body.title or (question[:80] if question else "Correction")
    replaced = kn.find_approved(db, body.category, title)
    e = kn.propose(db, category=body.category, title=title, body=body.answer, proposed_by="human", user=user, source_kind="conversation",
                   source_ref="Correction to an assistant reply", source_quote=question[:300], supersedes_id=replaced.id if replaced else None)
    if has_perm(user, "knowledge.decide"):
        kn.approve(db, e, user)
    db.commit()
    return {"item": kn.entry_dicts(db, [e])[0]}
