from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import db as app_db
from app.api import serializers
from app.db import get_db
from app.models import Event, User
from app.security.auth import lookup_session, require
from app.seed.reference_data import TOOLS
from app.services import events as ev
from app.services.broadcaster import broadcaster, sse_format

router = APIRouter(prefix="/api", tags=["feed"])

PING_SECONDS = 15


@router.get("/events")
def list_events(
    since: int = Query(default=0, ge=0),
    limit: int = Query(default=12, ge=1, le=200),
    from_: str | None = Query(default=None, alias="from", max_length=40),
    to: str | None = Query(default=None, max_length=40),
    contact_id: int | None = None,
    kind: str | None = Query(default=None, max_length=20),
    user: User = Depends(require("tools.view")),
    db: Session = Depends(get_db),
):
    """Feed events (Admin and Sign-in are audit-only), newest first. `since` returns only ids above it.

    With `from`, `to`, `contact_id` or `kind` the same path returns calendar events (docs/CONTRACT_CRM_P1.md).
    """
    if from_ or to or contact_id is not None or kind:
        from app.api.crm_misc import calendar_items
        from app.security.permissions import has_perm

        if not has_perm(user, "calendar.view"):
            raise HTTPException(status_code=403, detail="forbidden")
        return calendar_items(db, from_, to, contact_id, kind)
    stmt = select(Event).where(Event.tool.in_(ev.FEED_TOOLS))
    if since:
        stmt = stmt.where(Event.id > since)
    rows = db.scalars(stmt.order_by(Event.id.desc()).limit(limit)).all()
    last_id = db.scalar(select(func.max(Event.id))) or 0
    return {"items": [serializers.event(e) for e in rows], "last_id": last_id}


@router.get("/tools")
def list_tools(user: User = Depends(require("tools.view")), db: Session = Depends(get_db)):
    counts = dict(db.execute(select(Event.tool, func.count()).where(Event.tool.in_(ev.FEED_TOOLS)).group_by(Event.tool)).all())
    items = [
        {"name": t["n"], "who": t["who"], "writes": t["w"], "event_count": int(counts.get(t["n"], 0))}
        for t in TOOLS
    ]
    return {"items": items}


@router.get("/stream")
async def stream(request: Request):
    """Server-sent events. Signed-in users only. Sends `event: change` per change and a ping every 15 s."""
    # Authenticate with a short-lived session so no transaction stays open while streaming.
    def _auth() -> str:
        with app_db.get_sessionmaker()() as db:
            found = lookup_session(db, request)
            if found is None:
                return "none"
            return "password" if found[0].must_change_password else "ok"

    state = await asyncio.to_thread(_auth)
    if state == "none":
        raise HTTPException(status_code=401, detail="not_authenticated")
    if state == "password":
        raise HTTPException(status_code=403, detail="password_change_required")

    broadcaster.bind_loop(asyncio.get_running_loop())
    queue = broadcaster.subscribe()

    async def gen():
        try:
            yield "retry: 3000\n: connected\n\n"
            while True:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=PING_SECONDS)
                    yield sse_format(msg)
                except asyncio.TimeoutError:
                    if await request.is_disconnected():
                        break
                    yield ": ping\n\n"
        finally:
            broadcaster.unsubscribe(queue)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )
