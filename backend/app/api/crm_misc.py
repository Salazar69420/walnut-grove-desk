"""Staff list, search, notifications, saved views, attachments, calendar events and the access log."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.schemas import Strict
from app.db import get_db
from app.models import (
    AccessLog,
    Attachment,
    CalendarEvent,
    Contact,
    Message,
    Notification,
    Pet,
    Reminder,
    SavedView,
    Task,
    User,
)
from app.security.auth import require
from app.services import attachments as att_store
from app.services import crm
from app.services import events as ev
from app.services import timeutil

router = APIRouter(prefix="/api", tags=["crm"])


# ---------------------------------------------------------------- staff
@router.get("/staff")
def staff(user: User = Depends(require("contacts.view")), db: Session = Depends(get_db)):
    rows = db.scalars(select(User).where(User.active.is_(True), ~User.username.like("e2e%")).order_by(User.display_name, User.id)).all()
    return {"items": [{"id": u.id, "display_name": u.display_name, "initials": u.initials or crm.initials(u.display_name), "role": u.role}
                      for u in rows]}


# ---------------------------------------------------------------- search
@router.get("/search")
def search(q: str = Query(min_length=2, max_length=80), user: User = Depends(require("contacts.view")), db: Session = Depends(get_db)):
    text = q.strip()
    if len(text) < 2:
        raise HTTPException(status_code=400, detail="Type at least 2 letters to search.")
    like = crm.like_pattern(text)
    digits = re.sub(r"\D", "", text)
    pet_hit = select(Pet.contact_id).where(Pet.name.like(like, escape="\\"))
    conds = [Contact.display_name.like(like, escape="\\"), Contact.email.like(like, escape="\\"), Contact.id.in_(pet_hit)]
    if len(digits) >= 3:
        conds.append(Contact.phone.like("%" + digits + "%"))
    contacts = db.scalars(select(Contact).where(Contact.erased_at.is_(None), or_(*conds)).order_by(Contact.last_active_at.desc()).limit(8)).all()
    low = text.lower()
    out_contacts = []
    for c in contacts:
        if low in c.display_name.lower():
            match = "name"
        elif c.email and low in c.email:
            match = "email"
        elif len(digits) >= 3 and c.phone and digits in c.phone:
            match = "phone"
        else:
            match = "pet"
        out_contacts.append({"id": c.id, "display_name": c.display_name, "kind": c.kind, "match": match})
    pets = db.execute(
        select(Pet, Contact).join(Contact, Contact.id == Pet.contact_id)
        .where(Pet.name.like(like, escape="\\"), Contact.erased_at.is_(None)).order_by(Pet.name, Pet.id).limit(8)
    ).all()
    tasks = db.scalars(select(Task).where(Task.title.like(like, escape="\\")).order_by(Task.created_at.desc()).limit(8)).all()
    msgs = db.execute(
        select(Message, Contact).join(Contact, Contact.id == Message.contact_id)
        .where(Message.body.like(like, escape="\\"), Contact.erased_at.is_(None)).order_by(Message.created_at.desc(), Message.id.desc()).limit(8)
    ).all()
    return {
        "contacts": out_contacts,
        "pets": [{"id": p.id, "name": p.name, "species": p.species, "contact": {"id": c.id, "display_name": c.display_name}} for p, c in pets],
        "tasks": [{"id": t.id, "title": t.title, "status": t.status} for t in tasks],
        "messages": [{"id": m.id, "contact_id": c.id, "contact_name": c.display_name, "preview": crm.preview(m.body),
                      "at": timeutil.iso(m.created_at)} for m, c in msgs],
    }


# ---------------------------------------------------------------- notifications (for the signed-in person only)
def materialize_due_reminders(db: Session, user: User) -> None:
    now = timeutil.now_utc()
    due = db.execute(
        select(Reminder, Contact).join(Contact, Contact.id == Reminder.contact_id)
        .where(Reminder.owner_id == user.id, Reminder.done_at.is_(None), Reminder.notified_at.is_(None), Reminder.due_at <= now)
    ).all()
    for r, c in due:
        r.notified_at = now
        crm.notify(db, user.id, "reminder", f"Reminder for {c.display_name}: {r.text}", f"/contacts/{c.id}")
    if due:
        db.commit()


def _notification(n: Notification) -> dict[str, Any]:
    return {"id": n.id, "kind": n.kind, "text": n.text, "link": n.link, "created_at": timeutil.iso(n.created_at),
            "read_at": timeutil.iso(n.read_at)}


@router.get("/notifications")
def list_notifications(user: User = Depends(require("home.view")), db: Session = Depends(get_db)):
    materialize_due_reminders(db, user)
    rows = db.scalars(select(Notification).where(Notification.user_id == user.id)
                      .order_by(Notification.created_at.desc(), Notification.id.desc()).limit(50)).all()
    unread = db.scalar(select(func.count()).select_from(Notification).where(Notification.user_id == user.id, Notification.read_at.is_(None))) or 0
    return {"items": [_notification(n) for n in rows], "unread": unread}


@router.post("/notifications/read-all")
def read_all_notifications(user: User = Depends(require("home.view")), db: Session = Depends(get_db)):
    now = timeutil.now_utc()
    for n in db.scalars(select(Notification).where(Notification.user_id == user.id, Notification.read_at.is_(None))).all():
        n.read_at = now
    db.commit()
    return {"ok": True, "unread": 0}


@router.post("/notifications/{notification_id}/read")
def read_notification(notification_id: int, user: User = Depends(require("home.view")), db: Session = Depends(get_db)):
    n = db.get(Notification, notification_id)
    if n is None or n.user_id != user.id:
        raise HTTPException(status_code=404, detail="No such notification")
    if n.read_at is None:
        n.read_at = timeutil.now_utc()
        db.commit()
    return {"item": _notification(n)}


# ---------------------------------------------------------------- saved views (own only)
class ViewBody(Strict):
    screen: Literal["contacts", "tasks"]
    name: str = Field(min_length=1, max_length=60)
    filters: dict[str, Any] = Field(default_factory=dict)
    columns: list[str] = Field(default_factory=list, max_length=40)


def _view(v: SavedView) -> dict[str, Any]:
    return {"id": v.id, "screen": v.screen, "name": v.name, "filters": json.loads(v.filters_json or "{}"),
            "columns": json.loads(v.columns_json or "[]")}


@router.get("/views")
def list_views(screen: Literal["contacts", "tasks"], user: User = Depends(require("contacts.view")), db: Session = Depends(get_db)):
    rows = db.scalars(select(SavedView).where(SavedView.user_id == user.id, SavedView.screen == screen).order_by(SavedView.name, SavedView.id)).all()
    return {"items": [_view(v) for v in rows]}


@router.post("/views", status_code=201)
def create_view(body: ViewBody, user: User = Depends(require("contacts.view")), db: Session = Depends(get_db)):
    f_json = json.dumps(body.filters, separators=(",", ":"))
    c_json = json.dumps(body.columns, separators=(",", ":"))
    if len(f_json) > 4000 or any(len(c) > 40 for c in body.columns):
        raise HTTPException(status_code=400, detail="That view is too big to save.")
    count = db.scalar(select(func.count()).select_from(SavedView).where(SavedView.user_id == user.id)) or 0
    if count >= 50:
        raise HTTPException(status_code=409, detail="You have too many saved views. Delete one first.")
    v = SavedView(user_id=user.id, screen=body.screen, name=body.name, filters_json=f_json, columns_json=c_json)
    db.add(v)
    db.commit()
    return {"item": _view(v)}


@router.delete("/views/{view_id}")
def delete_view(view_id: int, user: User = Depends(require("contacts.view")), db: Session = Depends(get_db)):
    v = db.get(SavedView, view_id)
    if v is None or v.user_id != user.id:
        raise HTTPException(status_code=404, detail="No such view")
    db.delete(v)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- attachments
@router.post("/contacts/{contact_id}/attachments", status_code=201)
def upload_attachment(contact_id: int, file: UploadFile = File(...), user: User = Depends(require("messages.send")),
                      db: Session = Depends(get_db)):
    c = db.get(Contact, contact_id)
    if c is None or c.erased_at is not None:
        raise HTTPException(status_code=404, detail="No such contact")
    chunks: list[bytes] = []
    size = 0
    while True:
        chunk = file.file.read(att_store.CHUNK)
        if not chunk:
            break
        size += len(chunk)
        if size > att_store.MAX_BYTES:
            raise HTTPException(status_code=413, detail="That file is larger than 5 MB.")
        chunks.append(chunk)
    if size == 0:
        raise HTTPException(status_code=400, detail="That file is empty.")
    data = b"".join(chunks)
    mime = att_store.sniff_mime(data[:16])
    if mime is None:
        raise HTTPException(status_code=400, detail="Only JPEG, PNG, WEBP and PDF files can be attached.")
    stored = att_store.write_new(data)
    a = Attachment(contact_id=c.id, filename=att_store.safe_display_name(file.filename), mime=mime, bytes=size,
                   sha256=hashlib.sha256(data).hexdigest(), stored_name=stored, uploaded_by=user.id, created_at=timeutil.now_utc())
    db.add(a)
    try:
        db.flush()
        ev.record(db, ev.CONVERSATIONS, "attachment_added", f"{user.display_name} attached a file", user, "contact", c.id, {"attachment_id": a.id})
        db.commit()
    except BaseException:
        db.rollback()
        att_store.remove(stored)
        raise
    return {"id": a.id, "filename": a.filename, "mime": a.mime, "bytes": a.bytes}


@router.get("/attachments/{attachment_id}")
def download_attachment(attachment_id: int, user: User = Depends(require("contacts.view")), db: Session = Depends(get_db)):
    a = db.get(Attachment, attachment_id)
    if a is None:
        raise HTTPException(status_code=404, detail="No such file")
    path = att_store.path_for(a.stored_name)
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="That file is no longer available.")
    crm.log_access(db, user, a.contact_id, "view", detail="file", dedupe_seconds=120)
    db.commit()
    return FileResponse(
        path, media_type=a.mime,
        headers={
            "Content-Disposition": att_store.content_disposition(a.filename),
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox",
            "Cache-Control": "private, no-store",
        },
    )


# ---------------------------------------------------------------- calendar events
def _bound(text: str | None, end: bool) -> datetime | None:
    if not text:
        return None
    text = text.strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            d = date.fromisoformat(text)
            return crm.local_day_start(d + timedelta(days=1)) if end else crm.local_day_start(d)
        return crm.aware(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError:
        raise HTTPException(status_code=400, detail="Dates must look like 2026-11-02 or 2026-11-02T09:30:00Z.")


def calendar_items(db: Session, frm: str | None, to: str | None, contact_id: int | None, kind: str | None) -> dict[str, Any]:
    start = _bound(frm, False) or crm.local_day_start(timeutil.today_local())
    end = _bound(to, True) or (start + timedelta(days=14))
    if end <= start:
        raise HTTPException(status_code=400, detail="The end must be after the start.")
    stmt = select(CalendarEvent).where(CalendarEvent.starts_at >= start, CalendarEvent.starts_at < end)
    if contact_id is not None:
        stmt = stmt.where(CalendarEvent.contact_id == contact_id)
    if kind:
        if kind not in crm.EVENT_KIND_LABELS:
            raise HTTPException(status_code=400, detail="Unknown event kind.")
        stmt = stmt.where(CalendarEvent.kind == kind)
    rows = db.scalars(stmt.order_by(CalendarEvent.starts_at, CalendarEvent.id).limit(1000)).all()
    return {"items": crm.event_dicts(db, rows)}


@router.get("/calendar/events")
def calendar_events(
    from_: str | None = Query(default=None, alias="from", max_length=40),
    to: str | None = Query(default=None, max_length=40),
    contact_id: int | None = None,
    kind: str | None = Query(default=None, max_length=20),
    user: User = Depends(require("calendar.view")),
    db: Session = Depends(get_db),
):
    return calendar_items(db, from_, to, contact_id, kind)


EventKind = Literal["checkup", "vaccine", "surgery", "dental", "consult", "inquiry_call", "staff", "reminder", "other"]


class EventIn(Strict):
    kind: EventKind
    classification: Literal["inquiry", "patient", "none"] = "none"
    title: str = Field(min_length=1, max_length=200)
    contact_id: int | None = None
    pet_id: int | None = None
    staff_user_id: int | None = None
    starts_at: datetime
    ends_at: datetime
    status: Literal["booked", "requested", "done", "cancelled"] = "booked"


class EventPatch(Strict):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    staff_user_id: int | None = None
    status: Literal["booked", "requested", "done", "cancelled"] | None = None


@router.post("/calendar/events", status_code=201)
def create_calendar_event(body: EventIn, user: User = Depends(require("calendar.edit")), db: Session = Depends(get_db)):
    start, end = crm.aware(body.starts_at), crm.aware(body.ends_at)
    if end <= start:
        raise HTTPException(status_code=400, detail="The end must be after the start.")
    if body.contact_id is not None and db.get(Contact, body.contact_id) is None:
        raise HTTPException(status_code=400, detail="That contact was not found.")
    if body.pet_id is not None:
        pet = db.get(Pet, body.pet_id)
        if pet is None or (body.contact_id is not None and pet.contact_id != body.contact_id):
            raise HTTPException(status_code=400, detail="That pet was not found.")
    if body.staff_user_id is not None:
        staff_user = db.get(User, body.staff_user_id)
        if staff_user is None or not staff_user.active:
            raise HTTPException(status_code=400, detail="That staff member was not found.")
    e = CalendarEvent(kind=body.kind, classification=body.classification, title=body.title, contact_id=body.contact_id, pet_id=body.pet_id,
                      staff_user_id=body.staff_user_id, starts_at=start, ends_at=end, status=body.status, source="app")
    db.add(e)
    db.flush()
    ev.record(db, ev.CONTACTS, "event_created", f"{user.display_name} added a calendar event", user, "calendar_event", e.id, {"kind": e.kind})
    db.commit()
    return {"item": crm.event_dicts(db, [e])[0]}


@router.patch("/calendar/events/{event_id}")
def patch_calendar_event(event_id: int, body: EventPatch, user: User = Depends(require("calendar.edit")), db: Session = Depends(get_db)):
    e = db.get(CalendarEvent, event_id)
    if e is None:
        raise HTTPException(status_code=404, detail="No such event")
    if e.source == "import":
        raise HTTPException(status_code=409, detail="Imported appointments are read only. Change them in ImproMed.")
    ch = body.model_dump(exclude_unset=True)
    for key in ("title", "starts_at", "ends_at", "status"):
        if key in ch and ch[key] is None:
            raise HTTPException(status_code=400, detail="That field cannot be empty.")
    if "starts_at" in ch:
        e.starts_at = crm.aware(ch["starts_at"])
    if "ends_at" in ch:
        e.ends_at = crm.aware(ch["ends_at"])
    if e.ends_at <= e.starts_at:
        raise HTTPException(status_code=400, detail="The end must be after the start.")
    for key in ("title", "staff_user_id", "status"):
        if key in ch:
            setattr(e, key, ch[key])
    ev.record(db, ev.CONTACTS, "event_updated", f"{user.display_name} changed a calendar event", user, "calendar_event", e.id, {"status": e.status})
    db.commit()
    return {"item": crm.event_dicts(db, [e])[0]}


# ---------------------------------------------------------------- access log (admin)
@router.get("/admin/access-log")
def access_log(
    user_id: int | None = None,
    contact_id: int | None = None,
    action: Literal["view", "export", "erase", "send"] | None = None,
    page: int = Query(default=1, ge=1, le=100000),
    per_page: int = Query(default=50, ge=10, le=100),
    user: User = Depends(require("admin.access_log")),
    db: Session = Depends(get_db),
):
    stmt = select(AccessLog)
    if user_id is not None:
        stmt = stmt.where(AccessLog.user_id == user_id)
    if contact_id is not None:
        stmt = stmt.where(AccessLog.contact_id == contact_id)
    if action:
        stmt = stmt.where(AccessLog.action == action)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(AccessLog.at.desc(), AccessLog.id.desc()).limit(per_page).offset((page - 1) * per_page)).all()
    uids = {r.user_id for r in rows if r.user_id}
    cids = {r.contact_id for r in rows if r.contact_id}
    users = {u.id: u for u in db.scalars(select(User).where(User.id.in_(uids))).all()} if uids else {}
    contacts = {c.id: c for c in db.scalars(select(Contact).where(Contact.id.in_(cids))).all()} if cids else {}
    items = [{
        "id": r.id, "action": r.action, "at": timeutil.iso(r.at), "ip": r.ip, "detail": r.detail,
        "user": {"id": r.user_id, "display_name": users[r.user_id].display_name} if r.user_id in users else None,
        "contact": {"id": r.contact_id, "display_name": contacts[r.contact_id].display_name} if r.contact_id in contacts else None,
    } for r in rows]
    return {"items": items, "total": total, "page": page, "per_page": per_page}
