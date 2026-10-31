"""Every new recorded call is transcribed, summarised and filed, a few at a time, in the background.

1. Ask the download helper for a short-lived copy of the recording (the same path the play button uses; nothing is kept).
2. Send the audio to the transcript model on OpenRouter (an audio model, google/gemini-2.5-flash-lite by default, no data
   collection) and store the text on the call (owners read it, as with imported transcripts).
3. The assistant model reads the transcript and returns a summary, the reason for the call (a fixed list, for reports) and whether
   someone must follow up. The call note becomes fixed words ("Refill; follow-up needed"), the summary goes to the call (owners) and
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
from sqlalchemy import select
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
IN_FLIGHT = 3
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
    },
    "required": ["summary", "intent", "outcome", "follow_up_needed", "follow_up_note"],
    "additionalProperties": False,
}
TRANSCRIBE_PROMPT = ("Transcribe this phone call to a veterinary clinic word for word. Start each turn on a new line with 'Speaker 1:' or "
                     "'Speaker 2:' (the clinic is usually the one who greets with the clinic name). Write only the transcript. "
                     "If there is no speech, write NO_SPEECH.")
ANALYSE_PROMPT = ("You read call transcripts for Walnut Grove Animal Hospital, a vet clinic. Summarise the call for the front desk in two or "
                  "three plain sentences (who, which pet, what they need, what was agreed). Pick the reason for the call. Set follow_up_needed "
                  "when the clinic still has to do something (call back, a refill, results, booking, a question nobody answered, a voicemail). "
                  "Do not add medical advice. Reply with JSON only.")
_queued_at: dict[int, float] = {}


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


def transcribe_audio(cfg: dict[str, Any], audio: bytes, ext: str) -> str:
    fmt = {"mp3": "mp3", "wav": "wav", "ogg": "ogg", "m4a": "m4a", "mp4": "mp4", "webm": "webm"}.get(ext, "mp3")
    text = _post(cfg, {"model": cfg["transcribe_model"], "temperature": 0, "max_tokens": 8000, "messages": [{"role": "user", "content": [
        {"type": "text", "text": TRANSCRIBE_PROMPT},
        {"type": "input_audio", "input_audio": {"data": base64.b64encode(audio).decode("ascii"), "format": fmt}}]}]}, timeout=180.0)
    return text[:100000]


def analyse(cfg: dict[str, Any], transcript: str) -> dict[str, Any]:
    content = _post(cfg, {"model": cfg["model"], "temperature": 0.1, "max_tokens": 600,
                          "messages": [{"role": "system", "content": ANALYSE_PROMPT}, {"role": "user", "content": transcript[:24000]}],
                          "response_format": {"type": "json_schema", "json_schema": {"name": "call_summary", "strict": True, "schema": SCHEMA}}})
    data = json.loads(re.search(r"\{.*\}", content, re.S).group(0))
    if data.get("intent") not in INTENTS:
        data["intent"] = "other"
    return data


def file_call(db: Session, c: PhoneCall, transcript: str, data: dict[str, Any]) -> None:
    """Store the transcript and summary on the call, update the conversation line, and update or raise the task."""
    c.transcript, c.transcript_state = transcript, "done"
    c.summary = re.sub(r"\s+", " ", str(data.get("summary") or "")).strip()[:1200] or None
    c.intent = data["intent"]
    follow = bool(data.get("follow_up_needed"))
    c.short_note = (INTENTS[c.intent] + ("; follow-up needed" if follow else ""))[:240]
    m = db.scalar(select(Message).where(Message.phone_call_id == c.id))
    if m is not None:
        m.body = c.short_note
    if not c.phone or c.intent == "wrong_number_or_spam":
        return
    when = timeutil.local(c.started_at).strftime("%a %b %d, %I:%M %p").replace(" 0", " ")
    line = f"Call {when}: {c.summary or INTENTS[c.intent]}" + (f" Next step: {data['follow_up_note'].strip()}" if str(data.get("follow_up_note") or "").strip() else "")
    open_t = db.scalar(select(Task).where(Task.status != "done", Task.reason.like(f"%{crm.phone_display(c.phone)}%")).order_by(Task.id.desc()).limit(1))
    if open_t is not None:
        open_t.reason = (open_t.reason + "\n" + line)[:2000]
        if open_t.kind == "callback" and c.intent not in ("other",) and ":" not in open_t.title:
            open_t.title = f"{open_t.title}: {INTENTS[c.intent].lower()}"[:200]
        return
    if not follow or c.direction != "inbound" and c.result != "answered":
        return
    who = db.get(Contact, c.contact_id) if c.contact_id else None
    name = who.display_name if who else crm.phone_display(c.phone)
    t = Task(kind=TASK_KIND.get(c.intent, "callback"), classification=who.kind if who else "inquiry", title=f"{INTENTS[c.intent]}: {name}"[:200],
             reason=f"{line}\nNumber: {crm.phone_display(c.phone)}."[:2000], contact_id=who.id if who else None, priority="normal", status="open",
             raised_by="ai", created_at=timeutil.now_utc())
    db.add(t)
    db.flush()
    ev.record(db, ev.TASKS, "task_raised_by_assistant", f"The assistant raised a task from a call transcript: {t.title[:80]}", "AI assistant", "task", t.id)


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
    in_flight = 0
    for c in rows:
        if c.duration_seconds < MIN_SECONDS:
            c.transcript_state = "skipped"
            continue
        if c.transcript_state == "queued":
            ready = phone_audio._ready(c.recording_id)
            if ready:
                path, _type = ready
                try:
                    if os.path.getsize(path) > MAX_AUDIO:
                        raise ai.AiFailed("too_big")
                    with open(path, "rb") as fh:
                        audio = fh.read()
                    text = transcribe_audio(cfg, audio, path.rsplit(".", 1)[-1])
                    if text.strip().upper().startswith("NO_SPEECH"):
                        c.transcript_state, c.short_note = "done", c.short_note or "No conversation on the recording"
                    else:
                        file_call(db, c, text, analyse(cfg, text))
                    counts["done"] += 1
                except Exception as e:  # noqa: BLE001 - nothing from the call is logged
                    log.warning("transcript failed (%s)", type(e).__name__)
                    c.transcript_state = "" if c.transcript_tries < MAX_TRIES else "failed"
                    counts["failed"] += 1
                _queued_at.pop(c.id, None)
                db.commit()
                continue
            if phone_audio._failed(c.recording_id) or time.monotonic() - _queued_at.get(c.id, 0) > WAIT_FOR_COPY:
                c.transcript_state = "" if c.transcript_tries < MAX_TRIES else "failed"
            else:
                in_flight += 1
            continue
        if in_flight >= IN_FLIGHT:
            continue
        try:
            phone_audio.request_copy(c, "Call transcripts")
        except OSError:
            break
        c.transcript_state, c.transcript_tries = "queued", c.transcript_tries + 1
        _queued_at[c.id] = time.monotonic()
        in_flight += 1
        counts["asked"] += 1
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
