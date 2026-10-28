from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, or_, select, update
from sqlalchemy.orm import Session

from app.api import serializers
from app.db import get_db
from app.models import Callback, User
from app.security.auth import require
from app.services import events, timeutil

router = APIRouter(prefix="/api/callbacks", tags=["callbacks"])

_STATUS_ORDER = case((Callback.status == "new", 0), (Callback.status == "claimed", 1), else_=2)
_PRIO_ORDER = case((Callback.priority == "high", 0), else_=1)


def _label(c: Callback) -> str:
    return c.client_label + (f" ({c.pet_label})" if c.pet_label else "")


def list_query(filter_: str, user: User):
    stmt = select(Callback)
    if filter_ == "mine":
        stmt = stmt.where(or_(Callback.owner_id == user.id, (Callback.owner_id.is_(None)) & (Callback.owner_name == user.display_name)))
    elif filter_ == "high":
        stmt = stmt.where(Callback.priority == "high", Callback.status != "done")
    # reference order: new, claimed, done; high priority first; then newest first
    return stmt.order_by(_STATUS_ORDER, _PRIO_ORDER, Callback.received_at.desc(), Callback.id.desc())


@router.get("")
def list_callbacks(
    filter: Literal["all", "mine", "high"] = Query(default="all"),
    user: User = Depends(require("callbacks.view")),
    db: Session = Depends(get_db),
):
    now = timeutil.now_utc()
    rows = db.scalars(list_query(filter, user)).all()
    return {"items": [serializers.callback(c, now) for c in rows]}


def _get_or_404(db: Session, cb_id: int) -> Callback:
    c = db.get(Callback, cb_id)
    if c is None:
        raise HTTPException(status_code=404, detail="No such callback")
    return c


@router.post("/{cb_id}/claim")
def claim(cb_id: int, user: User = Depends(require("callbacks.work")), db: Session = Depends(get_db)):
    c = _get_or_404(db, cb_id)
    res = db.execute(
        update(Callback)
        .where(Callback.id == cb_id, Callback.status == "new")
        .values(status="claimed", owner_id=user.id, owner_name=user.display_name)
    )
    if res.rowcount == 0:
        db.refresh(c)
        if c.status == "claimed" and c.owner_id == user.id:
            return {"item": serializers.callback(c)}  # already yours, nothing to do
        if c.status == "claimed":
            raise HTTPException(status_code=409, detail=f"Already taken by {c.owner_name}")
        raise HTTPException(status_code=409, detail="Already closed")
    db.refresh(c)
    events.record(
        db, events.CALLBACK_LIST, "callback_claimed", f"{user.display_name} claimed {_label(c)}",
        user, "callback", c.id, {"kind": c.kind},
    )
    db.commit()
    return {"item": serializers.callback(c)}


@router.post("/{cb_id}/done")
def done(cb_id: int, user: User = Depends(require("callbacks.work")), db: Session = Depends(get_db)):
    c = _get_or_404(db, cb_id)
    if c.status == "done":
        raise HTTPException(status_code=409, detail="Already closed")
    if c.status == "claimed" and c.owner_id not in (None, user.id) and user.role != "owner":
        raise HTTPException(status_code=409, detail=f"Already taken by {c.owner_name}")
    now = timeutil.now_utc()
    keep_owner = c.status == "claimed"
    res = db.execute(
        update(Callback)
        .where(Callback.id == cb_id, Callback.status == c.status)
        .values(
            status="done",
            closed_at=now,
            owner_id=c.owner_id if keep_owner else user.id,
            owner_name=c.owner_name if keep_owner else user.display_name,
        )
    )
    if res.rowcount == 0:
        db.refresh(c)
        raise HTTPException(status_code=409, detail="Already closed" if c.status == "done" else f"Already taken by {c.owner_name}")
    db.refresh(c)
    events.record(
        db, events.CALLBACK_LIST, "callback_done", f"{user.display_name} closed {c.client_label} ({c.kind.lower()})",
        user, "callback", c.id, {"kind": c.kind},
    )
    db.commit()
    return {"item": serializers.callback(c, now)}
