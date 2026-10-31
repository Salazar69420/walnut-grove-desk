"""Contacts, pets, associated contacts, CSV export and erase (docs/CONTRACT_CRM_P1.md)."""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, Response
from pydantic import Field, model_validator
from sqlalchemy import and_, delete, exists, func, not_, or_, select, text, update
from sqlalchemy.orm import Session

from app import db as app_db
from app.api.schemas import Strict
from app.db import get_db
from app.models import (
    AccessLog,
    Attachment,
    CalendarEvent,
    CampaignRecipient,
    Contact,
    ContactLink,
    ContactNoteHistory,
    Message,
    Note,
    NoteMention,
    Notification,
    Outbox,
    Pet,
    PhoneCall,
    Reminder,
    Task,
    User,
    Visit,
)
from app.security.auth import require
from app.services import attachments as att_store
from app.services import crm
from app.services import events as ev
from app.services import timeutil
from app.services.csvutil import to_csv

router = APIRouter(prefix="/api", tags=["contacts"])

Kind = Literal["inquiry", "patient"]
Consent = Literal["full", "transactional", "none", "unsubscribed"]
Source = Literal["phone", "walk_in", "website", "referral", "after_hours", "import", "other"]
Relationship = Literal["friend", "spouse", "partner", "parent", "child", "neighbour", "family", "caregiver", "other"]


# ---------------------------------------------------------------- bodies
class PetIn(Strict):
    name: str = Field(min_length=1, max_length=80)
    species: str = Field(min_length=1, max_length=40)
    breed: str = Field(default="", max_length=80)
    sex: Literal["male", "female", "unknown", ""] = ""
    birth_date: date | None = None
    weight_kg: float | None = Field(default=None, gt=0, lt=250)
    notes: str = Field(default="", max_length=2000)


class PetPatch(Strict):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    species: str | None = Field(default=None, min_length=1, max_length=40)
    breed: str | None = Field(default=None, max_length=80)
    sex: Literal["male", "female", "unknown", ""] | None = None
    birth_date: date | None = None
    weight_kg: float | None = Field(default=None, gt=0, lt=250)
    notes: str | None = Field(default=None, max_length=2000)
    status: Literal["active", "deceased", "moved"] | None = None


class ContactIn(Strict):
    display_name: str = Field(min_length=2, max_length=120)
    kind: Kind
    email: str | None = Field(default=None, max_length=254)
    phone: str | None = Field(default=None, max_length=40)
    email_consent: Consent
    sms_consent: Consent
    consent_source: str = Field(default="", max_length=200)
    source: Source
    owner_id: int | None = None
    preference_notes: str = Field(default="", max_length=2000)
    pets: list[PetIn] = Field(default_factory=list, max_length=10)


class ContactPatch(Strict):
    display_name: str | None = Field(default=None, min_length=2, max_length=120)
    kind: Kind | None = None
    email: str | None = Field(default=None, max_length=254)
    phone: str | None = Field(default=None, max_length=40)
    email_consent: Consent | None = None
    sms_consent: Consent | None = None
    consent_source: str | None = Field(default=None, max_length=200)
    owner_id: int | None = None
    ai_enabled: bool | None = None
    preference_notes: str | None = Field(default=None, max_length=2000)
    source: Source | None = None


class NewContactIn(Strict):
    display_name: str = Field(min_length=2, max_length=120)
    phone: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=254)
    kind: Kind = "inquiry"


class LinkIn(Strict):
    relationship: Relationship
    note: str = Field(default="", max_length=300)
    related_contact_id: int | None = None
    new_contact: NewContactIn | None = None

    @model_validator(mode="after")
    def _one_of(self):
        if (self.related_contact_id is None) == (self.new_contact is None):
            raise ValueError("Send either related_contact_id or new_contact")
        return self


# ---------------------------------------------------------------- helpers
def _get_contact(db: Session, contact_id: int) -> Contact:
    c = db.get(Contact, contact_id)
    if c is None or c.erased_at is not None:
        raise HTTPException(status_code=404, detail="No such contact")
    return c


def _clean_phone(raw: str | None) -> str | None:
    try:
        return crm.normalize_phone(raw)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


def _clean_email(raw: str | None) -> str | None:
    try:
        return crm.normalize_email(raw)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


def _duplicate(db: Session, phone: str | None, email: str | None, exclude_id: int | None = None) -> JSONResponse | None:
    """A 409 response naming the existing contact when the phone or email is already taken."""
    for column, value, word in ((Contact.phone, phone, "phone"), (Contact.email, email, "email")):
        if not value:
            continue
        stmt = select(Contact.id).where(column == value)
        if exclude_id is not None:
            stmt = stmt.where(Contact.id != exclude_id)
        found = db.scalar(stmt.limit(1))
        if found:
            return JSONResponse(status_code=409, content={"detail": f"A contact with this {word} already exists", "existing_id": found})
    return None


def _check_owner(db: Session, owner_id: int | None) -> None:
    if owner_id is None:
        return
    u = db.get(User, owner_id)
    if u is None or not u.active:
        raise HTTPException(status_code=400, detail="That person cannot be the owner.")


def _pet_from(body: PetIn, contact_id: int) -> Pet:
    return Pet(contact_id=contact_id, name=body.name, species=body.species.strip().lower(), breed=body.breed, sex=body.sex,
               birth_date=body.birth_date, weight_kg=body.weight_kg, notes=body.notes, status="active")


# ---------------------------------------------------------------- filters shared by list and export
class ContactFilters:
    def __init__(
        self,
        q: str | None = Query(default=None, max_length=80),
        kind: Kind | None = None,
        owner: str | None = Query(default=None, max_length=20),
        email_consent: Consent | None = None,
        sms_consent: Consent | None = None,
        species: str | None = Query(default=None, max_length=40),
        visits_min: int | None = Query(default=None, ge=0, le=10000),
        visits_max: int | None = Query(default=None, ge=0, le=10000),
        last_visit_from: date | None = None,
        last_visit_to: date | None = None,
        has_open_task: bool | None = None,
        ai: Literal["on", "off"] | None = None,
        source: Source | None = None,
        sort: Literal["name", "last_active", "created", "last_visit", "visits"] = "last_active",
        dir: Literal["asc", "desc"] | None = None,
    ):
        self.q, self.kind, self.owner = q, kind, owner
        self.email_consent, self.sms_consent, self.species = email_consent, sms_consent, species
        self.visits_min, self.visits_max = visits_min, visits_max
        self.last_visit_from, self.last_visit_to = last_visit_from, last_visit_to
        self.has_open_task, self.ai, self.source = has_open_task, ai, source
        self.sort = sort
        self.dir = dir or ("asc" if sort == "name" else "desc")


def _visit_count_sq():
    return select(func.count(Visit.id)).where(Visit.contact_id == Contact.id).correlate(Contact).scalar_subquery()


def _last_visit_sq():
    return select(func.max(Visit.visited_at)).where(Visit.contact_id == Contact.id).correlate(Contact).scalar_subquery()


def contacts_statement(f: ContactFilters, user: User):
    stmt = select(Contact).where(Contact.erased_at.is_(None))
    if f.q and f.q.strip():
        q = f.q.strip()
        like = crm.like_pattern(q)
        conds = [Contact.display_name.like(like, escape="\\"), Contact.email.like(like, escape="\\"),
                 exists().where(Pet.contact_id == Contact.id, Pet.name.like(like, escape="\\"))]
        digits = re.sub(r"\D", "", q)
        if len(digits) >= 3:
            conds.append(Contact.phone.like("%" + digits + "%"))
        stmt = stmt.where(or_(*conds))
    if f.kind:
        stmt = stmt.where(Contact.kind == f.kind)
    if f.owner:
        if f.owner == "me":
            stmt = stmt.where(Contact.owner_user_id == user.id)
        elif f.owner == "none":
            stmt = stmt.where(Contact.owner_user_id.is_(None))
        elif f.owner.isdigit():
            stmt = stmt.where(Contact.owner_user_id == int(f.owner))
        else:
            raise HTTPException(status_code=400, detail="Owner must be me, none or a person's number.")
    if f.email_consent:
        stmt = stmt.where(Contact.email_consent == f.email_consent)
    if f.sms_consent:
        stmt = stmt.where(Contact.sms_consent == f.sms_consent)
    if f.species and f.species.strip():
        stmt = stmt.where(exists().where(Pet.contact_id == Contact.id, func.lower(Pet.species) == f.species.strip().lower()))
    vc, lv = _visit_count_sq(), _last_visit_sq()
    if f.visits_min is not None:
        stmt = stmt.where(vc >= f.visits_min)
    if f.visits_max is not None:
        stmt = stmt.where(vc <= f.visits_max)
    if f.last_visit_from:
        stmt = stmt.where(lv >= crm.local_day_start(f.last_visit_from))
    if f.last_visit_to:
        stmt = stmt.where(lv < crm.local_day_start(f.last_visit_to) + timedelta(days=1))
    if f.has_open_task is not None:
        active = exists().where(Task.contact_id == Contact.id, Task.status.in_(crm.ACTIVE_TASK))
        stmt = stmt.where(active if f.has_open_task else not_(active))
    if f.ai:
        stmt = stmt.where(Contact.ai_enabled.is_(f.ai == "on"))
    if f.source:
        stmt = stmt.where(Contact.source == f.source)
    return stmt


def _ordered(stmt, f: ContactFilters):
    asc = f.dir == "asc"
    if f.sort == "name":
        key = func.lower(Contact.display_name)
        order = [key.asc() if asc else key.desc()]
    elif f.sort == "created":
        order = [Contact.created_at.asc() if asc else Contact.created_at.desc()]
    elif f.sort == "visits":
        vc = _visit_count_sq()
        order = [vc.asc() if asc else vc.desc()]
    elif f.sort == "last_visit":
        lv = _last_visit_sq()
        order = [lv.is_(None), lv.asc() if asc else lv.desc()]
    else:
        order = [Contact.last_active_at.asc() if asc else Contact.last_active_at.desc()]
    return stmt.order_by(*order, Contact.id.asc() if asc else Contact.id.desc())


# ---------------------------------------------------------------- list, create
@router.get("/contacts")
def list_contacts(
    f: ContactFilters = Depends(),
    page: int = Query(default=1, ge=1, le=100000),
    per_page: int = Query(default=50, ge=10, le=100),
    user: User = Depends(require("contacts.view")),
    db: Session = Depends(get_db),
):
    base = contacts_statement(f, user)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = db.scalars(_ordered(base, f).limit(per_page).offset((page - 1) * per_page)).all()
    return {"items": crm.contact_rows(db, rows), "total": total, "page": page, "per_page": per_page}


@router.post("/contacts", status_code=201)
def create_contact(body: ContactIn, user: User = Depends(require("contacts.edit")), db: Session = Depends(get_db)):
    phone, email = _clean_phone(body.phone), _clean_email(body.email)
    dup = _duplicate(db, phone, email)
    if dup is not None:
        return dup
    _check_owner(db, body.owner_id)
    now = timeutil.now_utc()
    c = Contact(
        kind=body.kind, display_name=body.display_name, email=email, email_consent=body.email_consent, phone=phone,
        sms_consent=body.sms_consent, consent_source=body.consent_source or "Entered by staff", consent_at=now,
        source=body.source, owner_user_id=body.owner_id, ai_enabled=True, preference_notes=body.preference_notes,
        created_at=now, last_active_at=now, last_read_at=now,
    )
    db.add(c)
    db.flush()
    for p in body.pets:
        db.add(_pet_from(p, c.id))
    ev.record(db, ev.CONTACTS, "contact_created", f"{user.display_name} added a contact", user, "contact", c.id, {"kind": c.kind})
    db.commit()
    return {"item": crm.contact_rows(db, [c])[0]}


# ---------------------------------------------------------------- detail
def _link_items(db: Session, contact_id: int) -> list[dict[str, Any]]:
    links = db.scalars(
        select(ContactLink).where(or_(ContactLink.contact_id == contact_id, ContactLink.related_contact_id == contact_id)).order_by(ContactLink.id)
    ).all()
    other_ids = {(l.related_contact_id if l.contact_id == contact_id else l.contact_id) for l in links}
    others = {c.id: c for c in db.scalars(select(Contact).where(Contact.id.in_(other_ids))).all()} if other_ids else {}
    out = []
    for l in links:
        forward = l.contact_id == contact_id
        other = others.get(l.related_contact_id if forward else l.contact_id)
        if other is None:
            continue
        rel = l.relationship if forward else crm.INVERSE_RELATIONSHIP.get(l.relationship, l.relationship)
        out.append({"id": l.id, "relationship": rel, "note": l.note, "contact": crm.contact_brief(other)})
    return out


@router.get("/contacts/{contact_id}")
def get_contact(contact_id: int, user: User = Depends(require("contacts.view")), db: Session = Depends(get_db)):
    c = _get_contact(db, contact_id)
    row = crm.contact_rows(db, [c])[0]
    row.update(preference_notes=c.preference_notes, consent_source=c.consent_source, consent_at=timeutil.iso(c.consent_at))
    pets = db.scalars(select(Pet).where(Pet.contact_id == c.id).order_by(Pet.id)).all()
    pet_by_id = {p.id: p for p in pets}
    visits = db.scalars(select(Visit).where(Visit.contact_id == c.id).order_by(Visit.visited_at.desc(), Visit.id.desc())).all()
    now = timeutil.now_utc()
    upcoming = db.scalars(
        select(CalendarEvent)
        .where(CalendarEvent.contact_id == c.id, CalendarEvent.starts_at >= now, CalendarEvent.status.in_(("booked", "requested")))
        .order_by(CalendarEvent.starts_at).limit(20)
    ).all()
    tasks = db.scalars(select(Task).where(Task.contact_id == c.id, Task.status.in_(crm.ACTIVE_TASK)).order_by(*crm.task_order())).all()
    atts = db.scalars(select(Attachment).where(Attachment.contact_id == c.id).order_by(Attachment.id.desc())).all()
    counts = {
        "messages": db.scalar(select(func.count()).select_from(Message).where(Message.contact_id == c.id)) or 0,
        "notes": db.scalar(select(func.count()).select_from(Note).where(Note.contact_id == c.id)) or 0,
        "visits": len(visits),
    }
    crm.log_access(db, user, c.id, "view")
    db.commit()
    return {
        "contact": row,
        "pets": [crm.pet_dict(p) for p in pets],
        "links": _link_items(db, c.id),
        "visits": [{"id": v.id, "pet": ({"id": v.pet_id, "name": pet_by_id[v.pet_id].name} if v.pet_id in pet_by_id else None),
                    "visited_at": timeutil.iso(v.visited_at), "kind": v.kind, "summary": v.summary, "source": v.source} for v in visits],
        "upcoming_events": crm.event_dicts(db, upcoming),
        "open_tasks": crm.task_dicts(db, tasks),
        "attachments": [crm.attachment_dict(a) for a in atts],
        "counts": counts,
    }


# ---------------------------------------------------------------- patch
@router.patch("/contacts/{contact_id}")
def patch_contact(contact_id: int, body: ContactPatch, user: User = Depends(require("contacts.edit")), db: Session = Depends(get_db)):
    c = _get_contact(db, contact_id)
    ch = body.model_dump(exclude_unset=True)
    if not ch:
        return {"item": crm.contact_rows(db, [c])[0]}
    for key in ("display_name", "kind", "email_consent", "sms_consent", "source", "ai_enabled", "preference_notes"):
        if key in ch and ch[key] is None:
            raise HTTPException(status_code=400, detail="That field cannot be empty.")
    now = timeutil.now_utc()
    if "phone" in ch:
        ch["phone"] = _clean_phone(ch["phone"])
    if "email" in ch:
        ch["email"] = _clean_email(ch["email"])
    dup = _duplicate(db, ch.get("phone") if "phone" in ch else None, ch.get("email") if "email" in ch else None, exclude_id=c.id)
    if dup is not None:
        return dup
    if "owner_id" in ch:
        _check_owner(db, ch["owner_id"])

    old_kind, old_owner = c.kind, c.owner_user_id
    consent_changed = False
    for key in ("email_consent", "sms_consent"):
        if key in ch and ch[key] != getattr(c, key):
            consent_changed = True
    if "preference_notes" in ch and ch["preference_notes"] != c.preference_notes:
        db.add(ContactNoteHistory(contact_id=c.id, old_text=c.preference_notes, new_text=ch["preference_notes"],
                                  edited_by=user.id, edited_at=now))
    for key in ("display_name", "kind", "email", "phone", "email_consent", "sms_consent", "consent_source", "ai_enabled",
                "preference_notes", "source"):
        if key in ch:
            setattr(c, key, ch[key])
    if "owner_id" in ch:
        c.owner_user_id = ch["owner_id"]
    if consent_changed:
        c.consent_at = now
    db.flush()

    if c.kind != old_kind:
        ev.record(db, ev.CONTACTS, "contact_kind_changed", f"{user.display_name} changed a contact from {old_kind} to {c.kind}", user,
                  "contact", c.id, {"from": old_kind, "to": c.kind})
    if c.owner_user_id != old_owner:
        ev.record(db, ev.CONTACTS, "contact_owner_changed", f"{user.display_name} changed who owns a contact", user, "contact", c.id,
                  {"from": old_owner, "to": c.owner_user_id})
    if consent_changed:
        ev.record(db, ev.CONTACTS, "contact_consent_changed", f"{user.display_name} updated a contact's consent", user, "contact", c.id,
                  {"email": c.email_consent, "sms": c.sms_consent})
    if "ai_enabled" in ch:
        ev.record(db, ev.CONTACTS, "contact_ai_switched", f"{user.display_name} switched a contact's AI assistant {'on' if c.ai_enabled else 'off'}",
                  user, "contact", c.id, {"ai_enabled": c.ai_enabled})
    if not (c.kind != old_kind or c.owner_user_id != old_owner or consent_changed or "ai_enabled" in ch):
        ev.queue_publish(db, "contacts", c.id, None)
    db.commit()
    row = crm.contact_rows(db, [c])[0]
    row.update(preference_notes=c.preference_notes, consent_source=c.consent_source, consent_at=timeutil.iso(c.consent_at))
    return {"item": row}


@router.get("/contacts/{contact_id}/note-history")
def note_history(contact_id: int, user: User = Depends(require("contacts.view")), db: Session = Depends(get_db)):
    _get_contact(db, contact_id)
    rows = db.scalars(select(ContactNoteHistory).where(ContactNoteHistory.contact_id == contact_id)
                      .order_by(ContactNoteHistory.edited_at.desc(), ContactNoteHistory.id.desc())).all()
    uids = {r.edited_by for r in rows if r.edited_by}
    users = {u.id: u for u in db.scalars(select(User).where(User.id.in_(uids))).all()} if uids else {}
    return {"items": [{"id": r.id, "old_text": r.old_text, "new_text": r.new_text, "edited_at": timeutil.iso(r.edited_at),
                       "edited_by": crm.user_brief(users.get(r.edited_by))} for r in rows]}


# ---------------------------------------------------------------- pets
@router.post("/contacts/{contact_id}/pets", status_code=201)
def add_pet(contact_id: int, body: PetIn, user: User = Depends(require("contacts.edit")), db: Session = Depends(get_db)):
    c = _get_contact(db, contact_id)
    pet = _pet_from(body, c.id)
    db.add(pet)
    db.flush()
    ev.record(db, ev.CONTACTS, "pet_added", f"{user.display_name} added a pet to a contact", user, "contact", c.id, {"pet_id": pet.id})
    db.commit()
    return {"item": crm.pet_dict(pet)}


@router.patch("/pets/{pet_id}")
def patch_pet(pet_id: int, body: PetPatch, user: User = Depends(require("contacts.edit")), db: Session = Depends(get_db)):
    pet = db.get(Pet, pet_id)
    if pet is None:
        raise HTTPException(status_code=404, detail="No such pet")
    ch = body.model_dump(exclude_unset=True)
    for key in ("name", "species", "breed", "sex", "notes", "status"):
        if key in ch and ch[key] is None:
            raise HTTPException(status_code=400, detail="That field cannot be empty.")
    if "species" in ch:
        ch["species"] = ch["species"].strip().lower()
    for k, v in ch.items():
        setattr(pet, k, v)
    ev.record(db, ev.CONTACTS, "pet_updated", f"{user.display_name} updated a pet", user, "contact", pet.contact_id, {"pet_id": pet.id})
    db.commit()
    return {"item": crm.pet_dict(pet)}


# ---------------------------------------------------------------- associated contacts
@router.post("/contacts/{contact_id}/links", status_code=201)
def add_link(contact_id: int, body: LinkIn, user: User = Depends(require("contacts.edit")), db: Session = Depends(get_db)):
    c = _get_contact(db, contact_id)
    now = timeutil.now_utc()
    if body.new_contact is not None:
        n = body.new_contact
        phone, email = _clean_phone(n.phone), _clean_email(n.email)
        dup = _duplicate(db, phone, email)
        if dup is not None:
            return dup
        other = Contact(kind=n.kind, display_name=n.display_name, email=email, email_consent="none", phone=phone, sms_consent="none",
                        consent_source="Added as an associated contact", consent_at=now, source="other", created_at=now,
                        last_active_at=now, last_read_at=now)
        db.add(other)
        db.flush()
        ev.record(db, ev.CONTACTS, "contact_created", f"{user.display_name} added a contact", user, "contact", other.id, {"kind": other.kind})
    else:
        other = _get_contact(db, body.related_contact_id)  # type: ignore[arg-type]
        if other.id == c.id:
            raise HTTPException(status_code=400, detail="A contact cannot be linked to themselves.")
        exists_already = db.scalar(select(ContactLink.id).where(
            or_(and_(ContactLink.contact_id == c.id, ContactLink.related_contact_id == other.id),
                and_(ContactLink.contact_id == other.id, ContactLink.related_contact_id == c.id))).limit(1))
        if exists_already:
            raise HTTPException(status_code=409, detail="These two contacts are already linked.")
    link = ContactLink(contact_id=c.id, related_contact_id=other.id, relationship=body.relationship, note=body.note)
    db.add(link)
    db.flush()
    ev.record(db, ev.CONTACTS, "contact_linked", f"{user.display_name} linked two contacts", user, "contact", c.id, {"link_id": link.id})
    ev.queue_publish(db, "contacts", other.id, None)
    db.commit()
    return {"item": next(i for i in _link_items(db, c.id) if i["id"] == link.id)}


@router.delete("/contact-links/{link_id}")
def delete_link(link_id: int, user: User = Depends(require("contacts.edit")), db: Session = Depends(get_db)):
    link = db.get(ContactLink, link_id)
    if link is None:
        raise HTTPException(status_code=404, detail="No such link")
    a, b = link.contact_id, link.related_contact_id
    db.delete(link)
    ev.record(db, ev.CONTACTS, "contact_unlinked", f"{user.display_name} removed a link between contacts", user, "contact", a, {})
    ev.queue_publish(db, "contacts", b, None)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- CSV export
EXPORT_HEADER = ["id", "name", "kind", "email", "phone", "email_consent", "sms_consent", "owner", "source", "visits", "last_visit",
                 "pets", "created_at", "last_active_at"]


@router.get("/export/contacts.csv")
def export_contacts(f: ContactFilters = Depends(), user: User = Depends(require("contacts.export")), db: Session = Depends(get_db)):
    rows = db.scalars(_ordered(contacts_statement(f, user), f).limit(20000)).all()
    shaped = crm.contact_rows(db, rows)
    data = [[r["id"], r["display_name"], r["kind"], r["email"] or "", r["phone"] or "", r["email_consent"], r["sms_consent"],
             r["owner"]["display_name"] if r["owner"] else "", r["source"], r["visit_count"], r["last_visit_at"] or "",
             "; ".join(r["pet_names"]), r["created_at"], r["last_active_at"]] for r in shaped]
    crm.log_access(db, user, None, "export", detail=f"{len(data)} contacts")
    ev.record(db, ev.CONTACTS, "contacts_exported", f"{user.display_name} exported the contact list", user, "contact", "", {"count": len(data)})
    db.commit()
    return Response(
        content=to_csv(EXPORT_HEADER, data), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="contacts.csv"'},
    )


# ---------------------------------------------------------------- erase
def erase_contact_rows(db: Session, contact_id: int) -> list[str]:
    """Delete the contact and every child row. Returns the stored file names to wipe after the commit."""
    msg_ids = list(db.scalars(select(Message.id).where(Message.contact_id == contact_id)).all())
    stored = list(db.scalars(select(Attachment.stored_name).where(Attachment.contact_id == contact_id)).all())
    note_ids = list(db.scalars(select(Note.id).where(Note.contact_id == contact_id)).all())
    if msg_ids:
        db.execute(delete(Outbox).where(Outbox.source_type == "message", Outbox.source_id.in_([str(i) for i in msg_ids])))
    if note_ids:
        db.execute(delete(NoteMention).where(NoteMention.note_id.in_(note_ids)))
    db.execute(delete(Notification).where(Notification.link == f"/contacts/{contact_id}"))
    db.execute(delete(CampaignRecipient).where(CampaignRecipient.contact_id == contact_id))
    db.execute(update(PhoneCall).where(PhoneCall.contact_id == contact_id).values(contact_id=None, phone=None, short_note="", transcript=None))
    db.execute(delete(Attachment).where(Attachment.contact_id == contact_id))
    db.execute(delete(Reminder).where(Reminder.contact_id == contact_id))
    db.execute(delete(Note).where(Note.contact_id == contact_id))
    db.execute(delete(Message).where(Message.contact_id == contact_id))
    db.execute(delete(Visit).where(Visit.contact_id == contact_id))
    db.execute(delete(Task).where(Task.contact_id == contact_id))
    db.execute(delete(CalendarEvent).where(CalendarEvent.contact_id == contact_id))
    db.execute(delete(ContactNoteHistory).where(ContactNoteHistory.contact_id == contact_id))
    db.execute(delete(ContactLink).where(or_(ContactLink.contact_id == contact_id, ContactLink.related_contact_id == contact_id)))
    db.execute(delete(Pet).where(Pet.contact_id == contact_id))
    db.execute(update(AccessLog).where(AccessLog.contact_id == contact_id).values(contact_id=None))
    db.execute(delete(Contact).where(Contact.id == contact_id))
    return stored


@router.post("/admin/contacts/{contact_id}/erase")
def erase_contact(contact_id: int, user: User = Depends(require("admin.contacts_erase")), db: Session = Depends(get_db)):
    c = db.get(Contact, contact_id)
    if c is None:
        raise HTTPException(status_code=404, detail="No such contact")
    db.execute(text("PRAGMA secure_delete=ON"))
    stored = erase_contact_rows(db, contact_id)
    db.flush()
    crm.log_access(db, user, None, "erase", detail=f"contact {contact_id}")
    ev.record(db, ev.CONTACTS, "contact_erased", f"{user.display_name} erased a contact and everything attached to it", user, "contact",
              contact_id, {})
    db.commit()
    for name in stored:
        att_store.remove(name)
    try:  # fold the freed pages out of the write-ahead log too
        raw = app_db.engine.raw_connection()  # type: ignore[union-attr]
        try:
            raw.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            raw.close()
    except Exception:  # pragma: no cover - best effort
        pass
    return {"ok": True}
