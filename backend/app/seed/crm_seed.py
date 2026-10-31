"""Seed the Clinic CRM demo rows (all invented, all marked is_demo) and remove only those rows on reset.

Staff are looked up by display name. When the accounts do not exist yet (the usual order in tests is
reset-demo, then create-users) the user columns stay empty and relink_users() fills them in later.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models import (
    AccessLog,
    Attachment,
    CalendarEvent,
    Contact,
    ContactLink,
    ContactNoteHistory,
    Conversation,
    Message,
    Note,
    NoteMention,
    Notification,
    Pet,
    Reminder,
    SavedView,
    Task,
    User,
    Visit,
)
from app.seed import crm_data as D
from app.services import attachments as att_store
from app.services import timeutil

CRM_TABLES = [
    NoteMention, Attachment, Reminder, Note, Message, Visit, Task, CalendarEvent, ContactNoteHistory, ContactLink, Pet,
    Notification, SavedView, AccessLog, Contact,
]


# ---------------------------------------------------------------- wipe
def wipe_demo(db: Session) -> None:
    """Delete only the rows marked is_demo. Rows people added themselves stay."""
    demo_files = list(db.scalars(select(Attachment.stored_name).where(Attachment.is_demo.is_(True))).all())
    demo_ids = list(db.scalars(select(Contact.id).where(Contact.is_demo.is_(True))).all())
    for model in CRM_TABLES:
        db.execute(delete(model).where(model.is_demo.is_(True)))
    # Real rows that hang off a demo contact (a note somebody typed) go with it through the foreign keys.
    db.flush()
    for name in demo_files:
        att_store.remove(name)
    _ = demo_ids


# ---------------------------------------------------------------- helpers
def _when(spec: tuple, now: datetime, k: int = 0) -> datetime:
    if spec[0] == "ago":
        _, d, h, m = spec
        t = now - timedelta(days=d, hours=h, minutes=m)
    else:
        _, clock, y = spec
        t = timeutil.anchor(clock, now, pm_is_yesterday=y)
    # never in the future, even when the demo is reset early in the morning
    return min(t, now - timedelta(minutes=2, seconds=k))


def _users(db: Session) -> dict[str, int]:
    return {u.display_name: u.id for u in db.scalars(select(User)).all()}


def _phone(c: dict[str, Any]) -> str | None:
    return f"+1{c['area']}555{c['ph']}" if c.get("ph") else None


def _event_day(today, offset: int):
    day = today + timedelta(days=offset)
    return day + timedelta(days=1) if day.weekday() == 6 else day


# ---------------------------------------------------------------- seed
def seed_crm(db: Session, now: datetime, conv_rows: dict[str, Conversation]) -> dict[str, int]:
    """Create the demo contacts, pets, visits, messages, notes, tasks, events and reminders. Caller commits."""
    users = _users(db)
    today = timeutil.today_local(now)
    contacts: dict[str, Contact] = {}
    pets: dict[tuple[str, str], Pet] = {}
    last_in: dict[str, datetime] = {}
    last_any: dict[str, datetime] = {}

    for spec in D.CONTACTS:
        created = now - timedelta(days=spec["created"], hours=3)
        c = Contact(
            kind=spec["kind"], display_name=spec["name"], email=spec["email"], email_consent=spec["ec"], phone=_phone(spec),
            sms_consent=spec["sc"], consent_source=spec["cs"], consent_at=created + timedelta(minutes=5), source=spec["source"],
            owner_user_id=users.get(spec["owner"]) if spec["owner"] else None, ai_enabled=spec["ai"],
            preference_notes=spec["prefs"], created_at=created, last_active_at=created, last_read_at=now, is_demo=True,
        )
        db.add(c)
        db.flush()
        contacts[spec["name"]] = c
        for p in spec["pets"]:
            years, months = p["born"]
            pet = Pet(contact_id=c.id, name=p["name"], species=p["species"], breed=p["breed"], sex=p["sex"],
                      birth_date=today - timedelta(days=years * 365 + months * 30), weight_kg=p["kg"], notes=p["notes"],
                      status=p.get("status", "active"), is_demo=True)
            db.add(pet)
            db.flush()
            pets[(spec["name"], p["name"])] = pet
            for days_ago, kind, summary in p["visits"]:
                visited = timeutil.at_local(today - timedelta(days=max(days_ago, 1)), 10, 0)
                db.add(Visit(contact_id=c.id, pet_id=pet.id, visited_at=visited, kind=kind, summary=summary, source="import", is_demo=True))
                if visited > last_any.get(spec["name"], created):
                    last_any[spec["name"]] = visited
    db.flush()

    # notes on the preference field, with edit history
    for name, old, new, _editor, days in D.NOTE_HISTORY:
        db.add(ContactNoteHistory(contact_id=contacts[name].id, old_text=old, new_text=new, edited_at=now - timedelta(days=days), is_demo=True))

    # associated contacts
    for a, b, rel, note in D.LINKS:
        db.add(ContactLink(contact_id=contacts[a].id, related_contact_id=contacts[b].id, relationship=rel, note=note, is_demo=True))

    # the timeline
    for name, items in D.TIMELINES.items():
        c = contacts[name]
        for idx, item in enumerate(items):
            kind = item[0]
            extra: dict[str, Any] = item[-1] if isinstance(item[-1], dict) else {}
            if kind == "note":
                _, who, body, when, _mentions = item
                t = _when(when, now, idx)
                db.add(Note(contact_id=c.id, body=body, created_at=t, is_demo=True))
            else:
                if kind == "cin":
                    _, body, when = item
                    m = Message(contact_id=c.id, channel="sms", direction="in", sender_kind="client", body=body, delivery_state="received")
                elif kind == "cein":
                    _, subject, body, when = item[:4]
                    m = Message(contact_id=c.id, channel="email", direction="in", sender_kind="client", subject=subject, body=body,
                                delivery_state="received")
                elif kind == "sout":
                    _, who, body, when = item[:4]
                    m = Message(contact_id=c.id, channel="sms", direction="out", sender_kind="ai" if who == "ai" else "staff", body=body,
                                delivery_state="dry_run")
                elif kind == "eout":
                    _, who, subject, body, when = item[:5]
                    m = Message(contact_id=c.id, channel="email", direction="out", sender_kind="ai" if who == "ai" else "staff", subject=subject,
                                body=body, delivery_state="dry_run")
                elif kind == "call":
                    _, direction, who, body, when = item
                    m = Message(contact_id=c.id, channel="call", direction=direction, sender_kind="client" if direction == "in" else "staff",
                                body=body, delivery_state="received")
                else:  # pragma: no cover - guards the data file
                    raise ValueError(f"unknown timeline item {kind!r}")
                t = _when(when, now, idx)
                m.created_at = t
                if extra.get("sched_hours"):
                    m.delivery_state = "scheduled_dry_run"
                    m.scheduled_for = now + timedelta(hours=extra["sched_hours"])
                db.add(m)
                if m.direction == "in" and t > last_in.get(name, datetime.min.replace(tzinfo=t.tzinfo)):
                    last_in[name] = t
            if t > last_any.get(name, c.created_at):
                last_any[name] = t

    # last active and unread markers
    for spec in D.CONTACTS:
        c = contacts[spec["name"]]
        c.last_active_at = max(c.created_at, last_any.get(spec["name"], c.created_at))
        if spec["unread"] and spec["name"] in last_in:
            c.last_read_at = last_in[spec["name"]] - timedelta(minutes=1)
    db.flush()

    # tasks
    for t in D.TASKS:
        created = _when(t["age"], now)
        done_at = _when(t["done_ago"], now) if t.get("done_ago") else None
        row = Task(
            kind=t["kind"], classification=t["cls"], title=t["title"], reason=t["reason"], contact_id=contacts[t["contact"]].id,
            conversation_id=conv_rows[t["conv"]].id if t.get("conv") and t["conv"] in conv_rows else None,
            assigned_to=users.get(t["assigned"]) if t["assigned"] else None, priority=t["prio"], status=t["status"],
            due_at=None, raised_by=t["by"], created_at=created, done_at=done_at,
            done_by=users.get(t["assigned"]) if (done_at and t["assigned"]) else None, is_demo=True,
        )
        if t["title"] == "Check in on Mochi":
            row.due_at = now + timedelta(hours=3)
        db.add(row)

    # calendar
    for title, kind, cls, cname, pname, staff, offset, hhmm, minutes, status, source in D.EVENTS:
        day = _event_day(today, offset)
        h, mi = (int(x) for x in hhmm.split(":"))
        start = timeutil.at_local(day, h, mi)
        c = contacts.get(cname) if cname else None
        db.add(CalendarEvent(
            kind=kind, classification=cls, title=title, contact_id=c.id if c else None,
            pet_id=pets[(cname, pname)].id if cname and pname else None, staff_user_id=users.get(staff) if staff else None,
            starts_at=start, ends_at=start + timedelta(minutes=minutes), status=status, source=source, is_demo=True,
        ))

    # reminders
    for cname, owner, hours, text in D.REMINDERS:
        db.add(Reminder(contact_id=contacts[cname].id, owner_id=users.get(owner), due_at=now + timedelta(hours=hours), text=text, is_demo=True))
    db.flush()

    relink_users(db, now)
    return count_demo(db)


def count_demo(db: Session) -> dict[str, int]:
    out = {}
    for model in (Contact, Pet, Visit, Message, Note, Task, CalendarEvent, Reminder, ContactLink, Notification):
        out[model.__tablename__] = db.scalar(select(func.count()).select_from(model).where(model.is_demo.is_(True))) or 0
    return out


# ---------------------------------------------------------------- staff links, safe to run again and again
def relink_users(db: Session, now: datetime | None = None) -> None:
    """Point demo rows at staff accounts by display name. Does nothing for names that do not exist yet."""
    now = now or timeutil.now_utc()
    users = _users(db)
    if not users:
        return
    contacts = {c.display_name: c for c in db.scalars(select(Contact).where(Contact.is_demo.is_(True))).all()}
    if not contacts:
        return

    for spec in D.CONTACTS:
        c = contacts.get(spec["name"])
        if c is not None and spec["owner"] in users:
            c.owner_user_id = users[spec["owner"]]

    by_text: dict[tuple[int, str], ContactNoteHistory] = {
        (h.contact_id, h.new_text): h for h in db.scalars(select(ContactNoteHistory).where(ContactNoteHistory.is_demo.is_(True))).all()
    }
    for name, _old, new, editor, _days in D.NOTE_HISTORY:
        h = by_text.get((contacts[name].id, new))
        if h is not None and editor in users:
            h.edited_by = users[editor]

    msgs = {(m.contact_id, m.body, m.direction): m for m in db.scalars(select(Message).where(Message.is_demo.is_(True))).all()}
    notes = {(n.contact_id, n.body): n for n in db.scalars(select(Note).where(Note.is_demo.is_(True))).all()}
    mentioned = {(r.note_id, r.user_id) for r in db.scalars(select(NoteMention).where(NoteMention.is_demo.is_(True))).all()}
    for name, items in D.TIMELINES.items():
        c = contacts.get(name)
        if c is None:
            continue
        for item in items:
            kind = item[0]
            if kind == "note":
                _, who, body, _when_, mention_names = item
                n = notes.get((c.id, body))
                if n is None:
                    continue
                if who in users:
                    n.author_id = users[who]
                for mname in mention_names:
                    if mname in users and (n.id, users[mname]) not in mentioned:
                        db.add(NoteMention(note_id=n.id, user_id=users[mname], is_demo=True))
                        mentioned.add((n.id, users[mname]))
            elif kind in ("sout", "eout", "call"):
                who = item[1] if kind != "call" else item[2]
                body = item[2] if kind == "sout" else (item[3] if kind == "eout" else item[3])
                direction = "out"
                if kind == "call":
                    direction = item[1]
                m = msgs.get((c.id, body, direction))
                if m is not None and who and who != "ai" and who in users:
                    m.sender_user_id = users[who]

    for t in db.scalars(select(Task).where(Task.is_demo.is_(True))).all():
        for spec in D.TASKS:
            if spec["title"] == t.title:
                if spec["assigned"] in users:
                    t.assigned_to = users[spec["assigned"]]
                    if t.status == "done":
                        t.done_by = users[spec["assigned"]]
                break
    ev_staff = {title: staff for title, _k, _c, _cn, _pn, staff, *_rest in D.EVENTS}
    for e in db.scalars(select(CalendarEvent).where(CalendarEvent.is_demo.is_(True))).all():
        staff = ev_staff.get(e.title)
        if staff in users:
            e.staff_user_id = users[staff]
    owners = {text: owner for _c, owner, _h, text in D.REMINDERS}
    for r in db.scalars(select(Reminder).where(Reminder.is_demo.is_(True))).all():
        if owners.get(r.text) in users:
            r.owner_id = users[owners[r.text]]

    # two unread notifications for Priya
    tasks = {t.title: t for t in db.scalars(select(Task).where(Task.is_demo.is_(True))).all()}
    have = {(n.user_id, n.text) for n in db.scalars(select(Notification).where(Notification.is_demo.is_(True))).all()}
    for spec in D.NOTIFICATIONS:
        uid = users.get(spec["user"])
        if uid is None or (uid, spec["text"]) in have:
            continue
        if "task" in spec:
            task = tasks.get(spec["task"])
            if task is None:
                continue
            link = f"/tasks/{task.id}"
        else:
            target = contacts.get(spec["link_contact"])
            if target is None:
                continue
            link = f"/contacts/{target.id}"
        db.add(Notification(user_id=uid, kind=spec["kind"], text=spec["text"], link=link, created_at=_when(spec["ago"], now), is_demo=True))
    db.flush()
