"""Turn the callers found in imported calls into contacts, pets and call history (used by pipeline/app_import.py).

Rules, chosen for precision:
* A contact needs a full name (first and last), or a first name plus a pet name. A first name alone is never enough.
* First names that turn up alone in many calls are skipped (they are almost always staff, vendors or regulars we cannot tell apart).
* Everything is created with no phone, no email and NO consent, so nothing can be sent to them, and marked "please check".
* Calls are linked to the contact; each linked call adds one short call line (fixed vocabulary, never call text) to their timeline.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .people import Found

MARK = "Imported from a call recording. No consent recorded."
NOTE = "Imported from call recordings. The name and pet were picked up automatically from what the caller said. Please check them and add a phone number."
PATIENT_INTENTS = {"refill", "results_question", "results_call_out", "pickup_ready", "recall_or_reminder", "check_in", "records_request"}
FREQUENT_FIRST_ONLY = 8


@dataclass
class Info:
    ref: str
    person: Found
    intent: str


def _key(p: Found, frequent: set[str]):
    if p.first and p.last:
        return ("full", p.first.lower(), p.last.lower())
    if p.first and p.pet and p.first.lower() not in frequent:
        return ("fp", p.first.lower(), p.pet.lower())
    return None


def link_contacts(db: Session, infos: list[Info], now: datetime) -> dict[str, int]:
    from app.models import Contact, Message, Pet, PhoneCall
    from app.services import events as ev

    firsts = Counter(i.person.first.lower() for i in infos if i.person.first and not i.person.last)
    frequent = {k for k, v in firsts.items() if v >= FREQUENT_FIRST_ONLY}
    groups: dict[tuple, list[Info]] = defaultdict(list)
    for i in infos:
        k = _key(i.person, frequent)
        if k:
            groups[k].append(i)

    # contacts made by an earlier import, so running again joins them instead of duplicating
    existing: dict[tuple, Contact] = {}
    for c in db.scalars(select(Contact).where(Contact.consent_source == MARK, Contact.erased_at.is_(None))).all():
        parts = c.display_name.split()
        if len(parts) >= 2:
            existing[("full", parts[0].lower(), parts[-1].lower())] = c
        else:
            for p in db.scalars(select(Pet).where(Pet.contact_id == c.id)).all():
                existing[("fp", parts[0].lower(), p.name.lower())] = c

    calls = {c.external_ref: c for c in db.scalars(select(PhoneCall).where(PhoneCall.external_ref.in_({i.ref for g in groups.values() for i in g}))).all()}
    made = linked = pets = 0
    for key, items in groups.items():
        rows = [(i, calls[i.ref]) for i in items if i.ref in calls]
        if not rows:
            continue
        when = [c.started_at for _, c in rows]
        patient = any(i.intent in PATIENT_INTENTS for i, _ in rows)
        contact = existing.get(key)
        first = next((i.person for i, _ in rows if i.person.first), None)
        if contact is None:
            name = f"{first.first} {first.last}" if key[0] == "full" else first.first
            contact = Contact(kind="patient" if patient else "inquiry", display_name=name[:120], email=None, phone=None, email_consent="none", sms_consent="none",
                              consent_source=MARK, source="phone", ai_enabled=False, preference_notes=NOTE, created_at=min(when), last_active_at=max(when),
                              last_read_at=now, is_demo=False)
            db.add(contact)
            db.flush()
            existing[key] = contact
            made += 1
        elif patient and contact.kind == "inquiry":
            contact.kind = "patient"
        pet = next((i.person for i, _ in rows if i.person.pet), None)
        if pet and not db.scalar(select(Pet.id).where(Pet.contact_id == contact.id, Pet.name == pet.pet)):
            db.add(Pet(contact_id=contact.id, name=pet.pet[:80], species=(pet.species or ""), status="active", is_demo=False))
            pets += 1
        for i, call in rows:
            if call.contact_id is not None:
                continue
            call.contact_id = contact.id
            call.call_type = "patient" if contact.kind == "patient" else "inquiry"
            outbound = call.direction == "outbound"
            db.add(Message(contact_id=contact.id, channel="call", direction="out" if outbound else "in", sender_kind="staff" if outbound else "client",
                           body=call.short_note, delivery_state="received", created_at=call.started_at, phone_call_id=call.id, is_demo=False))
            if call.started_at > contact.last_active_at:
                contact.last_active_at = call.started_at
            if call.started_at < contact.created_at:
                contact.created_at = call.started_at
            linked += 1
    if made or linked:
        ev.record(db, ev.CONTACTS, "contacts_imported", f"Imported {made} contacts from call recordings ({linked} calls linked)", "Admin import", "contact", None,
                  {"contacts": made, "calls": linked})
    return {"contacts_created": made, "calls_linked": linked, "pets_added": pets, "groups": len(groups)}
