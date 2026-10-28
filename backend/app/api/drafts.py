from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.adapters.sms import queue_sms
from app.api import serializers
from app.api.schemas import DraftPatchBody
from app.db import get_db
from app.models import Draft, User
from app.security.auth import require
from app.services import events, timeutil

router = APIRouter(prefix="/api/drafts", tags=["drafts"])


@router.get("")
def list_drafts(user: User = Depends(require("drafts.view")), db: Session = Depends(get_db)):
    rows = db.scalars(select(Draft).order_by(Draft.id)).all()
    return {"items": [serializers.draft(d) for d in rows]}


def _get(db: Session, draft_id: int) -> Draft:
    d = db.get(Draft, draft_id)
    if d is None:
        raise HTTPException(status_code=404, detail="No such draft")
    return d


@router.patch("/{draft_id}")
def edit_draft(draft_id: int, body: DraftPatchBody, user: User = Depends(require("drafts.approve")), db: Session = Depends(get_db)):
    d = _get(db, draft_id)
    if d.status != "pending":
        raise HTTPException(status_code=409, detail=f"Already approved by {d.approved_by}")
    d.body = body.body
    d.edited_by = user.display_name
    events.record(
        db, events.RECALL_SENDER, "draft_edited", f"{user.display_name} edited a draft to {d.to_label}",
        user, "draft", d.id,
    )
    db.commit()
    return {"item": serializers.draft(d)}


@router.post("/{draft_id}/approve")
def approve_draft(draft_id: int, user: User = Depends(require("drafts.approve")), db: Session = Depends(get_db)):
    d = _get(db, draft_id)
    now = timeutil.now_utc()
    res = db.execute(
        update(Draft)
        .where(Draft.id == draft_id, Draft.status == "pending")
        .values(status="sent", approved_by=user.display_name, approved_at=now)
    )
    if res.rowcount == 0:
        db.refresh(d)
        raise HTTPException(status_code=409, detail=f"Already approved by {d.approved_by}")
    db.refresh(d)
    queue_sms(db, d.to_label, d.body, source_type="draft", source_id=d.id, created_by=user.display_name)
    events.record(
        db, events.RECALL_SENDER, "draft_approved", f"{user.display_name} approved: {d.kind} to {d.to_label}",
        user, "draft", d.id, {"outbox": "would_send"},
    )
    db.commit()
    return {"item": serializers.draft(d)}
