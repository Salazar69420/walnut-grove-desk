"""Plain dict serializers for API responses (snake_case)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.models import Callback, Draft, Event, LabResult, Shift, User
from app.services import timeutil

STAGE_NAMES = ["Received", "Doctor reviewed", "Client told"]


def user_public(u: User) -> dict[str, Any]:
    return {
        "id": u.id,
        "username": u.username,
        "display_name": u.display_name,
        "title": u.title,
        "initials": u.initials,
        "role": u.role,
        "is_admin": u.is_admin,
        "must_change_password": u.must_change_password,
    }


def user_admin(u: User, now: datetime | None = None) -> dict[str, Any]:
    now = now or timeutil.now_utc()
    d = user_public(u)
    d.update(
        email=u.email,
        active=u.active,
        locked=bool(u.locked_until and u.locked_until > now),
        locked_until=timeutil.iso(u.locked_until),
        created_at=timeutil.iso(u.created_at),
        last_login_at=timeutil.iso(u.last_login_at),
    )
    return d


def callback(c: Callback, now: datetime | None = None) -> dict[str, Any]:
    return {
        "id": c.id,
        "client_label": c.client_label,
        "pet_label": c.pet_label,
        "what": c.what,
        "kind": c.kind,
        "via": c.via,
        "received_at": timeutil.iso(c.received_at),
        "at_display": timeutil.display_time(c.received_at, now),
        "priority": c.priority,
        "red_flag": c.red_flag,
        "owner_id": c.owner_id,
        "owner_name": c.owner_name,
        "status": c.status,
        "source": c.source,
        "conversation_id": c.conversation_id,
        "closed_at": timeutil.iso(c.closed_at),
    }


def lab_result(r: LabResult, now: datetime | None = None) -> dict[str, Any]:
    age = timeutil.age_days(r.received_at, now)
    return {
        "id": r.id,
        "pet_label": r.pet_label,
        "owner_label": r.owner_label,
        "test": r.test,
        "received_at": timeutil.iso(r.received_at),
        "age_days": age,
        "stage": r.stage,
        "stage_name": STAGE_NAMES[r.stage],
        "stage_names": STAGE_NAMES,
        "late": r.stage < 2 and age >= 2,
        "reviewed_by": r.reviewed_by,
        "told_by": r.told_by,
        "updated_at": timeutil.iso(r.updated_at),
    }


def draft(d: Draft) -> dict[str, Any]:
    return {
        "id": d.id,
        "kind": d.kind,
        "to_label": d.to_label,
        "body": d.body,
        "channel": d.channel,
        "status": d.status,
        "created_by_tool": d.created_by_tool,
        "edited_by": d.edited_by,
        "approved_by": d.approved_by,
        "approved_at": timeutil.iso(d.approved_at),
        "suggestion_id": d.suggestion_id,
    }


def shift(s: Shift) -> dict[str, Any]:
    return {
        "id": s.id,
        "weekday": s.weekday,
        "person_label": s.person_label,
        "user_id": s.user_id,
        "role": s.role,
        "start_min": s.start_min,
        "end_min": s.end_min,
        "start": timeutil.hhmm(s.start_min),
        "end": timeutil.hhmm(s.end_min),
        "label": f"{s.person_label} {timeutil.hhmm(s.start_min)}-{timeutil.hhmm(s.end_min)}",
        "proposed": s.proposed,
        "suggestion_id": s.suggestion_id,
    }


def event(e: Event) -> dict[str, Any]:
    return {
        "id": e.id,
        "ts": timeutil.iso(e.ts),
        "t_display": timeutil.clock_text(e.ts),
        "tool": e.tool,
        "actor_id": e.actor_id,
        "actor_name": e.actor_name,
        "action": e.action,
        "entity_type": e.entity_type,
        "entity_id": e.entity_id,
        "text": e.text,
    }


def event_audit(e: Event) -> dict[str, Any]:
    d = event(e)
    d["data_json"] = e.data_json
    d["ip"] = e.ip
    return d
