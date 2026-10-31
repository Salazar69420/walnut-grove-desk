"""Bookable hours (for booking by text), confirming a text booking, and the product list for suggestions."""
from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.aftercare import templates as T
from app.api.schemas import Strict
from app.assistant import booking, upsell
from app.db import get_db
from app.models import CalendarEvent, Contact, Pet, Product, SmsOffer, Task, User, VisitType
from app.security.auth import require
from app.services import crm, sender, settings_store, timeutil
from app.services import events as ev

router = APIRouter(prefix="/api", tags=["booking"])


# ---------------------------------------------------------------- bookable hours
@router.get("/calendar/booking-hours")
def get_hours(user: User = Depends(require("calendar.view")), db: Session = Depends(get_db)):
    cfg = booking.get_hours(db)
    preview = [{"start": booking.timeutil.iso(t), "label": booking.label(t)} for t in booking.free_times(db, 30, n=6)]
    return {**cfg, "preview": preview}


class HoursIn(Strict):
    enabled: bool | None = None
    slot_minutes: int | None = Field(default=None, ge=10, le=120)
    lead_minutes: int | None = Field(default=None, ge=0, le=2880)
    horizon_days: int | None = Field(default=None, ge=1, le=60)
    days: dict[Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"], list[list[str]]] | None = None
    closed_dates: list[str] | None = Field(default=None, max_length=100)


@router.put("/calendar/booking-hours")
def put_hours(body: HoursIn, user: User = Depends(require("afterhours.edit")), db: Session = Depends(get_db)):
    try:
        cfg = booking.put_hours(db, body.model_dump(exclude_none=True), user.display_name)
    except ValueError:
        raise HTTPException(status_code=400, detail="Dates look like 2026-12-25.")
    ev.record(db, ev.ADMIN, "booking_hours_changed", f"{user.display_name} changed the bookable hours ({'on' if cfg['enabled'] else 'off'})", user, "setting", booking.KEY)
    db.commit()
    return get_hours(user, db)


class ConfirmIn(Strict):
    notify: bool = False


def _text_confirmation(db: Session, e: CalendarEvent, user: User) -> str | None:
    if not e.contact_id:
        return None
    c = db.get(Contact, e.contact_id)
    pet = db.get(Pet, e.pet_id) if e.pet_id else None
    first = (T.first_name(c.display_name) or "there")
    try:
        m = sender.send_to_contact(db, c, user, channel="sms", body=str(T.booking_confirmed(first, booking.label(crm.aware(e.starts_at)), pet.name if pet else None)))
        return m.delivery_state
    except crm.CrmError as x:
        return f"not sent: {x.detail}"


def _close_booking_tasks(db: Session, e: CalendarEvent, user: User) -> None:
    """The "confirm text/phone booking" task for this person is finished once the appointment is confirmed."""
    if not e.contact_id:
        return
    for t in db.scalars(select(Task).where(Task.contact_id == e.contact_id, Task.kind == "booking", Task.status != "done",
                                          Task.title.like("Confirm % booking%"))).all():
        t.status, t.done_at, t.done_by = "done", timeutil.now_utc(), user.id


@router.post("/calendar/events/{event_id}/confirm")
def confirm(event_id: int, body: ConfirmIn, user: User = Depends(require("tasks.work")), db: Session = Depends(get_db)):
    """Any staff member (or the owner) can approve a booking the assistant took by text."""
    e = db.get(CalendarEvent, event_id)
    if e is None:
        raise HTTPException(status_code=404, detail="No such event")
    if e.status != "requested":
        raise HTTPException(status_code=409, detail="Only a requested appointment can be confirmed.")
    if any(o.id != e.id for o in _overlaps(db, crm.aware(e.starts_at), crm.aware(e.ends_at), booked_only=True)):
        raise HTTPException(status_code=409, detail="Another confirmed appointment is already at that time. Move one of them first.")
    e.status = "booked"
    _close_booking_tasks(db, e, user)
    texted = _text_confirmation(db, e, user) if body.notify else None
    ev.record(db, ev.CONTACTS, "booking_confirmed", f"{user.display_name} confirmed an appointment", user, "calendar_event", e.id)
    db.commit()
    return {"item": crm.event_dicts(db, [e])[0], "texted": texted}


@router.post("/calendar/events/{event_id}/decline")
def decline(event_id: int, body: ConfirmIn, user: User = Depends(require("tasks.work")), db: Session = Depends(get_db)):
    """Any staff member can turn down a requested appointment (it is cancelled; the client can be texted)."""
    e = db.get(CalendarEvent, event_id)
    if e is None:
        raise HTTPException(status_code=404, detail="No such event")
    if e.status != "requested":
        raise HTTPException(status_code=409, detail="Only a requested appointment can be declined.")
    e.status = "cancelled"
    for t in db.scalars(select(Task).where(Task.contact_id == e.contact_id, Task.kind == "booking", Task.status != "done",
                                          Task.title.like("Confirm % booking%"))).all():
        t.status, t.done_at, t.done_by = "done", timeutil.now_utc(), user.id
        t.reason = (t.reason + f"\nDeclined by {user.display_name}.")[:2000]
    texted = None
    if body.notify and e.contact_id:
        c = db.get(Contact, e.contact_id)
        first = (T.first_name(c.display_name) or "there")
        try:
            texted = sender.send_to_contact(db, c, user, channel="sms", body=str(T.booking_declined(first, booking.label(crm.aware(e.starts_at))))).delivery_state
        except crm.CrmError as x:
            texted = f"not sent: {x.detail}"
    ev.record(db, ev.CONTACTS, "booking_declined", f"{user.display_name} declined a requested appointment", user, "calendar_event", e.id)
    db.commit()
    return {"item": crm.event_dicts(db, [e])[0], "texted": texted}


# ---------------------------------------------------------------- booking from the app (the Book appointment button)
def _overlaps(db: Session, start: datetime, end: datetime, booked_only: bool = False) -> list[CalendarEvent]:
    states = ("booked",) if booked_only else ("booked", "requested")
    return list(db.scalars(select(CalendarEvent).where(CalendarEvent.status.in_(states), CalendarEvent.starts_at < end, CalendarEvent.ends_at > start)).all())


def _visit_types(db: Session) -> list[dict[str, Any]]:
    return [{"id": v.id, "name": v.name, "minutes": v.minutes} for v in db.scalars(select(VisitType).order_by(VisitType.sort, VisitType.id)).all()]


@router.get("/calendar/open-times")
def open_times(day: date = Query(alias="date"), minutes: int = Query(default=30, ge=10, le=240), user: User = Depends(require("calendar.view")),
               db: Session = Depends(get_db)):
    """Every open start time on one day, inside the clinic's hours and free in the Calendar."""
    cfg = booking.get_hours(db)
    ranges = [] if day.isoformat() in cfg["closed_dates"] else cfg["days"].get(booking.DAYS[day.weekday()], [])
    times = booking.free_times(db, minutes, n=200, day=day, staff=True)
    return {"date": day.isoformat(), "minutes": minutes, "hours": ranges, "closed": not ranges,
            "times": [{"start": timeutil.iso(t), "label": timeutil.local(t).strftime("%I:%M %p").lstrip("0")} for t in times],
            "visit_types": _visit_types(db), "week": {d: cfg["days"].get(d, []) for d in booking.DAYS}}


class BookIn(Strict):
    contact_id: int
    pet_id: int | None = None
    visit_type_id: int | None = None
    starts_at: datetime
    minutes: int = Field(default=30, ge=10, le=240)
    note: str = Field(default="", max_length=200)
    notify: bool = False


@router.post("/calendar/book", status_code=201)
def book(body: BookIn, user: User = Depends(require("tasks.work")), db: Session = Depends(get_db)):
    """A staff member books a confirmed appointment straight into the schedule."""
    c = db.get(Contact, body.contact_id)
    if c is None or c.erased_at is not None:
        raise HTTPException(status_code=404, detail="No such contact")
    pet = db.get(Pet, body.pet_id) if body.pet_id else None
    if body.pet_id and (pet is None or pet.contact_id != c.id):
        raise HTTPException(status_code=400, detail="That pet does not belong to this person.")
    vt = db.get(VisitType, body.visit_type_id) if body.visit_type_id else None
    start = crm.aware(body.starts_at)
    if start < timeutil.now_utc():
        raise HTTPException(status_code=400, detail="That time has already passed.")
    minutes = vt.minutes if vt else body.minutes
    end = start + timedelta(minutes=minutes)
    if start not in booking.free_times(db, minutes, n=200, day=timeutil.local(start).date(), staff=True):
        raise HTTPException(status_code=409, detail="That time is not open any more (outside hours or already taken). Pick another time.")
    visit = vt.name if vt else "Appointment"
    title = f"{visit}: {c.display_name}{(' and ' + pet.name) if pet else ''}"
    if body.note.strip():
        title = f"{title} ({body.note.strip()})"
    e = CalendarEvent(kind=booking.KIND_FOR_VISIT.get(visit, "checkup"), classification=c.kind, title=title[:200], contact_id=c.id,
                      pet_id=pet.id if pet else None, staff_user_id=user.id, starts_at=start, ends_at=end, status="booked", source="app")
    db.add(e)
    db.flush()
    for o in db.scalars(select(SmsOffer).where(SmsOffer.contact_id == c.id, SmsOffer.status == "offered")).all():
        o.status = "expired"  # the person is booked now; old texted options no longer apply
    texted = _text_confirmation(db, e, user) if body.notify else None
    ev.record(db, ev.CONTACTS, "appointment_booked", f"{user.display_name} booked {visit.lower()} for {c.display_name} on {booking.label(start)}",
              user, "calendar_event", e.id, {"contact_id": c.id})
    db.commit()
    return {"item": crm.event_dicts(db, [e])[0], "texted": texted, "label": booking.label(start)}


# ---------------------------------------------------------------- products and suggestions
def product_dict(p: Product) -> dict[str, Any]:
    return {"id": p.id, "name": p.name, "price_cents": p.price_cents, "unit": p.unit, "pitch": p.pitch, "triggers": p.triggers,
            "species": p.species, "status": p.status, "source": p.source, "line": upsell.line(p)}


class ProductIn(Strict):
    name: str = Field(min_length=2, max_length=160)
    price_dollars: float | None = Field(default=None, ge=0, le=100000)
    unit: str = Field(default="", max_length=40)
    pitch: str = Field(default="", max_length=300)
    triggers: str = Field(default="", max_length=400)
    species: Literal["", "dog", "cat"] = ""


class ProductPatch(Strict):
    name: str | None = Field(default=None, min_length=2, max_length=160)
    price_dollars: float | None = Field(default=None, ge=0, le=100000)
    unit: str | None = Field(default=None, max_length=40)
    pitch: str | None = Field(default=None, max_length=300)
    triggers: str | None = Field(default=None, max_length=400)
    species: Literal["", "dog", "cat"] | None = None


@router.get("/products")
def list_products(status: Literal["pending", "approved", "retired", "all"] = "all", user: User = Depends(require("knowledge.view")), db: Session = Depends(get_db)):
    stmt = select(Product) if status == "all" else select(Product).where(Product.status == status)
    return {"items": [product_dict(p) for p in db.scalars(stmt.order_by(Product.status, Product.name)).all()], "upsell": upsell.get(db)}


@router.post("/products", status_code=201)
def add_product(body: ProductIn, user: User = Depends(require("knowledge.suggest")), db: Session = Depends(get_db)):
    p = Product(name=body.name, price_cents=None if body.price_dollars is None else int(round(body.price_dollars * 100)), unit=body.unit,
                pitch=body.pitch, triggers=body.triggers, species=body.species, status="pending", source="manual")
    db.add(p)
    db.flush()
    ev.record(db, ev.KNOWLEDGE, "product_proposed", f"{user.display_name} added a product: {p.name}", user, "fee", p.id)
    db.commit()
    return {"item": product_dict(p)}


@router.patch("/products/{pid}")
def edit_product(pid: int, body: ProductPatch, user: User = Depends(require("knowledge.suggest")), db: Session = Depends(get_db)):
    p = db.get(Product, pid)
    if p is None:
        raise HTTPException(status_code=404, detail="No such product")
    if p.status != "pending":
        raise HTTPException(status_code=409, detail="Only a pending product can be edited. Retire it and add a new one.")
    ch = body.model_dump(exclude_unset=True)
    if "price_dollars" in ch:
        v = ch.pop("price_dollars")
        p.price_cents = None if v is None else int(round(v * 100))
    for k, v in ch.items():
        if v is not None:
            setattr(p, k, v)
    db.commit()
    return {"item": product_dict(p)}


@router.post("/products/{pid}/{action}")
def decide_product(pid: int, action: Literal["approve", "retire", "decline"], user: User = Depends(require("knowledge.decide")), db: Session = Depends(get_db)):
    p = db.get(Product, pid)
    if p is None:
        raise HTTPException(status_code=404, detail="No such product")
    if action == "approve" and not p.pitch.strip():
        raise HTTPException(status_code=400, detail="Add the sentence the assistant should say before approving.")
    p.status = "approved" if action == "approve" else "retired"
    ev.record(db, ev.KNOWLEDGE, f"product_{action}", f"{user.display_name} {action}d the product {p.name}", user, "fee", p.id)
    db.commit()
    return {"item": product_dict(p)}


PRICE_RX = re.compile(r"[-+]?\d+(?:[.,]\d{1,2})?")


@router.post("/products/import")
async def import_products(file: UploadFile = File(...), user: User = Depends(require("knowledge.suggest")), db: Session = Depends(get_db)):
    """A CSV with a header row. Columns (any order, extra ones ignored): name, price, unit, pitch, triggers, species. Rows arrive pending."""
    raw = await file.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="The file is larger than 2 MB.")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    rows = list(csv.DictReader(io.StringIO(text)))
    norm = lambda d: {(k or "").strip().lower(): (v or "").strip() for k, v in d.items()}  # noqa: E731
    added = skipped = 0
    for r in (norm(x) for x in rows[:2000]):
        name = (r.get("name") or r.get("product") or r.get("item") or "")[:160]
        if len(name) < 2 or db.scalar(select(Product.id).where(Product.name == name)) is not None:
            skipped += 1
            continue
        m = PRICE_RX.search(r.get("price") or "")
        price = int(round(float(m.group(0).replace(",", ".")) * 100)) if m else None
        sp = (r.get("species") or "").lower()
        db.add(Product(name=name, price_cents=price, unit=(r.get("unit") or "")[:40], pitch=(r.get("pitch") or r.get("description") or "")[:300],
                       triggers=(r.get("triggers") or r.get("tags") or r.get("category") or "")[:400], species=sp if sp in ("dog", "cat") else "",
                       status="pending", source="file"))
        added += 1
    ev.record(db, ev.KNOWLEDGE, "products_imported", f"{user.display_name} imported {added} products for review", user, "fee", None, {"added": added})
    db.commit()
    return {"added": added, "skipped": skipped}


class UpsellIn(Strict):
    enabled: bool
    days_between: int = Field(default=14, ge=1, le=365)


@router.put("/upsell")
def put_upsell(body: UpsellIn, user: User = Depends(require("knowledge.decide")), db: Session = Depends(get_db)):
    settings_store.put_raw(db, upsell.KEY, {"enabled": body.enabled, "days_between": body.days_between}, by=user.display_name)
    ev.record(db, ev.KNOWLEDGE, "upsell_changed", f"{user.display_name} turned suggestions {'on' if body.enabled else 'off'}", user, "setting", upsell.KEY)
    db.commit()
    return upsell.get(db)
