"""Shared CRM helpers: phone and email cleanup, labels, serializers, notifications, access log.

Nothing here writes client names, numbers or message text to the logs.
"""
from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

from fastapi import HTTPException
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from app.models import (
    AccessLog,
    Attachment,
    CalendarEvent,
    Contact,
    Message,
    Notification,
    Pet,
    Task,
    User,
    Visit,
)
from app.services import events as ev
from app.services import timeutil

# ---------------------------------------------------------------- constants
CONSENT_VALUES = ("full", "transactional", "none", "unsubscribed")
CONSENT_CAN_SEND = {"full", "transactional"}
ACTIVE_TASK = ("open", "in_progress")

TASK_KIND_LABELS = {
    "unknown_knowledge": "Needs an answer",
    "escalation": "Escalation",
    "callback": "Call back",
    "refill": "Refill",
    "booking": "Booking",
    "complaint": "Complaint",
    "emergency_followup": "Emergency follow-up",
    "results": "Lab results",
    "other": "Other",
}
EVENT_KIND_LABELS = {
    "checkup": "Check-up",
    "vaccine": "Vaccine",
    "surgery": "Surgery",
    "dental": "Dental",
    "consult": "Consult",
    "inquiry_call": "Inquiry call",
    "staff": "Staff",
    "reminder": "Reminder",
    "other": "Other",
}
INVERSE_RELATIONSHIP = {"parent": "child", "child": "parent"}


class CrmError(HTTPException):
    """A refusal with a plain sentence and an HTTP status."""

    def __init__(self, status: int, detail: str):
        super().__init__(status_code=status, detail=detail)


# ---------------------------------------------------------------- phone and email
_PHONE_JUNK = re.compile(r"[\s().\-]")


def normalize_phone(raw: str | None) -> str | None:
    """Return E.164 (Canadian default, +1) or None for empty. Raises ValueError for a bad number."""
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    if re.search(r"[A-Za-z]", text):
        raise ValueError("That phone number does not look right")
    plus = text.startswith("+")
    digits = _PHONE_JUNK.sub("", text.lstrip("+"))
    if not digits.isdigit():
        raise ValueError("That phone number does not look right")
    if plus or (len(digits) == 11 and digits[0] == "1") or len(digits) == 10:
        if not plus:
            digits = digits if len(digits) == 11 else "1" + digits
        if digits.startswith("1"):
            # North America: 1 + area code + exchange + line; area code and exchange never start with 0 or 1
            if len(digits) != 11 or digits[1] in "01" or digits[4] in "01":
                raise ValueError("That phone number does not look right")
        elif not (8 <= len(digits) <= 15) or digits[0] == "0":
            raise ValueError("That phone number does not look right")
        return "+" + digits
    raise ValueError("That phone number does not look right")


def phone_display(e164: str | None) -> str:
    if not e164:
        return ""
    if re.fullmatch(r"\+1\d{10}", e164):
        return f"({e164[2:5]}) {e164[5:8]}-{e164[8:]}"
    return e164


_EMAIL_RE = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@([A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,24}$")


def normalize_email(raw: str | None) -> str | None:
    if raw is None:
        return None
    text = raw.strip().lower()
    if not text:
        return None
    local = text.split("@", 1)[0]
    if len(text) > 254 or len(local) > 64 or ".." in text or local.startswith(".") or local.endswith(".") or not _EMAIL_RE.match(text):
        raise ValueError("That email address does not look right")
    return text


# ---------------------------------------------------------------- small helpers
def initials(name: str) -> str:
    parts = [p for p in re.split(r"[\s.]+", name or "") if p and p.lower() not in {"dr", "mr", "ms", "mrs", "mx"}]
    if not parts:
        return (name or "?")[:1].upper()
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def avatar_hue(contact_id: int) -> int:
    return int(hashlib.sha1(f"walnut-{contact_id}".encode()).hexdigest()[:8], 16) % 360


def preview(text: str, limit: int = 80) -> str:
    flat = re.sub(r"\s+", " ", text or "").strip()
    if len(flat) <= limit:
        return flat
    return flat[: limit - 3].rstrip() + "..."


def like_pattern(text: str) -> str:
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def aware(dt: datetime | None) -> datetime | None:
    """Naive values are read as Vancouver wall-clock time."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timeutil.VANCOUVER).astimezone(timezone.utc)
    return dt.astimezone(timezone.utc)


def local_day_start(d: date) -> datetime:
    return timeutil.at_local(d, 0, 0)


def age_label(birth: date | None, today: date | None = None) -> str:
    if birth is None:
        return ""
    today = today or timeutil.today_local()
    months = (today.year - birth.year) * 12 + (today.month - birth.month) - (1 if today.day < birth.day else 0)
    if months < 0:
        return ""
    if months < 2:
        return "under 2 months"
    if months < 24:
        return f"{months} months"
    return f"{months // 12} years"


def user_brief(u: User | None) -> dict[str, Any] | None:
    if u is None:
        return None
    return {"id": u.id, "display_name": u.display_name, "initials": u.initials or initials(u.display_name)}


def contact_brief(c: Contact | None) -> dict[str, Any] | None:
    if c is None:
        return None
    return {"id": c.id, "display_name": c.display_name, "kind": c.kind, "initials": initials(c.display_name), "avatar_hue": avatar_hue(c.id)}


# ---------------------------------------------------------------- access log and notifications
def log_access(db: Session, user: User | None, contact_id: int | None, action: str, detail: str = "", dedupe_seconds: int = 0) -> None:
    now = timeutil.now_utc()
    if dedupe_seconds and contact_id is not None and user is not None:
        recent = db.scalar(
            select(AccessLog.id)
            .where(AccessLog.user_id == user.id, AccessLog.contact_id == contact_id, AccessLog.action == action,
                   AccessLog.at > now - timedelta(seconds=dedupe_seconds))
            .limit(1)
        )
        if recent:
            return
    db.add(AccessLog(user_id=user.id if user else None, contact_id=contact_id, action=action, at=now,
                     ip=ev.current_ip.get()[:64], detail=detail[:200]))


def notify(db: Session, user_id: int, kind: str, text: str, link: str) -> Notification:
    n = Notification(user_id=user_id, kind=kind, text=text[:300], link=link[:200], created_at=timeutil.now_utc())
    db.add(n)
    db.flush()
    ev.queue_publish(db, "notifications", user_id, None)
    return n


def touch_contact(db: Session, contact: Contact, when: datetime | None = None) -> None:
    contact.last_active_at = when or timeutil.now_utc()


# ---------------------------------------------------------------- contact rows
def _group_counts(db: Session, stmt) -> dict[int, int]:
    return {int(k): int(v) for k, v in db.execute(stmt).all()}


def contact_rows(db: Session, contacts: Iterable[Contact]) -> list[dict[str, Any]]:
    contacts = list(contacts)
    if not contacts:
        return []
    ids = [c.id for c in contacts]
    owner_ids = {c.owner_user_id for c in contacts if c.owner_user_id}
    owners = {u.id: u for u in db.scalars(select(User).where(User.id.in_(owner_ids))).all()} if owner_ids else {}

    visit_count: dict[int, int] = {}
    last_visit: dict[int, datetime] = {}
    for cid, n, last in db.execute(
        select(Visit.contact_id, func.count(), func.max(Visit.visited_at)).where(Visit.contact_id.in_(ids)).group_by(Visit.contact_id)
    ).all():
        visit_count[cid] = n
        last_visit[cid] = last

    pet_names: dict[int, list[str]] = {i: [] for i in ids}
    for p in db.scalars(select(Pet).where(Pet.contact_id.in_(ids), Pet.status == "active").order_by(Pet.id)).all():
        pet_names[p.contact_id].append(p.name)

    open_tasks = _group_counts(
        db, select(Task.contact_id, func.count()).where(Task.contact_id.in_(ids), Task.status.in_(ACTIVE_TASK)).group_by(Task.contact_id)
    )
    unread = _group_counts(
        db,
        select(Message.contact_id, func.count())
        .join(Contact, Contact.id == Message.contact_id)
        .where(Message.contact_id.in_(ids), Message.direction == "in",
               or_(Contact.last_read_at.is_(None), Message.created_at > Contact.last_read_at))
        .group_by(Message.contact_id),
    )
    newest = (
        select(Message.contact_id.label("cid"), func.max(Message.created_at).label("mx"))
        .where(Message.contact_id.in_(ids)).group_by(Message.contact_id).subquery()
    )
    last_msg: dict[int, Message] = {}
    for m in db.scalars(select(Message).join(newest, and_(Message.contact_id == newest.c.cid, Message.created_at == newest.c.mx)).order_by(Message.id)).all():
        last_msg[m.contact_id] = m

    out = []
    for c in contacts:
        lm = last_msg.get(c.id)
        out.append({
            "id": c.id,
            "display_name": c.display_name,
            "initials": initials(c.display_name),
            "avatar_hue": avatar_hue(c.id),
            "kind": c.kind,
            "email": c.email,
            "phone": c.phone,
            "phone_display": phone_display(c.phone),
            "email_consent": c.email_consent,
            "sms_consent": c.sms_consent,
            "owner": ({"id": owners[c.owner_user_id].id, "display_name": owners[c.owner_user_id].display_name}
                      if c.owner_user_id in owners else None),
            "ai_enabled": c.ai_enabled,
            "source": c.source,
            "visit_count": visit_count.get(c.id, 0),
            "last_visit_at": timeutil.iso(last_visit.get(c.id)),
            "pet_names": pet_names.get(c.id, []),
            "open_tasks": open_tasks.get(c.id, 0),
            "unread": unread.get(c.id, 0),
            "last_message": ({"channel": lm.channel, "direction": lm.direction, "preview": preview(lm.body), "at": timeutil.iso(lm.created_at)}
                             if lm else None),
            "last_active_at": timeutil.iso(c.last_active_at),
            "created_at": timeutil.iso(c.created_at),
        })
    return out


def pet_dict(p: Pet) -> dict[str, Any]:
    return {
        "id": p.id, "contact_id": p.contact_id, "name": p.name, "species": p.species, "breed": p.breed, "sex": p.sex,
        "birth_date": p.birth_date.isoformat() if p.birth_date else None, "age_label": age_label(p.birth_date),
        "weight_kg": p.weight_kg, "notes": p.notes, "status": p.status,
    }


def attachment_dict(a: Attachment) -> dict[str, Any]:
    return {"id": a.id, "filename": a.filename, "mime": a.mime, "bytes": a.bytes, "created_at": timeutil.iso(a.created_at)}


# ---------------------------------------------------------------- tasks and events
def task_dicts(db: Session, tasks: Iterable[Task]) -> list[dict[str, Any]]:
    tasks = list(tasks)
    cids = {t.contact_id for t in tasks if t.contact_id}
    uids = {t.assigned_to for t in tasks if t.assigned_to}
    contacts = {c.id: c for c in db.scalars(select(Contact).where(Contact.id.in_(cids))).all()} if cids else {}
    users = {u.id: u for u in db.scalars(select(User).where(User.id.in_(uids))).all()} if uids else {}
    return [{
        "id": t.id, "kind": t.kind, "kind_label": TASK_KIND_LABELS.get(t.kind, t.kind), "classification": t.classification,
        "title": t.title, "reason": t.reason, "priority": t.priority, "status": t.status, "due_at": timeutil.iso(t.due_at),
        "raised_by": t.raised_by, "created_at": timeutil.iso(t.created_at), "conversation_id": t.conversation_id,
        "contact": contact_brief(contacts.get(t.contact_id)) if t.contact_id else None,
        "assigned": user_brief(users.get(t.assigned_to)) if t.assigned_to else None,
    } for t in tasks]


_PRIO_RANK = case((Task.priority == "high", 0), (Task.priority == "normal", 1), else_=2)


def task_order():
    return (_PRIO_RANK, Task.created_at.asc(), Task.id.asc())


def event_dicts(db: Session, rows: Iterable[CalendarEvent]) -> list[dict[str, Any]]:
    rows = list(rows)
    cids = {e.contact_id for e in rows if e.contact_id}
    pids = {e.pet_id for e in rows if e.pet_id}
    uids = {e.staff_user_id for e in rows if e.staff_user_id}
    contacts = {c.id: c for c in db.scalars(select(Contact).where(Contact.id.in_(cids))).all()} if cids else {}
    pets = {p.id: p for p in db.scalars(select(Pet).where(Pet.id.in_(pids))).all()} if pids else {}
    users = {u.id: u for u in db.scalars(select(User).where(User.id.in_(uids))).all()} if uids else {}
    out = []
    for e in rows:
        c, p, u = contacts.get(e.contact_id), pets.get(e.pet_id), users.get(e.staff_user_id)
        out.append({
            "id": e.id, "kind": e.kind, "kind_label": EVENT_KIND_LABELS.get(e.kind, e.kind), "classification": e.classification,
            "title": e.title, "starts_at": timeutil.iso(e.starts_at), "ends_at": timeutil.iso(e.ends_at), "status": e.status,
            "source": e.source,
            "contact": {"id": c.id, "display_name": c.display_name} if c else None,
            "pet": {"id": p.id, "name": p.name} if p else None,
            "staff": {"id": u.id, "display_name": u.display_name} if u else None,
        })
    return out


def person_counts(db: Session, user: User | None = None) -> dict[str, int]:
    """Counts for the left menu and Home."""
    base = Task.status.in_(ACTIVE_TASK)
    out = {
        "open": db.scalar(select(func.count()).select_from(Task).where(Task.status == "open")) or 0,
        "in_progress": db.scalar(select(func.count()).select_from(Task).where(Task.status == "in_progress")) or 0,
        "mine": 0,
        "unassigned": db.scalar(select(func.count()).select_from(Task).where(base, Task.assigned_to.is_(None))) or 0,
    }
    if user is not None:
        out["mine"] = db.scalar(select(func.count()).select_from(Task).where(base, Task.assigned_to == user.id)) or 0
    return out
