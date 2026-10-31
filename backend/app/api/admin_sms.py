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
from app.services import sms_mode

router = APIRouter(prefix="/api", tags=["sms"])

LIVE_PHRASE = "SEND REAL TEXTS"


class ModeIn(Strict):
    mode: Literal["practice", "test_numbers_only", "live"]
    test_numbers: list[str] = Field(default_factory=list, max_length=20)
    confirm: str | None = Field(default=None, max_length=40)


def _view(db: Session) -> dict[str, Any]:
    m = sms_mode.get(db)
    return {**m, "bridge_ok": admin_telus.bridge_ok(), "sender_ready": sms_mode.sender_ready(), "live_phrase": LIVE_PHRASE}


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
