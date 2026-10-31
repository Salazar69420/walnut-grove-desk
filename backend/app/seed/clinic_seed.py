"""Demo rows for Knowledge & Fees, Phone and Campaigns (all marked is_demo, all invented or clearly "heard in calls, unconfirmed").

Nothing here is the clinic's real price or policy. The pending items that mention hours and prices come from the answer sheet
draft (docs/ANSWER_SHEET_DRAFT.md) and say so; they need Dr. Garg to confirm them before they mean anything.
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import Campaign, CampaignRecipient, Contact, Fee, KnowledgeEntry, PhoneCall, User
from app.services import campaigns as cp
from app.services import timeutil

CLINIC_TABLES = [CampaignRecipient, Campaign, PhoneCall, Fee, KnowledgeEntry]

# (category, title, body)
APPROVED = [
    ("clinic_info", "Where we are", "Example: the clinic is in Langley, British Columbia. Free parking is behind the building."),
    ("clinic_info", "Weekday hours", "Example: Monday to Friday, 8:30 am to 6 pm. Closed on statutory holidays."),
    ("clinic_info", "Emergencies after hours", "After hours the assistant does not give medical advice. It tells the caller to contact the nearest emergency veterinarian and the clinic follows up in the morning."),
    ("patient_knowledge", "Fasting before surgery", "Example: no food after 10 pm the night before surgery. Water is fine until the morning. The nurse confirms this when the surgery is booked."),
    ("patient_knowledge", "Records requests", "Example: records are sent by email once the owner asks in writing. Allow two working days."),
    ("patient_communication", "Calling about lab results", "Example: a nurse calls with results after the doctor has reviewed them. The assistant never reads out a result."),
    ("patient_communication", "Medicine refills", "Example: refills need a doctor's approval and take one working day. The assistant takes the request and raises a task."),
    ("inquiry_communication", "New client first visit", "Example: new clients are asked for their pet's name, species, age and the reason for the visit. They are told a person confirms the booking."),
    ("inquiry_communication", "Do you take new patients?", "Example: yes. Offer the next open new-patient slot and raise a booking task for the front desk."),
    ("laws_regulations", "No medical advice from the assistant", "The assistant never diagnoses, names a dose or says a pet is fine. Any medical question becomes a task for a person."),
    ("laws_regulations", "Consent for messages", "Texts and emails need recorded consent. Reminders for existing patients may go to anyone with care-only consent. Promotions need full consent. Everyone can opt out."),
    ("specials", "Dental month", "Example: a discount on dental cleanings in February. The front desk confirms the exact amount."),
]

# Pending: (category, title, body, proposed_by, source_kind, source_ref, quote, confidence, replaces_title or None)
PENDING = [
    ("clinic_info", "Weekday hours", "Heard in calls, needs the clinic to confirm: weekdays open until 7 pm, reopening at 8:30 am. Saturday and Sunday 9 am to 5 pm.",
     "ai", "call", "recorded calls, weekdays", "Staff say \"we're open till seven tonight\" on several calls.", 72, "Weekday hours"),
    ("fees", "Office visit", "Heard in calls, needs the clinic to confirm: an office visit is 47 dollars plus tax. One call said 30 dollars each, possibly a vaccine visit.",
     "import", "answer_sheet", "Answer sheet draft", "Two different figures were heard for a basic exam.", 55, None),
    ("fees", "Microchip", "Heard in calls, needs the clinic to confirm: a microchip is 58 dollars plus tax.", "import", "answer_sheet", "Answer sheet draft", "", 60, None),
    ("fees", "Nail trim", "Heard in calls, needs the clinic to confirm: a nail trim is 28.50 dollars.", "import", "answer_sheet", "Answer sheet draft", "", 60, None),
    ("patient_knowledge", "Fasting before surgery", "Example: no food after 8 pm the night before surgery, and no water after midnight. Confirm with the nurse when booking.",
     "ai", "conversation", "after-hours chat", "Client asked \"when should I stop feeding her before Thursday?\"", 64, "Fasting before surgery"),
    ("patient_communication", "Pickup after surgery", "Example: pets are usually ready for pickup between 3 and 5 pm. A nurse calls the owner first and goes over the discharge sheet.",
     "ai", "call", "recorded calls, this week", "\"The nurse will call you when she's ready, usually mid-afternoon.\"", 81, None),
    ("patient_communication", "Insurance claims", "Example: the clinic can fill in a claim form on request. The owner submits it and pre-existing conditions are decided by the insurer.",
     "ai", "call", "recorded calls, last week", "A client asked who files the claim.", 58, None),
    ("inquiry_communication", "Bringing a stool sample", "Example: new puppies and kittens should bring a fresh stool sample in a sealed bag to the first visit.",
     "ai", "call", "recorded calls, this week", "\"Do I need to bring anything for the puppy?\"", 77, None),
    ("laws_regulations", "Prescription records", "Example: a prescription can only be refilled for a patient seen within the last year. A person checks this before the refill.",
     "ai", "conversation", "after-hours chat", "", 49, None),
    ("clinic_info", "Reception phone line", "Example: calls to the main line after 6 pm go to the after-hours assistant.", "human", "manual", "Added by the desk", "", None, None),
]

FEES = [
    ("Office visit", 4700, "", "per visit", "Heard in calls, needs the clinic to confirm. One call said 30 dollars.", "pending", "import"),
    ("Microchip", 5800, "", "", "Heard in calls, needs the clinic to confirm.", "pending", "import"),
    ("Nail trim", 2850, "", "", "Heard in calls, needs the clinic to confirm.", "pending", "import"),
    ("Dental cleaning", 38000, "from", "", "Example only. A doctor gives the real estimate after an exam.", "approved", "human"),
    ("Booster vaccine", 6500, "", "per vaccine", "Example only, not the clinic's real price.", "approved", "human"),
]

NOTES = {
    "booked": ["Booked a check-up for next week.", "Booked a vaccine visit.", "Rebooked a surgery drop-off."],
    "answered": ["Asked about the next free appointment.", "Wanted to update a phone number.", "Asked about opening hours."],
    "info_given": ["Asked about hours and parking.", "Asked what to bring to the first visit.", "Asked how long records take."],
    "callback_task": ["Wants the doctor to call about a lab result.", "Asked for a refill. Task raised for the nurse.", "Asked about a bill. Task raised for the desk."],
    "voicemail": ["Left a voicemail about pickup time.", "Left a voicemail asking to rebook."],
    "missed": ["Missed call. Nobody left a message."],
    "emergency_routed": ["Possible emergency. Told to contact the nearest emergency vet. Task raised."],
}
TALK = [
    "Caller: Hi, I need to book Biscuit in for her shots.\nAgent: Of course. I have Thursday at 10:30. Does that work?\nCaller: Yes, that is great.\nAgent: You are booked. We will send a text reminder.",
    "Caller: What time do you close tonight?\nAgent: Tonight we are open until six. Is there something I can help with?\nCaller: No, that was it. Thanks.",
    "Caller: I need a refill on my cat's pills.\nAgent: I can take that request. A nurse will check with the doctor and call you back tomorrow.\nCaller: Thank you.",
]


def wipe_clinic_demo(db: Session) -> None:
    for model in CLINIC_TABLES:
        db.execute(delete(model).where(model.is_demo.is_(True)))
    db.flush()


def _call_rows(now: datetime, contacts: list[Contact], rng: random.Random) -> list[PhoneCall]:
    today = timeutil.today_local(now)
    rows: list[PhoneCall] = []
    for back in range(8):
        day = today - timedelta(days=back)
        if day.weekday() == 6:
            continue
        for _ in range(rng.randint(7, 12)):
            hour = rng.choices([8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20], weights=[3, 8, 7, 7, 5, 6, 6, 7, 6, 4, 3, 2, 1])[0]
            when = timeutil.at_local(day, hour, rng.randint(0, 59))
            if when > now - timedelta(minutes=5):
                continue
            after = hour >= 18 or day.weekday() >= 5
            handled = rng.choices(["human", "ai", "none"], weights=([12, 1, 2] if not after else [1, 12, 3]))[0]
            direction = "outbound" if handled == "human" and rng.random() < 0.28 else "inbound"
            if handled == "none":
                result = rng.choice(["missed", "voicemail"])
            elif handled == "ai":
                result = rng.choice(["booked", "callback_task", "info_given", "info_given", "emergency_routed" if rng.random() < 0.12 else "callback_task"])
            else:
                result = rng.choice(["answered", "booked", "info_given", "callback_task", "answered"])
            c = rng.choice(contacts) if contacts and rng.random() < 0.78 else None
            n = rng.randint(10, 99)
            dur = 0 if handled == "none" and result == "missed" else rng.randint(25, 95) if result == "voicemail" else rng.randint(40, 420)
            rows.append(PhoneCall(
                started_at=when, direction=direction, handled_by=handled,
                agent_label="AI assistant" if handled == "ai" else rng.choice(["Priya", "Marcus", "Anita"]) if handled == "human" else "",
                contact_id=c.id if c else None, phone=c.phone if c else f"+160455501{n:02d}",
                duration_seconds=dur, result=result, call_type=("patient" if c and c.kind == "patient" else "inquiry") if c else "unknown",
                short_note=rng.choice(NOTES[result]), transcript=rng.choice(TALK) if handled != "none" and dur > 60 and rng.random() < 0.4 else None,
                source="demo", is_demo=True,
            ))
    return rows


def seed_clinic(db: Session, now: datetime) -> dict[str, int]:
    rng = random.Random(20261031)
    users = {u.display_name: u.id for u in db.scalars(select(User)).all()}
    approved: dict[str, KnowledgeEntry] = {}
    for cat, title, body in APPROVED:
        e = KnowledgeEntry(category=cat, title=title, body=body, status="approved", proposed_by="human", source_kind="manual", source_ref="Set up by the clinic (example)",
                           proposed_at=now - timedelta(days=40), decided_at=now - timedelta(days=39), decided_by=users.get("Dr. Garg"), is_demo=True)
        db.add(e)
        approved[title] = e
    db.flush()
    for i, (cat, title, body, by, kind, ref, quote, conf, replaces) in enumerate(PENDING):
        old = approved.get(replaces) if replaces else None
        db.add(KnowledgeEntry(category=cat, title=title, body=body, status="pending", proposed_by=by, source_kind=kind, source_ref=ref, source_quote=quote,
                              confidence=conf, supersedes_id=old.id if old else None, previous_body=old.body if old else "", conflicts=old is not None,
                              proposed_at=now - timedelta(days=i % 6, hours=i * 3 + 1), is_demo=True))
    declined = KnowledgeEntry(category="patient_knowledge", title="Dose for a headache pill", body="A client asked what dose of a human pain pill is safe. (Declined: the assistant never gives doses.)",
                              status="declined", proposed_by="ai", source_kind="conversation", source_ref="after-hours chat", confidence=40,
                              proposed_at=now - timedelta(days=5), decided_at=now - timedelta(days=4), decided_by=users.get("Dr. Garg"),
                              decline_reason="Medical advice. The assistant must pass this to a person.", is_demo=True)
    db.add(declined)
    for service, cents, prefix, unit, note, status, by in FEES:
        db.add(Fee(service=service, price_cents=cents, price_prefix=prefix, unit=unit, note=note, status=status, proposed_by=by,
                   effective_from=timeutil.today_local(now) - timedelta(days=30) if status == "approved" else None,
                   decided_by=users.get("Dr. Garg") if status == "approved" else None, decided_at=now - timedelta(days=30) if status == "approved" else None,
                   created_at=now - timedelta(days=31), is_demo=True))
    db.flush()

    contacts = list(db.scalars(select(Contact).where(Contact.is_demo.is_(True)).order_by(Contact.id)).all())
    calls = _call_rows(now, contacts, rng)
    db.add_all(calls)
    db.flush()

    # campaigns: one already run in practice mode, one waiting for approval, one draft
    def make(name, channel, purpose, segment, subject, body, status, **kw):
        c = Campaign(name=name, channel=channel, purpose=purpose, segment_json=json.dumps(cp.clean_segment(segment), sort_keys=True), subject=subject, body=body,
                     status=status, created_at=now - timedelta(days=kw.pop("age", 2)), is_demo=True, **kw)
        db.add(c)
        db.flush()
        return c

    done = make("Vaccine reminder, early autumn", "sms", "care", {"kind": "patient"}, "",
                "Hi {first_name}, {pet_name} may be due for a check-up and vaccines. Call us or reply to book a time. {clinic_name}",
                "approved", age=6, approved_at=now - timedelta(days=5))
    evaluated, _ = cp.evaluate(db, None, done.channel, done.purpose, json.loads(done.segment_json))
    sent = 0
    skipped: dict[str, int] = {}
    for c, reason in evaluated:
        state = "dry_run_sent" if reason is None else "skipped"
        sent += reason is None
        if reason:
            skipped[reason] = skipped.get(reason, 0) + 1
        db.add(CampaignRecipient(campaign_id=done.id, contact_id=c.id, state=state, skipped_reason=reason or "", is_demo=True))
    done.status, done.ran_at = "done_dry_run", now - timedelta(days=5, hours=-1)
    done.counts_json = json.dumps({"matched": len(evaluated), "sent": sent, "skipped": skipped})
    make("Senior cat check-up offer", "email", "promo", {"species": "cat", "kind": "patient"}, "A senior check-up for {pet_name}",
         "Hi {first_name}, cats over ten benefit from a yearly senior check-up. Reply to this email and we will find a time for {pet_name}.", "awaiting_approval", age=1)
    make("Dental month announcement", "email", "promo", {"kind": "patient", "email_consent": "full"}, "Dental month at {clinic_name}",
         "Hi {first_name}, February is dental month. Ask us about a cleaning for {pet_name}.", "draft", age=0)
    db.flush()
    relink(db)
    return {"knowledge_entries": len(APPROVED) + len(PENDING) + 1, "fees": len(FEES), "phone_calls": len(calls), "campaigns": 3}


def relink(db: Session) -> None:
    """Point demo rows at staff accounts by name once those accounts exist. Safe to run again."""
    users = {u.display_name: u.id for u in db.scalars(select(User)).all()}
    if not users:
        return
    doc = users.get("Dr. Garg")
    for e in db.scalars(select(KnowledgeEntry).where(KnowledgeEntry.is_demo.is_(True), KnowledgeEntry.status.in_(("approved", "declined")), KnowledgeEntry.decided_by.is_(None))).all():
        e.decided_by = doc
    for f in db.scalars(select(Fee).where(Fee.is_demo.is_(True), Fee.status == "approved", Fee.decided_by.is_(None))).all():
        f.decided_by = doc
    for c in db.scalars(select(Campaign).where(Campaign.is_demo.is_(True))).all():
        if c.created_by is None:
            c.created_by = users.get("Priya")
        if c.status in ("approved", "done_dry_run") and c.approved_by is None:
            c.approved_by = doc
