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
TIMEOUT = 25.0
MAX_REPLY = 640
HISTORY = 8

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
4. Never book, cancel or confirm an appointment time yourself. You MAY tell them open times listed under OPEN TIMES (cite A1) and ask which one suits; a team member confirms it. Mention an appointment they already have only if it is listed under THEIR APPOINTMENTS (cite A2). Never offer a time that is not listed.
5. Never ask for or repeat card numbers or other payment details.
6. "knowledge_ids" lists the ids (like K12, F3, P4, A1 or A2) of every entry you used, also when you only mention their appointment.
7. Reply with JSON only, matching the schema.
8. If OPTIONAL PRODUCTS are listed, you may add ONE short sentence suggesting ONE of them, using its wording, only when it clearly fits what the client asked about. Never suggest anything when they are worried about their pet's health, upset, grieving, or asking about an emergency. Include its id.
9. If you are in any doubt at all, set "needs_human" to true. Handing over to a person is always fine. When "answered" is false or "needs_human" is true, fill "task_title" (a short, specific title for the team, like "Asks if we see rabbits") and "task_note" (what the client needs, what was missing, and the next step you suggest).
10. Read the whole conversation above and reply to what they mean in context, like a person would. If the message is only thanks, a greeting, an okay or a goodbye, reply warmly and briefly (mention their appointment from THEIR APPOINTMENTS if there is one), set "answered" false, "needs_human" false and leave task_title empty: no task is needed for that."""

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
    },
    "required": ["reply", "answered", "needs_human", "knowledge_ids", "reason", "task_title", "task_note"],
    "additionalProperties": False,
}
FORBIDDEN = re.compile(
    r"\b(\d+\s?(mg|ml|milligrams?|millilit\w*|cc|tablets?|pills?|capsules?)|dose|dosage|diagnos\w*|ibuprofen|tylenol|acetaminophen|aspirin|benadryl|"
    r"metacam|gabapentin|prednisone|apoquel|antibiotic\w*|is (fine|okay|ok|normal)|nothing to worry)\b", re.I)


class AiFailed(Exception):
    """The AI could not be used for this reply. The message is a short code, never text from the model or the client."""


# ---------------------------------------------------------------- settings
def defaults() -> dict[str, Any]:
    return {"enabled": False, "staff_enabled": False, "model": DEFAULT_MODEL, "key_enc": "", "key_last4": None, "deny_data_collection": True, "system_prompt": DEFAULT_PROMPT}


def get_config(db: Session) -> dict[str, Any]:
    cfg = defaults()
    stored = settings_store.get_raw(db, KEY, None)
    if isinstance(stored, dict):
        cfg.update({k: v for k, v in stored.items() if k in cfg})
    if not str(cfg.get("system_prompt") or "").strip() or cfg["system_prompt"].strip() == OLD_DEFAULT_PROMPT.strip():
        cfg["system_prompt"] = DEFAULT_PROMPT  # nobody edited the old default: use the new, warmer one
    return cfg


def public(cfg: dict[str, Any]) -> dict[str, Any]:
    return {"enabled": bool(cfg["enabled"]), "staff_enabled": bool(cfg["staff_enabled"]), "model": cfg["model"], "has_key": bool(cfg["key_enc"]), "key_last4": cfg["key_last4"],
            "deny_data_collection": bool(cfg["deny_data_collection"]), "system_prompt": cfg["system_prompt"], "default_prompt": DEFAULT_PROMPT,
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
    lines: list[str] = []
    times = booking.availability(db)
    if times:
        texts["A1"] = "Open times: " + "; ".join(f"{i}. {booking.label(t)}" for i, t in enumerate(times, 1))
        lines += ["", "OPEN TIMES [A1] (from the Calendar, right now; a team member confirms any booking):"] + [f"{i}. {booking.label(t)}" for i, t in enumerate(times, 1)]
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
    return f"{cfg['system_prompt'].strip()}\n\n{RULES}\n\n{kb}{extra}", texts


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
    header = f"(Client first name: {name}. Pets: {', '.join(pets) if pets else 'not on file'}.)\n"
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
        "model": cfg["model"], "messages": [{"role": "system", "content": system}, *messages], "temperature": 0.2, "max_tokens": 500,
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
    if any(i not in texts for i in ids):
        raise AiFailed("unknown_id")
    if answered and not any(i[0] in "KFA" for i in ids):
        raise AiFailed("no_source")
    if FORBIDDEN.search(reply):
        raise AiFailed("medical_wording")
    allowed: list[Any] = [1]
    for src in [texts[i] for i in ids] + [client_text]:
        nums, times = numbercheck.extract(src)
        allowed += nums + times
    ok, _bad = numbercheck.check(reply, allowed)
    if not ok:
        raise AiFailed("number_not_in_knowledge")
    return {"reply": reply, "answered": answered and not needs_human, "needs_human": needs_human, "ids": ids,
            "reason": str(data.get("reason") or "")[:200], "usage": data.get("_usage") or {},
            "task_title": str(data.get("task_title") or "")[:120], "task_note": str(data.get("task_note") or "")[:800]}


def draft(db: Session, contact: Contact, text: str) -> dict[str, Any]:
    """The checked AI reply for this contact's message. Raises AiFailed with a short code."""
    from app.assistant import upsell
    cfg = get_config(db)
    system, texts = system_prompt(db, upsell.eligible(db, contact, text), contact)
    started = time.monotonic()
    data = call(cfg, system, conversation(db, contact, text))
    out = check(data, texts, text)
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
