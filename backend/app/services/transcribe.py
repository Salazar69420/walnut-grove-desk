"""Every new recorded call is transcribed, summarised and filed, a few at a time, in the background.

1. Ask the download helper for a short-lived copy of the recording (the same path the play button uses; nothing is kept).
2. Send the audio to the transcript model on OpenRouter (openai/whisper-large-v3-turbo by default, the owner's choice, through
   /audio/transcriptions; a chat model that takes audio also works) and store the text on the call (owners read it).
3. The assistant model reads the transcript and returns a summary, the reason for the call (a fixed list, for reports), whether it
   was an existing patient or a new inquiry, the caller's own name and pet (only when they say them), urgency, and whether someone
   must follow up. A number with no contact becomes a prospect contact (not opted in, marked to check); nothing staff entered is
   ever overwritten. The call note becomes fixed words ("Refill; follow-up needed"), the summary goes to the call (owners) and
   to the task: an open callback task gets the summary added, and a call that needs follow-up and has no task gets one.
Only TELUS calls of 8 seconds or more from the last 30 days; imported calls already have transcripts.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.adapters import llm as llm_adapter
from app.assistant import ai
from app.models import Contact, Message, PhoneCall, Task
from app.security import crypto
from app.services import crm, timeutil
from app.services import events as ev

log = logging.getLogger("walnut.transcribe")
MIN_SECONDS = 8
MAX_AGE_DAYS = 30
IN_FLIGHT = 4  # recordings asked for in one batch
MAX_TRIES = 4
WAIT_FOR_COPY = 600  # seconds before asking the helper again
MAX_AUDIO = 25 * 1024 * 1024
INTENTS = {
    "booking": "Booking", "reschedule_or_cancel": "Reschedule or cancel", "refill": "Refill", "food_or_supply_order": "Food or supply order",
    "pickup_ready": "Pickup", "results_question": "Asked about results", "check_in": "Check-in on a pet", "records_request": "Records request",
    "pricing_or_policy_question": "Price or policy question", "triage_symptom": "Symptom question", "billing_or_insurance": "Billing or insurance",
    "complaint": "Complaint", "vendor_or_business": "Vendor or business", "wrong_number_or_spam": "Wrong number or spam", "other": "Call",
}
TASK_KIND = {"refill": "refill", "results_question": "results", "booking": "booking", "reschedule_or_cancel": "booking", "complaint": "complaint"}
OUTCOMES = ("resolved", "follow_up_needed", "voicemail_left", "no_conversation")
SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "two or three plain sentences: who called, about which pet, what they need, what was agreed"},
        "intent": {"type": "string", "enum": list(INTENTS)},
        "outcome": {"type": "string", "enum": list(OUTCOMES)},
        "follow_up_needed": {"type": "boolean"},
        "follow_up_note": {"type": "string", "description": "what the team should do next, or empty"},
        "caller_type": {"type": "string", "enum": ["patient", "inquiry"], "description": "patient: already a client of the clinic; inquiry: new or not clear"},
        "caller_first_name": {"type": "string", "description": "only if the CALLER says their own name, else empty"},
        "caller_last_name": {"type": "string", "description": "only if the caller says it, else empty"},
        "pet_name": {"type": "string", "description": "the caller's pet discussed, else empty"},
        "species": {"type": "string", "enum": ["", "dog", "cat", "rabbit", "bird", "other"]},
        "urgent": {"type": "boolean", "description": "true if the caller described signs that may need a vet today"},
        "appointment": {"type": "object", "description": "an appointment time the caller and the clinic AGREED on this call", "properties": {
            "agreed": {"type": "boolean"}, "date": {"type": "string", "description": "YYYY-MM-DD or empty"},
            "time": {"type": "string", "description": "HH:MM, 24-hour clock, or empty"}, "visit": {"type": "string", "description": "what the visit is for"}},
            "required": ["agreed", "date", "time", "visit"], "additionalProperties": False},
        "clinic_knowledge": {"type": "array", "maxItems": 2, "items": {"type": "object", "properties": {
            "category": {"type": "string", "enum": ["clinic_info", "fees", "patient_communication", "inquiry_communication", "specials"]},
            "title": {"type": "string"}, "text": {"type": "string"}}, "required": ["category", "title", "text"], "additionalProperties": False}},
    },
    "required": ["summary", "intent", "outcome", "follow_up_needed", "follow_up_note", "caller_type", "caller_first_name", "caller_last_name",
                 "pet_name", "species", "urgent", "appointment", "clinic_knowledge"],
    "additionalProperties": False,
}
TRANSCRIBE_PROMPT = ("Transcribe this phone call to a veterinary clinic word for word. Start each turn on a new line with 'Speaker 1:' or "
                     "'Speaker 2:' (the clinic is usually the one who greets with the clinic name). Write only the transcript. "
                     "If there is no speech, write NO_SPEECH.")
ANALYSE_PROMPT = ("You read call transcripts for Walnut Grove Animal Hospital, a vet clinic. Summarise the call for the front desk in two or "
                  "three plain sentences (who, which pet, what they need, what was agreed). Pick the reason for the call. Set follow_up_needed "
                  "when the clinic still has to do something (call back, a refill, results, booking, a question nobody answered, a voicemail). "
                  "Say whether the caller is an existing patient of the clinic or a new inquiry. Give the caller's name and pet only if the "
                  "CALLER says them (never a staff member's name; the clinic side greets with the clinic name). Set urgent when the caller "
                  "describes signs that may need a vet today. In appointment, set agreed only when a specific day AND time were clearly agreed by both "
                  "sides on this call (work out words like tomorrow or next Tuesday from the call date given); not for times only offered "
                  "or discussed. In clinic_knowledge, list (at most two) GENERAL clinic facts or policies the STAFF member "
                  "actually said while answering, that would answer the same question from any other client (hours, prices, services, "
                  "procedures, what to bring, how things work), written as the clinic would text them. Only what staff said, nothing about "
                  "this one client (no names, pets, dates, results), no medical advice; an empty list when there is none. The transcript may "
                  "have no speaker labels. Do not add medical advice. "
                  "Reply with JSON only.")
_queued_at: dict[int, float] = {}
_last_batch = {"at": -1e9}
BATCH_EVERY = 75  # seconds between batches of recording requests


def _key(cfg: dict[str, Any]) -> str | None:
    return crypto.decrypt(cfg["key_enc"]) if cfg.get("key_enc") else None


def _post(cfg: dict[str, Any], payload: dict[str, Any], timeout: float = 120.0) -> str:
    key = _key(cfg)
    if not key:
        raise ai.AiFailed("no_key")
    payload["provider"] = {"require_parameters": True, **({"data_collection": "deny"} if cfg["deny_data_collection"] else {})}
    with httpx.Client(timeout=timeout, transport=llm_adapter.TRANSPORT) as h:
        r = h.post(ai.URL, json=payload, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "X-Title": "Walnut Grove Desk"})
    if r.status_code != 200:
        raise ai.AiFailed(f"http_{r.status_code}")
    content = r.json()["choices"][0]["message"]["content"]
    if not isinstance(content, str) or not content.strip():
        raise ai.AiFailed("empty")
    return content.strip()


STT_URL = "https://openrouter.ai/api/v1/audio/transcriptions"


def _speech_to_text_model(model: str) -> bool:
    return "whisper" in model or "transcribe" in model


def transcribe_audio(cfg: dict[str, Any], audio: bytes, ext: str) -> str:
    if _speech_to_text_model(cfg["transcribe_model"]):
        key = _key(cfg)
        if not key:
            raise ai.AiFailed("no_key")
        mime = {"mp3": "audio/mpeg", "wav": "audio/wav", "ogg": "audio/ogg", "m4a": "audio/mp4", "mp4": "audio/mp4", "webm": "audio/webm"}.get(ext, "audio/mpeg")
        with httpx.Client(timeout=180.0, transport=llm_adapter.TRANSPORT) as h:
            r = h.post(STT_URL, headers={"Authorization": f"Bearer {key}", "X-Title": "Walnut Grove Desk"},
                       files={"file": (f"call.{ext}", audio, mime)}, data={"model": cfg["transcribe_model"], "language": "en"})
        if r.status_code != 200:
            raise ai.AiFailed(f"http_{r.status_code}")
        text = str((r.json() or {}).get("text") or "").strip()
        return text[:100000] if text else "NO_SPEECH"
    fmt = {"mp3": "mp3", "wav": "wav", "ogg": "ogg", "m4a": "m4a", "mp4": "mp4", "webm": "webm"}.get(ext, "mp3")
    text = _post(cfg, {"model": cfg["transcribe_model"], "temperature": 0, "max_tokens": 8000, "messages": [{"role": "user", "content": [
        {"type": "text", "text": TRANSCRIBE_PROMPT},
        {"type": "input_audio", "input_audio": {"data": base64.b64encode(audio).decode("ascii"), "format": fmt}}]}]}, timeout=180.0)
    return text[:100000]


def analyse(cfg: dict[str, Any], transcript: str, when: str = "") -> dict[str, Any]:
    content = _post(cfg, {"model": cfg["model"], "temperature": 0.1, "max_tokens": 2000,
                          "messages": [{"role": "system", "content": ANALYSE_PROMPT},
                                       {"role": "user", "content": (f"Call date: {when} (Vancouver time)\n\n" if when else "") + "Transcript:\n" + transcript[:24000]}],
                          "response_format": {"type": "json_schema", "json_schema": {"name": "call_summary", "strict": True, "schema": SCHEMA}}})
    m = re.search(r"\{.*\}", content, re.S)
    try:
        data = json.loads(m.group(0)) if m else None
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise ai.AiFailed("bad_json")
    if data.get("intent") not in INTENTS:
        data["intent"] = "other"
    return data


def file_call(db: Session, c: PhoneCall, transcript: str, data: dict[str, Any], *, historical: bool = False) -> None:
    """Store the transcript and summary on the call, update the conversation line, and update or raise the task.
    historical=True (older imported calls): labels, reports, contacts and call notes only; no tasks or Calendar requests months late."""
    c.transcript, c.transcript_state = transcript, "done"
    c.summary = re.sub(r"\s+", " ", str(data.get("summary") or "")).strip()[:1200] or None
    c.intent = data["intent"]
    follow = bool(data.get("follow_up_needed"))
    c.short_note = (INTENTS[c.intent] + ("; follow-up needed" if follow else ""))[:240]
    learn_from_call(db, c, data, historical=historical)
    if c.intent != "wrong_number_or_spam" and data.get("caller_type") in ("patient", "inquiry"):
        c.call_type = data["caller_type"]  # every real call is a patient or an inquiry
    if c.contact_id is None and c.phone and c.intent not in ("wrong_number_or_spam", "vendor_or_business"):
        _prospect(db, c, data, need_name=historical)
    elif c.contact_id and c.intent not in ("wrong_number_or_spam", "vendor_or_business"):
        enrich_contact(db, c, data)
    if not historical:
        agreed_appointment(db, c, data)
    m = db.scalar(select(Message).where(Message.phone_call_id == c.id))
    if m is not None:
        m.body = c.short_note
    if historical:
        return
    if not c.phone or c.intent == "wrong_number_or_spam":
        return
    when = timeutil.local(c.started_at).strftime("%a %b %d, %I:%M %p").replace(" 0", " ")
    line = f"Call {when}: {c.summary or INTENTS[c.intent]}" + (f" Next step: {data['follow_up_note'].strip()}" if str(data.get("follow_up_note") or "").strip() else "")
    open_t = db.scalar(select(Task).where(Task.status != "done", Task.reason.like(f"%{crm.phone_display(c.phone)}%")).order_by(Task.id.desc()).limit(1))
    if open_t is not None:
        open_t.reason = (open_t.reason + "\n" + line)[:2000]
        if data.get("urgent"):
            open_t.priority = "high"
        if open_t.kind == "callback" and c.intent not in ("other",) and ":" not in open_t.title:
            open_t.title = f"{open_t.title}: {INTENTS[c.intent].lower()}"[:200]
        return
    if not follow or c.direction != "inbound" and c.result != "answered":
        return
    who = db.get(Contact, c.contact_id) if c.contact_id else None
    name = who.display_name if who else crm.phone_display(c.phone)
    urgent = bool(data.get("urgent"))
    t = Task(kind=TASK_KIND.get(c.intent, "callback"), classification=who.kind if who else "inquiry",
             title=(f"Urgent, {INTENTS[c.intent].lower()}: {name}" if urgent else f"{INTENTS[c.intent]}: {name}")[:200],
             reason=f"{line}\nNumber: {crm.phone_display(c.phone)}."[:2000], contact_id=who.id if who else None, priority="high" if urgent else "normal", status="open",
             raised_by="ai", created_at=timeutil.now_utc())
    db.add(t)
    db.flush()
    ev.record(db, ev.TASKS, "task_raised_by_assistant", f"The assistant raised a task from a call transcript: {t.title[:80]}", "AI assistant", "task", t.id)


def agreed_appointment(db: Session, c: PhoneCall, data: dict[str, Any]):
    """A time agreed on the call goes into the Calendar as REQUESTED, with a task; any staff member approves it with one click."""
    from datetime import date as _date, datetime as _dt
    from app.assistant import booking
    from app.models import CalendarEvent, Pet, VisitType
    a = data.get("appointment") if isinstance(data.get("appointment"), dict) else {}
    if not a.get("agreed") or not c.contact_id or c.intent in ("wrong_number_or_spam", "vendor_or_business"):
        return None
    try:
        d = _date.fromisoformat(str(a.get("date")))
        hh, mm = (int(x) for x in str(a.get("time")).split(":")[:2])
        start = timeutil.at_local(d, hh, mm)
    except (ValueError, TypeError):
        return None
    if not (crm.aware(c.started_at) < start <= crm.aware(c.started_at) + timedelta(days=120)) or start < timeutil.now_utc():
        return None
    visit = re.sub(r"\s+", " ", str(a.get("visit") or "")).strip()[:60] or "Appointment"
    named = next((n for rx, n in booking.VISIT_RULES if rx.search(visit)), None)
    vt = db.scalar(select(VisitType).where(VisitType.name == named)) if named else None
    minutes = vt.minutes if vt else 30
    end = start + timedelta(minutes=minutes)
    if db.scalar(select(CalendarEvent.id).where(CalendarEvent.contact_id == c.contact_id, CalendarEvent.status.in_(("booked", "requested")),
                                                CalendarEvent.starts_at < end + timedelta(minutes=30), CalendarEvent.ends_at > start - timedelta(minutes=30)).limit(1)):
        return None  # already in the Calendar
    ct = db.get(Contact, c.contact_id)
    pet = db.scalar(select(Pet).where(Pet.contact_id == ct.id, Pet.status == "active").order_by(Pet.id).limit(1))
    e = CalendarEvent(kind=booking.KIND_FOR_VISIT.get(named or "", "checkup"), classification=ct.kind, title=f"{(named or visit).capitalize()}: {ct.display_name} (agreed on a call)"[:200],
                      contact_id=ct.id, pet_id=pet.id if pet else None, starts_at=start, ends_at=end, status="requested", source="app")
    db.add(e)
    db.flush()
    t = Task(kind="booking", classification=ct.kind, title=f"Confirm phone booking: {ct.display_name}, {booking.label(start)}"[:200],
             reason=f"A time was agreed on a call ({c.summary or 'see the call'}). It is in the Calendar waiting for approval: check it, approve it, and enter it in ImproMed."[:2000],
             contact_id=ct.id, priority="normal", status="open", raised_by="ai", created_at=timeutil.now_utc())
    db.add(t)
    ev.record(db, ev.CONTACTS, "booking_requested_from_call", f"An appointment agreed on a call is waiting for approval: {ct.display_name}, {booking.label(start)}",
              "AI assistant", "calendar_event", e.id)
    return e


MAX_CALL_NOTES_PER_DAY = 15


MAX_CALL_NOTES_FROM_HISTORY = 60  # older calls: enough to cover the common questions without burying the owner


def learn_from_call(db: Session, c: PhoneCall, data: dict[str, Any], *, historical: bool = False) -> int:
    """How staff answered on the phone becomes PENDING knowledge, labelled "Call notes", for an owner to approve one by one."""
    from app.models import KnowledgeEntry, Pet
    from app.services import knowledge as kn
    items = data.get("clinic_knowledge") if isinstance(data.get("clinic_knowledge"), list) else []
    if not items or c.intent in ("wrong_number_or_spam", "vendor_or_business", "triage_symptom"):
        return 0
    if historical:
        if (db.scalar(select(func.count()).select_from(KnowledgeEntry).where(KnowledgeEntry.source_ref.like("Call notes%"))) or 0) >= MAX_CALL_NOTES_FROM_HISTORY:
            return 0
    else:
        since = timeutil.now_utc() - timedelta(days=1)
        if (db.scalar(select(func.count()).select_from(KnowledgeEntry).where(KnowledgeEntry.source_ref.like("Call notes%"), KnowledgeEntry.proposed_at >= since)) or 0) >= MAX_CALL_NOTES_PER_DAY:
            return 0
    personal = {str(data.get(k) or "").strip().lower() for k in ("caller_first_name", "caller_last_name", "pet_name")}
    if c.contact_id:
        ct = db.get(Contact, c.contact_id)
        personal |= {w.lower() for w in (ct.display_name or "").split()} if ct else set()
        personal |= {p.lower() for p in db.scalars(select(Pet.name).where(Pet.contact_id == c.contact_id)).all()}
    personal = {w for w in personal if len(w) > 2}
    when = timeutil.local(c.started_at).strftime("%b %d, %I:%M %p").replace(" 0", " ")
    added = 0
    for it in items[:2]:
        if not isinstance(it, dict):
            continue
        title, text = str(it.get("title") or "").strip()[:200], re.sub(r"\s+", " ", str(it.get("text") or "")).strip()[:600]
        cat = it.get("category") if it.get("category") in ("clinic_info", "fees", "patient_communication", "inquiry_communication", "specials") else "clinic_info"
        words = set(re.findall(r"[a-z']+", f"{title} {text}".lower()))
        if len(title) < 3 or len(text) < 15 or ai.FORBIDDEN.search(text) or words & personal:
            continue
        if db.scalar(select(KnowledgeEntry.id).where(KnowledgeEntry.status == "pending", func.lower(KnowledgeEntry.title) == title.lower()).limit(1)):
            continue  # one suggestion per topic waits at a time
        try:
            e = kn.propose(db, category=cat, title=title, body=text, proposed_by="ai", source_kind="call", source_ref=f"Call notes: call on {when}"[:80])
        except crm.CrmError:
            continue
        ev.record(db, ev.KNOWLEDGE, "knowledge_from_call", f"Call notes: suggested knowledge from how staff answered a call: {e.title}", "AI assistant", "knowledge", e.id,
                  {"phone_call_id": c.id})
        added += 1
    return added


NAME_OK = re.compile(r"^[A-Za-z][A-Za-z'\-]{1,29}$")


def _prospect(db: Session, c: PhoneCall, data: dict[str, Any], need_name: bool = False) -> None:
    """A number that called us with no contact yet becomes a prospect, only when exactly nobody has that number. Never texted until they opt in."""
    from app.models import Pet
    if db.scalar(select(Contact.id).where(Contact.phone == c.phone, Contact.erased_at.is_(None)).limit(1)) is not None:
        return
    first, last = str(data.get("caller_first_name") or "").strip(), str(data.get("caller_last_name") or "").strip()
    first = first if NAME_OK.match(first) else ""
    last = last if first and NAME_OK.match(last) else ""
    if need_name and not first:
        return  # an old call with no name given: leave it as a number in the call log
    kind = data.get("caller_type") if data.get("caller_type") in ("patient", "inquiry") else "inquiry"
    name = f"{first} {last}".strip().title() if first else f"Caller {crm.phone_display(c.phone)}"
    ct = Contact(kind=kind, display_name=name[:120], phone=c.phone, email_consent="none", sms_consent="none", consent_source="Called the clinic, not opted in",
                 source="phone", ai_enabled=False, created_at=c.started_at, last_active_at=c.started_at,
                 preference_notes="Created from a call transcript. The name and pet were picked up automatically: please check them.")
    db.add(ct)
    db.flush()
    pet = str(data.get("pet_name") or "").strip()
    if NAME_OK.match(pet) and pet.lower() != first.lower():
        db.add(Pet(contact_id=ct.id, name=pet.title()[:80], species=str(data.get("species") or ""), status="active", is_demo=False))
    c.contact_id = ct.id
    db.flush()
    from app.services import calls_live
    calls_live.relink(db)  # their other calls from this number
    calls_live.ensure_call_messages(db)
    for t in db.scalars(select(Task).where(Task.contact_id.is_(None), Task.status != "done", Task.reason.like(f"%{crm.phone_display(c.phone)}%"))).all():
        t.contact_id = ct.id
    ev.record(db, ev.CONTACTS, "contact_from_call", f"New {kind} contact from a call: {name}", "AI assistant", "contact", ct.id)


AUTO_CREATED = ("Imported from a call recording. No consent recorded.", "Called the clinic, not opted in")


def enrich_contact(db: Session, c: PhoneCall, data: dict[str, Any]) -> None:
    """Fill in a contact the system created (never one staff made or edited by hand): full name, pets, patient or inquiry."""
    from app.models import Pet
    ct = db.get(Contact, c.contact_id)
    if ct is None or ct.erased_at is not None or ct.consent_source not in AUTO_CREATED:
        return
    first, last = str(data.get("caller_first_name") or "").strip(), str(data.get("caller_last_name") or "").strip()
    parts = (ct.display_name or "").split()
    if NAME_OK.match(first):
        if ct.display_name.startswith("Caller ") or not parts:
            ct.display_name = f"{first} {last}".strip().title()[:120] if NAME_OK.match(last) else first.title()
        elif len(parts) == 1 and parts[0].lower() == first.lower() and NAME_OK.match(last) and last.lower() != first.lower():
            ct.display_name = f"{parts[0]} {last.title()}"[:120]
    pet = str(data.get("pet_name") or "").strip()
    have = {p.name.lower() for p in db.scalars(select(Pet).where(Pet.contact_id == ct.id)).all()}
    if NAME_OK.match(pet) and pet.lower() not in have and pet.lower() not in {w.lower() for w in ct.display_name.split()} and len(have) < 6:
        db.add(Pet(contact_id=ct.id, name=pet.title()[:80], species=str(data.get("species") or ""), status="active", is_demo=False))
    elif NAME_OK.match(pet) and data.get("species"):
        p = db.scalar(select(Pet).where(Pet.contact_id == ct.id, func.lower(Pet.name) == pet.lower(), Pet.species == "").limit(1))
        if p is not None:
            p.species = str(data["species"])
    db.flush()
    votes = dict(db.execute(select(PhoneCall.call_type, func.count()).where(PhoneCall.contact_id == ct.id, PhoneCall.call_type.in_(("patient", "inquiry")))
                            .group_by(PhoneCall.call_type)).all())
    if votes:
        ct.kind = "patient" if votes.get("patient", 0) >= votes.get("inquiry", 0) else "inquiry"


def _candidates(db: Session) -> list[PhoneCall]:
    since = timeutil.now_utc() - timedelta(days=MAX_AGE_DAYS)
    return list(db.scalars(select(PhoneCall).where(PhoneCall.source == "telus", PhoneCall.recording_id.is_not(None), PhoneCall.transcript.is_(None),
                                                   PhoneCall.transcript_state.in_(("", "queued")), PhoneCall.transcript_tries < MAX_TRIES,
                                                   PhoneCall.started_at >= since).order_by(PhoneCall.started_at.desc()).limit(40)).all())


def step(db: Session) -> dict[str, int]:
    """One pass: finish calls whose audio copy is ready, and ask for a few more."""
    from app.api import phone_audio
    from app.api import admin_telus as bridge
    cfg = ai.get_config(db)
    counts = {"done": 0, "asked": 0, "failed": 0}
    if not cfg.get("transcribe_enabled") or not cfg.get("key_enc") or not bridge.read_settings().get("recordings_enabled"):
        return counts
    rows = _candidates(db)
    in_flight = sum(1 for c in rows if c.transcript_state == "queued")  # a batch is still out: wait for it before asking again
    ready_jobs: list[tuple[int, str, str]] = []
    for c in rows:
        if c.duration_seconds < MIN_SECONDS:
            c.transcript_state = "skipped"
            continue
        if c.transcript_state == "queued":
            ready = phone_audio._ready(c.recording_id)
            if ready:
                if len(ready_jobs) < IN_FLIGHT:
                    ready_jobs.append((c.id, ready[0], timeutil.local(c.started_at).strftime("%A %Y-%m-%d %H:%M")))
                continue
            _queued_at.setdefault(c.id, time.monotonic())  # after a restart: count the wait from now, do not ask again at once
            if phone_audio._failed(c.recording_id) or time.monotonic() - _queued_at[c.id] > WAIT_FOR_COPY:
                c.transcript_state = "" if c.transcript_tries < MAX_TRIES else "failed"
            continue
        if in_flight or time.monotonic() - _last_batch["at"] < BATCH_EVERY or counts["asked"] >= IN_FLIGHT:
            continue  # ask in batches: the helper may start only 5 times in 5 minutes, and the play button needs room
        try:
            phone_audio.request_copy(c, "Call transcripts")
        except OSError:
            break
        c.transcript_state, c.transcript_tries = "queued", c.transcript_tries + 1
        _queued_at[c.id] = time.monotonic()
        counts["asked"] += 1
    if counts["asked"]:
        _last_batch["at"] = time.monotonic()
    db.commit()
    for cid, path, when in ready_jobs:
        # the slow part (Whisper and the model) runs with NO database transaction open; the result is saved in a short one after
        text, data, err = None, None, None
        try:
            if os.path.getsize(path) > MAX_AUDIO:
                raise ai.AiFailed("too_big")
            with open(path, "rb") as fh:
                audio = fh.read()
            text = transcribe_audio(cfg, audio, path.rsplit(".", 1)[-1])
            if not text.strip().upper().startswith("NO_SPEECH"):
                data = analyse(cfg, text, when)
        except Exception as e:  # noqa: BLE001 - nothing from the call is logged
            err = e
            log.warning("transcript failed (%s %s)", type(e).__name__, str(e)[:20] if isinstance(e, ai.AiFailed) else "")
        c = db.get(PhoneCall, cid)
        if c is None:
            continue
        if err is not None:
            c.transcript_state = "" if c.transcript_tries < MAX_TRIES else "failed"
            counts["failed"] += 1
        elif data is None:
            c.transcript_state, c.short_note = "done", c.short_note or "No conversation on the recording"
            counts["done"] += 1
        else:
            file_call(db, c, text, data)
            counts["done"] += 1
        _queued_at.pop(cid, None)
        db.commit()
    return counts


def run_once() -> dict[str, int]:
    from app import db as app_db
    with app_db.get_sessionmaker()() as s:
        try:
            return step(s)
        except Exception as e:  # noqa: BLE001
            s.rollback()
            log.error("transcript loop failed (%s)", type(e).__name__)
            return {}
