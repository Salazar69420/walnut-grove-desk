"""The shared record. Every state change goes through record()."""
from __future__ import annotations

import contextvars
import json
from datetime import datetime
from typing import Any

from sqlalchemy import event as sa_event
from sqlalchemy.orm import Session

from app.models import Event
from app.services import timeutil
from app.services.broadcaster import broadcaster

# Tool names (exact, from the contract).
CALL_LOGGER = "Call logger"
CALLBACK_LIST = "Callback list"
RESULTS_TRACKER = "Results tracker"
RECALL_SENDER = "Recall sender"
AFTER_HOURS = "After-hours assistant"
STAFF_ROSTER = "Staff roster"
IMPROMED_SYNC = "ImproMed sync"
CONTACTS = "Contacts"
CONVERSATIONS = "Conversations"
TASKS = "Tasks"
KNOWLEDGE = "Knowledge"
PHONE = "Phone"
CAMPAIGNS = "Campaigns"
ADMIN = "Admin"
SIGN_IN = "Sign-in"

FEED_TOOLS = [CALL_LOGGER, CALLBACK_LIST, RESULTS_TRACKER, RECALL_SENDER, AFTER_HOURS, STAFF_ROSTER, IMPROMED_SYNC, CONTACTS, CONVERSATIONS, TASKS, KNOWLEDGE, PHONE, CAMPAIGNS]
AUDIT_ONLY_TOOLS = [ADMIN, SIGN_IN]
ALL_TOOLS = FEED_TOOLS + AUDIT_ONLY_TOOLS

# SSE entity names the frontend listens for.
ENTITIES = {
    "callbacks", "results", "drafts", "shifts", "suggestions", "overnight",
    "events", "settings", "hospitals", "visit_types", "users", "analytics",
    "contacts", "messages", "tasks", "notifications", "events_cal",
    "knowledge", "fees", "phone", "campaigns",
}
_ENTITY_OF_TYPE = {
    "callback": "callbacks",
    "result": "results",
    "draft": "drafts",
    "shift": "shifts",
    "suggestion": "suggestions",
    "conversation": "overnight",
    "overnight": "overnight",
    "hospital": "hospitals",
    "visit_type": "visit_types",
    "user": "users",
    "setting": "settings",
    "analytics": "analytics",
    "contact": "contacts",
    "message": "messages",
    "note": "messages",
    "task": "tasks",
    "notification": "notifications",
    "calendar_event": "events_cal",
    "knowledge": "knowledge",
    "fee": "fees",
    "phone_call": "phone",
    "campaign": "campaigns",
}

# Set by the request middleware so record() can stamp the client address.
current_ip: contextvars.ContextVar[str] = contextvars.ContextVar("current_ip", default="")


def entity_for(entity_type: str) -> str | None:
    return _ENTITY_OF_TYPE.get(entity_type)


def actor_parts(actor: Any) -> tuple[int | None, str]:
    """Accept a User, a (id, name) tuple, a plain name string, or None."""
    if actor is None:
        return None, "System"
    if isinstance(actor, str):
        return None, actor
    if isinstance(actor, tuple):
        return actor[0], actor[1]
    return getattr(actor, "id", None), getattr(actor, "display_name", "") or getattr(actor, "username", "")


def queue_publish(db: Session, entity: str, entity_id: int | str | None, event_id: int | None) -> None:
    """Remember an SSE message; it goes out only after the transaction commits."""
    pending: list[dict[str, Any]] = db.info.setdefault("pending_publish", [])
    pending.append({"entity": entity, "id": entity_id, "event_id": event_id})


def record(
    db: Session,
    tool: str,
    action: str,
    text: str,
    actor: Any = None,
    entity_type: str = "",
    entity_id: int | str | None = None,
    data: dict[str, Any] | None = None,
    *,
    ts: datetime | None = None,
    ip: str | None = None,
    publish: bool = True,
) -> Event:
    """Insert an event row and queue SSE messages for after the commit."""
    if tool not in ALL_TOOLS:
        raise ValueError(f"unknown tool name: {tool!r}")
    actor_id, actor_name = actor_parts(actor)
    ev = Event(
        ts=ts or timeutil.now_utc(),
        tool=tool,
        actor_id=actor_id,
        actor_name=actor_name,
        action=action,
        entity_type=entity_type,
        entity_id="" if entity_id is None else str(entity_id),
        text=text,
        data_json=json.dumps(data or {}, separators=(",", ":"), sort_keys=True),
        ip=current_ip.get() if ip is None else ip,
    )
    db.add(ev)
    db.flush()
    if publish:
        ent = entity_for(entity_type)
        if ent:
            queue_publish(db, ent, entity_id, ev.id)
        queue_publish(db, "events", ev.id, ev.id)
    return ev


@sa_event.listens_for(Session, "after_commit")
def _publish_after_commit(session: Session) -> None:
    pending = session.info.pop("pending_publish", None)
    if pending:
        for msg in pending:
            broadcaster.publish(msg)


@sa_event.listens_for(Session, "after_rollback")
def _drop_on_rollback(session: Session) -> None:
    session.info.pop("pending_publish", None)
