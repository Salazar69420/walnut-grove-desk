"""Suggest a reply (the assistant drafts, a person sends). See app/assistant/replies.py for the rules."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy.orm import Session

from app.api.schemas import Strict
from app.assistant import replies
from app.db import get_db
from app.models import Contact, User
from app.security.auth import require
from app.services import events as ev

router = APIRouter(prefix="/api", tags=["assistant"])


class SuggestIn(Strict):
    # Optional: what the client said (for example on the phone). Without it, the latest text or email from the client is used.
    text: str | None = Field(default=None, min_length=1, max_length=1500)


@router.post("/contacts/{contact_id}/suggest-reply")
def suggest_reply(contact_id: int, body: SuggestIn, user: User = Depends(require("messages.send")), db: Session = Depends(get_db)) -> dict[str, Any]:
    c = db.get(Contact, contact_id)
    if c is None or c.erased_at is not None:
        raise HTTPException(status_code=404, detail="No such contact")
    if not c.ai_enabled:
        raise HTTPException(status_code=409, detail="The AI assistant is off for this person. Turn it on in the contact panel to get suggestions.")
    text = (body.text or replies.latest_client_text(db, c) or "").strip()
    if not text:
        raise HTTPException(status_code=409, detail="There is no message from this person to answer yet. Type what they said to get a suggestion.")
    out = replies.suggest(db, c, text)
    ev.record(db, ev.CONVERSATIONS, "reply_suggested", f"{user.display_name} asked the assistant for a reply ({out['kind']})", user, "contact", c.id, {"kind": out["kind"]})
    db.commit()
    return out
