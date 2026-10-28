"""Glue between the pure engine and the database: build the context, store outcomes."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.llm import make_layer2
from app.aftercare import engine as E
from app.aftercare import keywords as kw
from app.models import AppointmentRequest, Callback, Conversation, EmergencyRouting, Hospital, OpenSlot, VisitType
from app.services import events as ev
from app.services import settings_store, timeutil

SLOT_TIMES = [(9, 30), (11, 0), (14, 0), (15, 30)]
SLOT_DAYS_AHEAD = 14


def generate_slots(today, existing: set[datetime] | None = None) -> list[OpenSlot]:
    """Open slots for the next 14 days on clinic weekdays (Mon to Fri), tomorrow onward."""
    rows = []
    for d in range(1, SLOT_DAYS_AHEAD + 1):
        day = today + timedelta(days=d)
        if day.weekday() > 4:
            continue
        for h, m in SLOT_TIMES:
            start = timeutil.at_local(day, h, m)
            if existing and start in existing:
                continue
            rows.append(OpenSlot(start=start, provider="Dr. Garg", taken=False))
    return rows


def hospital_dict(h: Hospital) -> dict[str, Any]:
    return {"id": h.id, "name": h.name, "place": h.place, "phone": h.phone, "address": h.address,
            "hours_claim": h.hours_claim, "confirmed": h.confirmed, "is_placeholder": h.is_placeholder}


def wording(db: Session) -> dict[str, str]:
    from app.seed import reference_data as ref
    w = dict(ref.WORDING)
    stored = settings_store.get(db, "wording", None)
    if isinstance(stored, dict):
        w.update({k: v for k, v in stored.items() if k in w and isinstance(v, str)})
    return w


def build_ctx(db: Session, *, use_layer2: bool = True) -> E.Ctx:
    now = timeutil.now_utc()
    s = settings_store.get_all(db)
    vts = [{"id": v.id, "name": v.name, "minutes": v.minutes, "bookable": v.bookable}
           for v in db.scalars(select(VisitType).order_by(VisitType.sort, VisitType.id))]
    slots = [{"id": o.id, "start": o.start, "visit_type_id": o.visit_type_id, "taken": o.taken, "label": timeutil.slot_display(o.start)}
             for o in db.scalars(select(OpenSlot).where(OpenSlot.taken.is_(False), OpenSlot.start > now).order_by(OpenSlot.start).limit(200))]
    hospitals = [hospital_dict(h) for h in db.scalars(select(Hospital).order_by(Hospital.sort, Hospital.id))]
    return E.Ctx(
        clinic_name=s["clinic_name"],
        toggles={"req": bool(s["tog_req"]), "msg": bool(s["tog_msg"]), "missed": bool(s["tog_missed"])},
        visit_types=vts, slots=slots, hospitals=hospitals, keywords=kw.load_keywords(db),
        layer2=make_layer2(db) if use_layer2 else None, now=now, opening_line=wording(db).get("opening_line"),
    )


def _label_for(kind: str) -> str:
    return {"booking": "appointment request", "callback": "callback", "emergency": "emergency routing"}[kind]


def persist_outcome(
    db: Session,
    outcome: dict[str, Any],
    messages: list[dict],
    *,
    channel: str,
    source: str,  # sim | retell
    is_test: bool,
    actor: Any = None,
    client_label: str = "You (test)",
    origin: str = "a test client",
    created_by: int | None = None,
) -> Conversation:
    """Write the conversation plus appointment request, callback or emergency routing, and the event."""
    now = timeutil.now_utc()
    kind = outcome["kind"]
    pet = outcome.get("pet") or "Pet not named"
    slot_label = None
    slot_row = None
    if kind == "booking":
        slot_row = db.get(OpenSlot, outcome.get("slot_id")) if outcome.get("slot_id") else None
        if slot_row is None or slot_row.start <= now or slot_row.taken:
            # the opening is gone: never record a booking for a past or taken slot
            outcome = {**outcome, "kind": "callback", "callback_kind": "Booking",
                       "summary": "Wanted to book, but the opening was no longer free. " + outcome.get("summary", "")}
            kind = "callback"
            slot_row = None
        else:
            slot_label = timeutil.slot_display(slot_row.start)
    conv = Conversation(
        channel=channel, client_label=client_label, pet_label=pet, kind=kind, status="new", preferred_slot=slot_label,
        summary=outcome.get("summary", ""), messages_json=json.dumps(messages), source=source, is_test=is_test,
        created_by=created_by, created_at=now,
    )
    db.add(conv)
    db.flush()
    if kind == "booking":
        db.add(AppointmentRequest(conversation_id=conv.id, visit_type_id=outcome.get("visit_type_id"),
                                  slot_start=slot_row.start if slot_row else None, status="requested", created_at=now))
    elif kind == "emergency":
        db.add(EmergencyRouting(conversation_id=conv.id, hospital_id=outcome.get("hospital_id"),
                                matched_terms=json.dumps(outcome.get("matched", [])), layer=outcome.get("layer") or "1", created_at=now))
    else:
        cb_kind = outcome.get("callback_kind", "Message")
        db.add(Callback(
            client_label=client_label, pet_label=pet if pet != "Pet not named" else "", kind=cb_kind,
            what=f"{outcome.get('summary', '')} Taken overnight by the assistant.".strip(),
            via="Call" if channel == "call" else "Text", received_at=now, priority="normal", red_flag=False,
            status="new", source=source, conversation_id=conv.id,
        ))
        db.flush()
    ev.record(db, ev.AFTER_HOURS, f"new_{kind}", f"New {_label_for(kind)} from {origin}", actor, "conversation", conv.id,
              {"source": source, "is_test": is_test})
    if kind == "callback":
        cb = db.scalar(select(Callback).where(Callback.conversation_id == conv.id))
        if cb is not None:
            ev.queue_publish(db, "callbacks", cb.id, None)
    return conv
