"""Conversation timeline, sending, internal notes and reminders."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import Strict
from app.db import get_db
from app.models import Attachment, Contact, Message, Note, NoteMention, Pet, PhoneCall, Reminder, Task, User, Visit
from app.security.auth import require
from app.services import crm
from app.services import events as ev
from app.services import sender, timeutil

router = APIRouter(prefix="/api", tags=["conversations"])

VISIT_LABELS = {"checkup": "Check-up", "vaccine": "Vaccine visit", "surgery": "Surgery", "dental": "Dental visit",
                "sick": "Sick visit", "emergency": "Emergency visit", "other": "Visit"}


# ---------------------------------------------------------------- bodies
class SendBody(Strict):
    channel: Literal["sms", "email"]
    subject: str = Field(default="", max_length=150)
    body: str = Field(min_length=1, max_length=2000)
    scheduled_for: datetime | None = None
    attachment_ids: list[int] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def _subject_rules(self):
        if self.channel == "sms" and self.subject:
            raise ValueError("A text has no subject")
        return self


class NoteBody(Strict):
    body: str = Field(min_length=1, max_length=2000)
    mention_ids: list[int] = Field(default_factory=list, max_length=10)
    attachment_ids: list[int] = Field(default_factory=list, max_length=10)


class ReminderBody(Strict):
    due_at: datetime
    text: str = Field(min_length=1, max_length=200)
    owner_id: int | None = None


# ---------------------------------------------------------------- helpers
def _contact(db: Session, contact_id: int) -> Contact:
    c = db.get(Contact, contact_id)
    if c is None or c.erased_at is not None:
        raise HTTPException(status_code=404, detail="No such contact")
    return c


def _sender_dict(kind: str, user: User | None, contact: Contact) -> dict[str, Any]:
    if kind == "client":
        return {"kind": "client", "name": contact.display_name, "initials": crm.initials(contact.display_name)}
    if kind == "ai":
        return {"kind": "ai", "name": "AI assistant", "initials": "AI"}
    if user is None:
        return {"kind": "staff", "name": "Staff", "initials": "S"}
    return {"kind": "staff", "name": user.display_name, "initials": user.initials or crm.initials(user.display_name)}


def build_items(db: Session, contact: Contact, *, messages: list[Message] = (), notes: list[Note] = (), visits: list[Visit] = (),
                tasks: list[Task] = ()) -> list[dict[str, Any]]:
    uids = {m.sender_user_id for m in messages if m.sender_user_id} | {n.author_id for n in notes if n.author_id}
    users = {u.id: u for u in db.scalars(select(User).where(User.id.in_(uids))).all()} if uids else {}
    msg_ids = [m.id for m in messages]
    note_ids = [n.id for n in notes]
    atts_by_msg: dict[int, list[dict]] = {}
    atts_by_note: dict[int, list[dict]] = {}
    if msg_ids or note_ids:
        for a in db.scalars(select(Attachment).where(Attachment.contact_id == contact.id).where(
                (Attachment.message_id.in_(msg_ids)) | (Attachment.note_id.in_(note_ids))).order_by(Attachment.id)).all():
            d = {"id": a.id, "filename": a.filename, "mime": a.mime, "bytes": a.bytes}
            if a.message_id is not None:
                atts_by_msg.setdefault(a.message_id, []).append(d)
            if a.note_id is not None:
                atts_by_note.setdefault(a.note_id, []).append(d)
    mentions: dict[int, list[dict]] = {}
    if note_ids:
        rows = db.execute(select(NoteMention.note_id, User).join(User, User.id == NoteMention.user_id).where(NoteMention.note_id.in_(note_ids))
                          .order_by(User.id)).all()
        for nid, u in rows:
            mentions.setdefault(nid, []).append({"id": u.id, "display_name": u.display_name})
    pet_ids = {v.pet_id for v in visits if v.pet_id}
    pets = {p.id: p for p in db.scalars(select(Pet).where(Pet.id.in_(pet_ids))).all()} if pet_ids else {}

    calls: dict[int, dict[str, Any]] = {}
    call_ids = {m.phone_call_id for m in messages if m.phone_call_id}
    if call_ids:
        for pid, rec, secs, has_t in db.execute(select(PhoneCall.id, PhoneCall.recording_id, PhoneCall.duration_seconds,
                                                         PhoneCall.transcript.is_not(None)).where(PhoneCall.id.in_(call_ids))).all():
            calls[pid] = {"id": pid, "has_recording": bool(rec), "has_transcript": bool(has_t), "seconds": secs}

    out: list[dict[str, Any]] = []
    for m in messages:
        out.append({
            "id": m.id, "type": "message", "channel": m.channel, "direction": m.direction,
            "sender": _sender_dict(m.sender_kind, users.get(m.sender_user_id), contact), "subject": m.subject, "body": m.body,
            "at": timeutil.iso(m.created_at), "delivery_state": m.delivery_state, "scheduled_for": timeutil.iso(m.scheduled_for),
            "attachments": atts_by_msg.get(m.id, []), "mentions": [], "meta": {"call": calls[m.phone_call_id]} if m.phone_call_id in calls else {},
        })
    for n in notes:
        out.append({
            "id": n.id, "type": "note", "channel": "note", "direction": None,
            "sender": _sender_dict("staff", users.get(n.author_id), contact), "subject": "", "body": n.body,
            "at": timeutil.iso(n.created_at), "delivery_state": None, "scheduled_for": None,
            "attachments": atts_by_note.get(n.id, []), "mentions": mentions.get(n.id, []), "meta": {},
        })
    for v in visits:
        pet = pets.get(v.pet_id)
        out.append({
            "id": v.id, "type": "visit", "channel": "visit", "direction": None,
            "sender": {"kind": "system", "name": "Visit", "initials": ""},
            "subject": VISIT_LABELS.get(v.kind, "Visit") + (f" for {pet.name}" if pet else ""), "body": v.summary,
            "at": timeutil.iso(v.visited_at), "delivery_state": None, "scheduled_for": None, "attachments": [], "mentions": [],
            "meta": {"kind": v.kind, "pet_id": v.pet_id, "source": v.source},
        })
    for t in tasks:
        out.append({
            "id": t.id, "type": "task", "channel": "task", "direction": None,
            "sender": {"kind": "system", "name": "Task", "initials": ""}, "subject": t.title, "body": t.reason,
            "at": timeutil.iso(t.created_at), "delivery_state": None, "scheduled_for": None, "attachments": [], "mentions": [],
            "meta": {"kind": t.kind, "status": t.status, "priority": t.priority, "raised_by": t.raised_by},
        })
    out.sort(key=lambda i: (i["at"], i["type"], i["id"]), reverse=True)
    return out


def reminder_dict(r: Reminder, contact: Contact | None, owner: User | None) -> dict[str, Any]:
    return {
        "id": r.id, "contact": {"id": contact.id, "display_name": contact.display_name} if contact else None,
        "owner": {"id": owner.id, "display_name": owner.display_name} if owner else None,
        "due_at": timeutil.iso(r.due_at), "text": r.text, "done_at": timeutil.iso(r.done_at),
    }


# ---------------------------------------------------------------- timeline
@router.get("/contacts/{contact_id}/timeline")
def timeline(
    contact_id: int,
    channel: Literal["all", "email", "sms", "call", "note"] = "all",
    before: datetime | None = None,
    limit: int = Query(default=30, ge=1, le=100),
    user: User = Depends(require("messages.view")),
    db: Session = Depends(get_db),
):
    c = _contact(db, contact_id)
    cut = crm.aware(before)
    take = limit + 1

    def limited(model, col, extra=()):
        stmt = select(model).where(model.contact_id == c.id, *extra)
        if cut is not None:
            stmt = stmt.where(col < cut)
        return db.scalars(stmt.order_by(col.desc(), model.id.desc()).limit(take)).all()

    msgs: list[Message] = []
    notes: list[Note] = []
    visits: list[Visit] = []
    tasks: list[Task] = []
    if channel in ("all", "email", "sms", "call"):
        extra = () if channel == "all" else (Message.channel == channel,)
        msgs = limited(Message, Message.created_at, extra)
    if channel in ("all", "note"):
        notes = limited(Note, Note.created_at)
    if channel == "all":
        visits = limited(Visit, Visit.visited_at)
        tasks = limited(Task, Task.created_at)
    merged = build_items(db, c, messages=msgs, notes=notes, visits=visits, tasks=tasks)
    more = len(merged) > limit
    items = merged[:limit]
    crm.log_access(db, user, c.id, "view", dedupe_seconds=120)
    db.commit()
    return {"items": items, "next_before": items[-1]["at"] if more and items else None}


@router.post("/contacts/{contact_id}/read")
def mark_read(contact_id: int, user: User = Depends(require("messages.view")), db: Session = Depends(get_db)):
    c = _contact(db, contact_id)
    c.last_read_at = timeutil.now_utc()
    ev.queue_publish(db, "contacts", c.id, None)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- send
@router.post("/contacts/{contact_id}/messages", status_code=201)
def send_message(contact_id: int, body: SendBody, user: User = Depends(require("messages.send")), db: Session = Depends(get_db)):
    c = _contact(db, contact_id)
    msg = sender.send_to_contact(
        db, c, user, channel=body.channel, subject=body.subject, body=body.body,
        scheduled_for=crm.aware(body.scheduled_for), attachment_ids=body.attachment_ids,
    )
    db.commit()
    return {"item": build_items(db, c, messages=[msg])[0]}


# ---------------------------------------------------------------- notes
@router.post("/contacts/{contact_id}/notes", status_code=201)
def add_note(contact_id: int, body: NoteBody, user: User = Depends(require("notes.write")), db: Session = Depends(get_db)):
    c = _contact(db, contact_id)
    mention_ids = sorted(set(body.mention_ids))
    people = db.scalars(select(User).where(User.id.in_(mention_ids), User.active.is_(True))).all() if mention_ids else []
    if len(people) != len(mention_ids):
        raise HTTPException(status_code=400, detail="One of the people you mentioned could not be found.")
    now = timeutil.now_utc()
    note = Note(contact_id=c.id, author_id=user.id, body=body.body, created_at=now)
    db.add(note)
    db.flush()
    sender.link_attachments(db, c.id, body.attachment_ids, note_id=note.id)
    for p in people:
        db.add(NoteMention(note_id=note.id, user_id=p.id))
        if p.id != user.id:
            crm.notify(db, p.id, "mention", f"{user.display_name} mentioned you on {c.display_name}", f"/contacts/{c.id}")
    crm.touch_contact(db, c, now)
    ev.record(db, ev.CONVERSATIONS, "note_added", f"{user.display_name} added an internal note", user, "note", note.id,
              {"contact_id": c.id, "mentions": len(people)})
    ev.queue_publish(db, "contacts", c.id, None)
    db.commit()
    return {"item": build_items(db, c, notes=[note])[0]}


# ---------------------------------------------------------------- reminders
@router.post("/contacts/{contact_id}/reminders", status_code=201)
def add_reminder(contact_id: int, body: ReminderBody, user: User = Depends(require("notes.write")), db: Session = Depends(get_db)):
    c = _contact(db, contact_id)
    owner = user
    if body.owner_id is not None and body.owner_id != user.id:
        owner = db.get(User, body.owner_id)
        if owner is None or not owner.active:
            raise HTTPException(status_code=400, detail="That person cannot get the reminder.")
    r = Reminder(contact_id=c.id, owner_id=owner.id, due_at=crm.aware(body.due_at), text=body.text)
    db.add(r)
    db.flush()
    ev.record(db, ev.CONVERSATIONS, "reminder_added", f"{user.display_name} added a reminder", user, "reminder", r.id, {"contact_id": c.id})
    db.commit()
    return {"item": reminder_dict(r, c, owner)}


@router.get("/reminders")
def list_reminders(
    mine: bool = False,
    done: Literal["0", "1"] | None = None,
    user: User = Depends(require("contacts.view")),
    db: Session = Depends(get_db),
):
    stmt = select(Reminder, Contact).join(Contact, Contact.id == Reminder.contact_id)
    if mine:
        stmt = stmt.where(Reminder.owner_id == user.id)
    if done == "0":
        stmt = stmt.where(Reminder.done_at.is_(None))
    elif done == "1":
        stmt = stmt.where(Reminder.done_at.is_not(None))
    rows = db.execute(stmt.order_by(Reminder.due_at, Reminder.id).limit(200)).all()
    uids = {r.owner_id for r, _ in rows if r.owner_id}
    owners = {u.id: u for u in db.scalars(select(User).where(User.id.in_(uids))).all()} if uids else {}
    return {"items": [reminder_dict(r, c, owners.get(r.owner_id)) for r, c in rows]}


@router.post("/reminders/{reminder_id}/done")
def reminder_done(reminder_id: int, user: User = Depends(require("notes.write")), db: Session = Depends(get_db)):
    r = db.get(Reminder, reminder_id)
    if r is None:
        raise HTTPException(status_code=404, detail="No such reminder")
    if r.done_at is None:
        r.done_at = timeutil.now_utc()
        ev.record(db, ev.CONVERSATIONS, "reminder_done", f"{user.display_name} finished a reminder", user, "reminder", r.id, {})
        db.commit()
    c = db.get(Contact, r.contact_id)
    owner = db.get(User, r.owner_id) if r.owner_id else None
    return {"item": reminder_dict(r, c, owner)}
