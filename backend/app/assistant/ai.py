"""The AI that writes replies (OpenRouter), its settings, its prompt, and the checks every reply must pass.

Who may be answered by the AI: only contacts with the assistant switched on AND whose number is on the texting test list. Everyone
else is answered by the rules in replies.py, and their words never leave the server.

What the AI sees: the base prompt (the owner can edit it in Admin), the fixed rules below (cannot be edited), every APPROVED knowledge
entry and fee with an id, the last few texts of this conversation, and the client's first name and pet names. No phone, email or last name.

What the code checks before a reply is used (otherwise the safe rule-based reply is used and a task is raised):
* valid JSON in the agreed shape, a non-empty reply of at most 640 characters;
* when it claims an answer, it cites at least one id it was actually given;
* every number and clock time in the reply appears in the cited knowledge (or the client's own message);
* no medicine, dose or diagnosis wording.
Emergencies and medical questions never reach the AI: replies.py answers them with fixed text first.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import httpx
from sqlalchemy import select

from app.aftercare import templates as T
from sqlalchemy.orm import Session

from app.adapters import llm as llm_adapter
from app.models import Contact, Fee, KnowledgeEntry, Message, Pet
from app.security import crypto
from app.services import numbercheck, settings_store, sms_mode

log = logging.getLogger("walnut.assistant.ai")

KEY = "assistant_ai"
URL = "https://openrouter.ai/api/v1/chat/completions"
MODELS_URL = "https://openrouter.ai/api/v1/models"
DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash-1017"
AGENT_NAME = "Hazel"  # the assistant's name in texts (owner can change it in Admin > AI assistant)
TRANSCRIBE_MODEL = "openai/whisper-large-v3-turbo"  # OpenRouter speech-to-text; used only to turn call recordings into text
TIMEOUT = 25.0
MAX_REPLY = 640
HISTORY = 20  # the last texts go to the model as they are; older ones live in the contact's memory (assistant/memory.py)

OLD_DEFAULT_PROMPT = """You are the text-message assistant for Walnut Grove Animal Hospital, a veterinary clinic in Langley, British Columbia. You answer pet owners by text on behalf of the front desk.

How to write:
- Sound like the clinic's front desk on the phone: warm, calm and short. Plain Canadian English.
- One to three short sentences. No lists, no emojis, no sign-off (one is added for you).
- Use the client's first name at most once. Mention their pet by name when it helps.
- Answer the question that was asked. If the knowledge covers only part of it, answer that part and say a team member will follow up on the rest.
- When you cannot help, say kindly that a member of the team will get back to them soon.

Good habits from the clinic's calls:
- Offer to book rather than promise a time: "We can book you in, a team member will confirm the time."
- For anything about a specific pet's medicine, results or health, say the team will call them."""

DEFAULT_PROMPT = """You are the text-message assistant for Walnut Grove Animal Hospital, a veterinary clinic in Langley, British Columbia. You reply to pet owners by text on behalf of the front desk team.

How to sound:
- Warm, calm and genuinely kind, like the best person at the front desk. Plain Canadian English, the way people actually text.
- Notice how they feel. If they are worried, acknowledge it first in a few words before anything else. Do not over-apologise.
- Short: one to three sentences. No lists, no emojis, no sign-off. Use their first name once at most and their pet's name when it helps.
- Vary your wording; never sound like a form letter.

Being honest:
- Only say what the clinic knowledge below supports. When you do not know, say so plainly and kindly, and tell them a team member will follow up.
- Never pretend to be a person. If someone asks whether they are talking to a person or a bot, say you are the clinic's automated assistant and that a team member can help too.

Good habits from the clinic's calls:
- Offer to help book rather than promising a time; the team confirms every appointment.
- For anything about a specific pet's health, medicine or results, say the team will call them."""

RULES = """Hard rules (always apply, they override everything above):
1. Use ONLY facts from CLINIC KNOWLEDGE and FEES below. Never guess or invent hours, prices, policies, services, availability, names, addresses or phone numbers. If they do not answer the question, set "answered" to false and write a short, kind holding reply saying a team member will get back to them.
2. Never give medical advice, a diagnosis, a medicine name or amount, or say a pet is fine or not fine. If asked, set "needs_human" to true and say the veterinary team will call them.
3. If the message could be an emergency, set "needs_human" to true and tell them to contact an emergency veterinarian now.
4. Appointments: do not hesitate. When the client wants to come in and a day AND time are clear (they named one inside CLINIC HOURS, or picked one of the OPEN TIMES), fill "appointment_request" with that date and time (a part of the day such as "morning" is fine) and tell them warmly you have sent the request to the team, who will confirm it by text. A person on the team reads the conversation and approves it, so you do not need to be sure. If the day or time is not clear yet, offer two or three OPEN TIMES (cite A1) and ask, with "wanted" false; set "wanted" true only in the reply where you say you sent the request. Never say an appointment is confirmed or booked, never cancel one, and mention an appointment they already have only if it is under THEIR APPOINTMENTS (cite A2).
5. Never ask for or repeat card numbers or other payment details.
6. "knowledge_ids" lists the ids (like K12, F3, P4, A1 or A2) of every entry you used, also when you only mention their appointment.
7. Reply with JSON only, matching the schema.
8. If OPTIONAL PRODUCTS are listed, you may add ONE short sentence suggesting ONE of them, using its wording, only when it clearly fits what the client asked about. Never suggest anything when they are worried about their pet's health, upset, grieving, or asking about an emergency. Include its id.
9. If you are in any doubt at all, set "needs_human" to true. Handing over to a person is always fine. When "answered" is false or "needs_human" is true, fill "task_title" (a short, specific title for the team, like "Asks if we see rabbits") and "task_note" (what the client needs, what was missing, and the next step you suggest).
10. Read the whole conversation above and reply to what they mean in context, like a person would. Use WHAT YOU REMEMBER ABOUT THEM naturally: never ask for something you already know (their name, their pet, what they told us before), and pick up where things were left. If the message is only thanks, a greeting, an okay or a goodbye, reply warmly and briefly (mention their appointment from THEIR APPOINTMENTS if there is one), set "answered" false, "needs_human" false and leave task_title empty: no task is needed for that.
11. Write like a real person texting, never like a system: no numbered menus, no "reply 1, 2 or 3", no form-letter phrases, and never talk about how things work behind the scenes (do not say "within our hours", "open times list", "the system" or "the Calendar"). Use what they told you before, in texts and on calls (see WHAT YOU REMEMBER), the way a front-desk person who knows them would."""

SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string", "description": "The text message to send"},
        "answered": {"type": "boolean", "description": "true only if the reply answers the question from the knowledge given"},
        "needs_human": {"type": "boolean", "description": "true if a person should follow up"},
        "knowledge_ids": {"type": "array", "items": {"type": "string"}, "description": "ids of the knowledge used"},
        "reason": {"type": "string", "description": "a few words on why, for the clinic team"},
        "task_title": {"type": "string", "description": "short specific title for the team when a person must follow up, else empty"},
        "task_note": {"type": "string", "description": "what the client needs, what is missing, suggested next step; else empty"},
        "appointment_request": {"type": "object", "description": "a time the client wants, to send to the team for approval", "properties": {
            "wanted": {"type": "boolean"}, "date": {"type": "string", "description": "YYYY-MM-DD or empty"},
            "time": {"type": "string", "description": "HH:MM 24-hour or empty"}, "visit": {"type": "string", "description": "what it is for, or empty"}},
            "required": ["wanted", "date", "time", "visit"], "additionalProperties": False},
    },
    "required": ["reply", "answered", "needs_human", "knowledge_ids", "reason", "task_title", "task_note", "appointment_request"],
    "additionalProperties": False,
}
FORBIDDEN = re.compile(
    r"\b(\d+\s?(mg|ml|milligrams?|millilit\w*|cc|tablets?|pills?|capsules?)|dose|dosage|diagnos\w*|ibuprofen|tylenol|acetaminophen|aspirin|benadryl|"
    r"metacam|gabapentin|prednisone|apoquel|antibiotic\w*|(he|she|they|it|pet|dog|cat|kitten|puppy) (is|'s|looks|seems|sounds) (fine|okay|ok|normal)|nothing to worry)\b", re.I)


RETRY = {
    "no_source": "it says it answered but cites no clinic knowledge (K, F or A id).",
    "unknown_id": "it cites an id that was not given.",
    "number_not_in_knowledge": "it contains a number or time that is not in the cited knowledge.",
    "medical_wording": "it contains medical wording (a medicine, an amount, or saying a pet is fine).",
    "too_long": "it is too long for a text; use at most three short sentences.",
    "bad_time": "the appointment_request is not a valid future time inside CLINIC HOURS. If you are still asking them what time suits, set wanted false; otherwise fix the date and time (a part of the day like morning is fine).",
}


class AiFailed(Exception):
    """The AI could not be used for this reply. The message is a short code, never text from the model or the client."""


# ---------------------------------------------------------------- settings
def defaults() -> dict[str, Any]:
    return {"enabled": False, "staff_enabled": False, "model": DEFAULT_MODEL, "key_enc": "", "key_last4": None, "deny_data_collection": True, "system_prompt": DEFAULT_PROMPT,
            "transcribe_enabled": True, "transcribe_model": TRANSCRIBE_MODEL, "agent_name": AGENT_NAME}


def get_config(db: Session) -> dict[str, Any]:
    cfg = defaults()
    stored = settings_store.get_raw(db, KEY, None)
    if isinstance(stored, dict):
        cfg.update({k: v for k, v in stored.items() if k in cfg})
    if cfg.get("transcribe_model") == "google/gemini-2.5-flash-lite":
        cfg["transcribe_model"] = TRANSCRIBE_MODEL  # the owner chose Whisper (2026-10-31)
    if not str(cfg.get("system_prompt") or "").strip() or cfg["system_prompt"].strip() == OLD_DEFAULT_PROMPT.strip():
        cfg["system_prompt"] = DEFAULT_PROMPT  # nobody edited the old default: use the new, warmer one
    return cfg


def public(cfg: dict[str, Any]) -> dict[str, Any]:
    return {"enabled": bool(cfg["enabled"]), "staff_enabled": bool(cfg["staff_enabled"]), "model": cfg["model"], "has_key": bool(cfg["key_enc"]), "key_last4": cfg["key_last4"],
            "deny_data_collection": bool(cfg["deny_data_collection"]), "system_prompt": cfg["system_prompt"],
            "transcribe_enabled": bool(cfg["transcribe_enabled"]), "transcribe_model": cfg["transcribe_model"], "agent_name": cfg["agent_name"], "default_prompt": DEFAULT_PROMPT,
            "rules": RULES}


def save(db: Session, cfg: dict[str, Any], by: str) -> None:
    settings_store.put_raw(db, KEY, {k: cfg[k] for k in defaults()}, by=by)


def allowed_for(db: Session, contact: Contact) -> bool:
    """The AI may write for this person: switched on, a key is set, assistant on for the person, number on the test list."""
    cfg = get_config(db)
    return bool(cfg["enabled"] and cfg["key_enc"] and contact.ai_enabled and contact.phone and contact.phone in sms_mode.get(db)["test_numbers"])


# ---------------------------------------------------------------- the prompt (it grows with the knowledge base)
def knowledge_block(db: Session) -> tuple[str, dict[str, str]]:
    """The approved knowledge and fees as text with ids, and a map id -> text (for checking numbers)."""
    lines, texts = ["CLINIC KNOWLEDGE (approved by the clinic):"], {}
    for e in db.scalars(select(KnowledgeEntry).where(KnowledgeEntry.status == "approved", KnowledgeEntry.category != "laws_regulations")
                        .order_by(KnowledgeEntry.category, KnowledgeEntry.id)).all():
        kid = f"K{e.id}"
        texts[kid] = f"{e.title}. {e.body}"
        lines.append(f"[{kid}] {e.title}: {e.body.strip()}")
    if len(lines) == 1:
        lines.append("(nothing approved yet)")
    lines.append("")
    lines.append("FEES (approved):")
    n = len(lines)
    for f in db.scalars(select(Fee).where(Fee.status == "approved").order_by(Fee.service)).all():
        fid = f"F{f.id}"
        price = "ask the clinic" if f.price_cents is None else f"{(f.price_prefix + ' ') if f.price_prefix else ''}${f.price_cents / 100:.2f}".replace(".00", "")
        line = f"{f.service}: {price}{(' ' + f.unit) if f.unit else ''}{(' (' + f.note + ')') if f.note else ''}"
        texts[fid] = line
        lines.append(f"[{fid}] {line}")
    if len(lines) == n:
        lines.append("(none approved yet)")
    return "\n".join(lines), texts


def schedule_block(db: Session, contact: Contact | None) -> tuple[str, dict[str, str], list]:
    """Open times (A1) and this client's own upcoming appointments (A2), from the Calendar, as the model's only source for times."""
    from app.assistant import booking
    texts: dict[str, str] = {}
    from app.services import timeutil as _tu
    now = _tu.local(_tu.now_utc())
    hours = booking.get_hours(db)
    names = {"mon": "Mon", "tue": "Tue", "wed": "Wed", "thu": "Thu", "fri": "Fri", "sat": "Sat", "sun": "Sun"}
    week = "; ".join(f"{names[d]} " + (", ".join(f"{a} to {b}" for a, b in hours['days'].get(d, [])) or "closed") for d in booking.DAYS)
    texts["A0"] = f"Today is {now.strftime('%A %B %d %Y, %H:%M')}. Clinic hours: {week}."
    lines: list[str] = ["", f"TODAY [A0]: {now.strftime('%A %Y-%m-%d, %H:%M')} (Vancouver time). CLINIC HOURS: {week}."]
    times = booking.availability(db)
    if times:
        texts["A1"] = "Open times: " + "; ".join(f"{i}. {booking.label(t)}" for i, t in enumerate(times, 1))
        lines += ["", "OPEN TIMES [A1] (a SAMPLE of open times from the Calendar right now, other times are free too; never say these are the only, first or last ones; a team member confirms any booking):"] + [f"{i}. {booking.label(t)}" for i, t in enumerate(times, 1)]
    if contact is not None:
        mine = booking.upcoming(db, contact)
        if mine:
            texts["A2"] = "Their appointments: " + "; ".join(f"{booking.label(e.starts_at)} ({'confirmed' if e.status == 'booked' else 'waiting for the team to confirm'})" for e in mine)
            lines += ["", "THEIR APPOINTMENTS [A2]:"] + [f"- {booking.label(e.starts_at)}: {e.title.split(':')[0]} ({'confirmed' if e.status == 'booked' else 'waiting for the team to confirm'})" for e in mine]
    return "\n".join(lines), texts, times


def system_prompt(db: Session, products: list | None = None, contact: Contact | None = None) -> tuple[str, dict[str, str]]:
    cfg = get_config(db)
    kb, texts = knowledge_block(db)
    sched, stexts, _ = schedule_block(db, contact)
    texts.update(stexts)
    extra = sched
    if products:
        from app.assistant import upsell
        lines = ["", "OPTIONAL PRODUCTS (approved; suggest at most one, only if it clearly fits):"]
        for p in products:
            texts[f"P{p.id}"] = f"{p.name}. {upsell.line(p)}"
            lines.append(f"[P{p.id}] {p.name}: {upsell.line(p)}{(' (fits: ' + p.triggers + ')') if p.triggers else ''}")
        extra += "\n".join(lines)
    name = (cfg.get("agent_name") or AGENT_NAME).strip()[:30]
    who = (f"Your name is {name}. Mention your name only when it is natural (for example the first time you text someone). If anyone asks whether "
           f"they are texting a person, say you are {name}, Walnut Grove's automated assistant, and that a team member can help too.\n\n")
    return f"{who}{cfg['system_prompt'].strip()}\n\n{RULES}\n\n{kb}{extra}", texts


def conversation(db: Session, contact: Contact, latest: str) -> list[dict[str, str]]:
    first = (T.first_name(contact.display_name) or "there")
    pets = [f"{p.name} ({p.species})" if p.species else p.name for p in db.scalars(select(Pet).where(Pet.contact_id == contact.id, Pet.status == "active")).all()]
    msgs = db.scalars(select(Message).where(Message.contact_id == contact.id, Message.channel == "sms").order_by(Message.created_at.desc(), Message.id.desc())
                      .limit(HISTORY)).all()
    out: list[dict[str, str]] = []
    for m in reversed(msgs):
        role = "user" if m.direction == "in" else "assistant"
        body = (m.body or "").strip()[:800]
        if body:
            out.append({"role": role, "content": body})
    name = first if first != "there" else "not known yet (do not use a name; never call them by a placeholder like 'Text')"
    from app.assistant import memory
    mem = memory.block(db, contact)
    header = f"(Client first name: {name}. Pets: {', '.join(pets) if pets else 'not on file'}.)\n" + (f"(WHAT YOU REMEMBER ABOUT THEM:\n{mem})\n" if mem else "")
    if out and out[-1]["role"] == "user" and out[-1]["content"] == latest.strip()[:800]:
        out[-1]["content"] = header + out[-1]["content"]
    else:
        out.append({"role": "user", "content": header + latest.strip()[:800]})
    return out


# ---------------------------------------------------------------- calling OpenRouter
def call(cfg: dict[str, Any], system: str, messages: list[dict[str, str]]) -> dict[str, Any]:
    key = crypto.decrypt(cfg["key_enc"]) if cfg["key_enc"] else None
    if not key:
        raise AiFailed("no_key")
    payload: dict[str, Any] = {
        "model": cfg["model"], "messages": [{"role": "system", "content": system}, *messages], "temperature": 0.3, "max_tokens": 1200,
        "response_format": {"type": "json_schema", "json_schema": {"name": "clinic_text_reply", "strict": True, "schema": SCHEMA}},
        "provider": {"require_parameters": True, **({"data_collection": "deny"} if cfg["deny_data_collection"] else {})},
    }
    try:
        with httpx.Client(timeout=TIMEOUT, transport=llm_adapter.TRANSPORT) as c:
            r = c.post(URL, json=payload, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                                                  "HTTP-Referer": "https://77-42-69-194.sslip.io", "X-Title": "Walnut Grove Desk"})
    except httpx.TimeoutException:
        raise AiFailed("timeout") from None
    except httpx.HTTPError:
        raise AiFailed("network") from None
    if r.status_code != 200:
        raise AiFailed(f"http_{r.status_code}")
    try:
        body = r.json()
        content = body["choices"][0]["message"]["content"]
        data = json.loads(re.search(r"\{.*\}", content, re.S).group(0)) if isinstance(content, str) else None
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        raise AiFailed("bad_json") from None
    if not isinstance(data, dict):
        raise AiFailed("bad_json")
    usage = body.get("usage") or {}
    data["_usage"] = {"prompt_tokens": usage.get("prompt_tokens"), "completion_tokens": usage.get("completion_tokens"), "cost": usage.get("cost"),
                      "model": body.get("model")}
    return data


def check(data: dict[str, Any], texts: dict[str, str], client_text: str) -> dict[str, Any]:
    """Raise AiFailed unless the reply is safe to use. Returns the cleaned result."""
    reply = data.get("reply")
    if not isinstance(reply, str) or not reply.strip():
        raise AiFailed("empty")
    reply = re.sub(r"\s+", " ", reply).strip()
    if len(reply) > MAX_REPLY:
        raise AiFailed("too_long")
    answered = data.get("answered") is True
    needs_human = data.get("needs_human") is True
    ids = [str(i) for i in (data.get("knowledge_ids") or []) if isinstance(i, (str, int))]
    ids = [i for i in ids if i in texts]  # a made-up id supports nothing; drop it (an answer still needs a real source below)
    appt = None
    a = data.get("appointment_request") if isinstance(data.get("appointment_request"), dict) else {}
    if a.get("wanted"):
        from datetime import date as _date
        try:
            d = _date.fromisoformat(str(a.get("date")))
            visit = re.sub(r"\s+", " ", str(a.get("visit") or "")).strip()[:60]
            t = str(a.get("time") or "").strip().lower()
            part = next((p for p in ("morning", "afternoon", "evening") if p in t), "any" if t in ("", "any", "anytime", "any time") else None)
            if part:  # "Monday morning": the first open time then; staff see what was asked and can adjust
                appt = {"date": d, "part": part, "visit": visit}
            else:
                hh, mm = (int(x) for x in t.split(":")[:2])
                appt = {"date": d, "hour": hh, "minute": mm, "visit": visit}
        except (ValueError, TypeError):
            raise AiFailed("bad_time") from None
    if answered and not needs_human and not appt and not any(i[0] in "KFA" for i in ids) and "A1" in texts:
        cal = [texts[k] for k in ("A0", "A1", "A2") if k in texts] + [client_text]
        allowed_cal: list[Any] = [1]
        for src in cal:
            n_, t_ = numbercheck.extract(src)
            allowed_cal += n_ + t_
        if re.search(r"\d", reply) and numbercheck.check(reply, allowed_cal)[0]:
            ids = ids + [k for k in ("A0", "A1", "A2") if k in texts and k not in ids]  # every time it named comes from the Calendar
    if answered and not needs_human and not appt and not any(i[0] in "KFA" for i in ids):
        raise AiFailed("no_source")
    reply = re.sub(r"\s*[\u2014\u2013]\s*", ", ", reply)  # house style: no long dashes in client texts
    cited = " ".join(texts[i] for i in ids).lower()
    if any(m.group(0).lower() not in cited for m in FORBIDDEN.finditer(reply)):  # wording copied from approved knowledge is fine
        raise AiFailed("medical_wording")
    allowed: list[Any] = [1]
    srcs = [texts[i] for i in ids] + [client_text]
    if appt and "hour" in appt:  # the reply may name the requested day and time
        from datetime import time as _time
        srcs.append(f"{appt['date'].strftime('%A %B')} {appt['date'].day} {appt['date'].year} {appt['date'].isoformat()} "
                    f"{_time(appt['hour'], appt['minute']).strftime('%I:%M %p').lstrip('0')} {appt['hour']}:{appt['minute']:02d}")
    for src in srcs:
        nums, times = numbercheck.extract(src)
        allowed += nums + times
    ok, _bad = numbercheck.check(reply, allowed)
    if not ok:
        raise AiFailed("number_not_in_knowledge")
    return {"reply": reply, "answered": answered and not needs_human, "needs_human": needs_human, "ids": ids,
            "reason": str(data.get("reason") or "")[:200], "usage": data.get("_usage") or {},
            "task_title": str(data.get("task_title") or "")[:120], "task_note": str(data.get("task_note") or "")[:800], "appointment": appt}


def _checked(db: Session, data: dict[str, Any], texts: dict[str, str], text: str) -> dict[str, Any]:
    """check(), plus: a requested appointment must be a future time inside the clinic's hours (it becomes a request for staff)."""
    from app.assistant import booking
    from app.services import timeutil as _tu
    out = check(data, texts, text)
    a = out.get("appointment")
    if a and "part" in a:
        free = booking.free_times(db, 30, n=1, day=a["date"], part=None if a["part"] == "any" else a["part"], staff=True)
        if not free:
            raise AiFailed("bad_time")
        loc = _tu.local(free[0])
        a.update(hour=loc.hour, minute=loc.minute, asked=f"{a['date'].strftime('%A')} {a['part'] if a['part'] != 'any' else ''}".strip())
    if a:
        start = _tu.at_local(a["date"], a["hour"], a["minute"])
        if not booking.within_hours(db, start) or start < _tu.now_utc() + __import__("datetime").timedelta(minutes=15):
            raise AiFailed("bad_time")
        a["start"] = start
    return out


def draft(db: Session, contact: Contact, text: str, note: str = "") -> dict[str, Any]:
    """The checked AI reply for this contact's message. Raises AiFailed with a short code. `note` is a fact from the code for this turn."""
    from app.assistant import upsell
    cfg = get_config(db)
    system, texts = system_prompt(db, upsell.eligible(db, contact, text), contact)
    if note:
        system += f"\n\nFOR THIS MESSAGE: {note}"
    started = time.monotonic()
    msgs = conversation(db, contact, text)
    db.commit()  # release the database before waiting for the model (the inbound loop and the app share it)
    try:
        data = call(cfg, system, msgs)
    except AiFailed as e:
        if str(e) != "bad_json":
            raise
        data = call(cfg, system, msgs)  # a cut-off or malformed answer: one more go
    try:
        out = _checked(db, data, texts, text)
    except AiFailed as first_try:
        if str(first_try) not in RETRY:
            raise
        # one more go, told why: usually it answered from general knowledge instead of the clinic's, or cited nothing
        fix = msgs + [{"role": "assistant", "content": json.dumps({k: data.get(k) for k in SCHEMA["properties"]})},
                      {"role": "user", "content": f"(System check: that reply was rejected: {RETRY[str(first_try)]} Write it again, still warm and natural, following the hard rules. "
                                                  "If the clinic knowledge does not cover it, set answered false, say kindly that a team member will check and get back to them, and fill task_title.)"}]
        data = call(cfg, system, fix)
        out = _checked(db, data, texts, text)
    for i in out["ids"]:
        if i.startswith("P"):
            upsell.log(db, contact, int(i[1:]))
    out["model"] = cfg["model"]
    out["seconds"] = round(time.monotonic() - started, 1)
    return out


# ---------------------------------------------------------------- the model list for the picker
_models_cache: dict[str, Any] = {"at": 0.0, "items": []}


def list_models() -> list[dict[str, Any]]:
    """Text models that support structured JSON output, cheapest first. Cached for an hour."""
    if time.time() - _models_cache["at"] < 3600 and _models_cache["items"]:
        return _models_cache["items"]
    try:
        with httpx.Client(timeout=20, transport=llm_adapter.TRANSPORT) as c:
            r = c.get(MODELS_URL)
        rows = r.json().get("data") or []
    except (httpx.HTTPError, ValueError):
        return _models_cache["items"]
    items = []
    for m in rows:
        sp = m.get("supported_parameters") or []
        mods = ((m.get("architecture") or {}).get("output_modalities") or ["text"])
        if "structured_outputs" not in sp or "text" not in mods:
            continue
        pr = m.get("pricing") or {}
        try:
            pin, pout = float(pr.get("prompt") or 0) * 1e6, float(pr.get("completion") or 0) * 1e6
        except ValueError:
            continue
        items.append({"id": m.get("id"), "name": m.get("name"), "context": m.get("context_length"), "in_per_million": round(pin, 3), "out_per_million": round(pout, 3)})
    items.sort(key=lambda x: (x["in_per_million"] + x["out_per_million"], x["id"]))
    _models_cache.update(at=time.time(), items=items)
    return items
