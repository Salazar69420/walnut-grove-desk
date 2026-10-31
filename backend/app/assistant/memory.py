"""What the assistant remembers about one person, beyond the last texts. Erased with the contact.

* `ai_memory`: a short running summary of OLDER texts (the most recent ones are sent as they are), updated by the assistant model
  when enough new texts have scrolled out of the window. Facts only: names, pets, preferences, what was asked and promised.
* Built fresh each time: their last few calls (date, reason, summary), open tasks, and staff notes on preferences.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Contact, Message, PhoneCall, Task
from app.services import crm, timeutil

log = logging.getLogger("walnut.memory")
WINDOW = 20          # texts sent to the model as they are
REFRESH_EVERY = 10   # summarise again once this many texts have left the window
IMPORT_NOTE = "Imported from call recordings."
SCHEMA = {"type": "object", "properties": {"memory": {"type": "string"}}, "required": ["memory"], "additionalProperties": False}
PROMPT = ("You keep short notes for a vet clinic's text assistant about ONE client. Merge the old notes with the older texts below into at most "
          "8 short bullet points of lasting facts: their name and pets, preferences, what they asked for, what the clinic promised or booked, "
          "anything unresolved. Drop small talk. No medical advice. Reply with JSON only: {\"memory\": \"- ...\\n- ...\"}.")


def block(db: Session, c: Contact) -> str:
    """Text for the model: the memory, recent calls and open tasks. Empty when there is nothing."""
    from app.services.transcribe import INTENTS
    parts: list[str] = []
    if (c.ai_memory or "").strip():
        parts.append("From earlier conversations:\n" + c.ai_memory.strip()[:1500])
    if (c.preference_notes or "").strip() and not c.preference_notes.startswith(IMPORT_NOTE):
        parts.append("Staff notes on preferences: " + c.preference_notes.strip()[:400])
    calls = db.scalars(select(PhoneCall).where(PhoneCall.contact_id == c.id).order_by(PhoneCall.started_at.desc()).limit(4)).all()
    if calls:
        lines = []
        for p in calls:
            what = p.summary or INTENTS.get(p.intent or "", "") or p.short_note or p.result
            lines.append(f"- {timeutil.local(p.started_at).strftime('%b %d')}: {'they called' if p.direction == 'inbound' else 'we called'} ({p.result}). {what}"[:300])
        parts.append("Recent phone calls:\n" + "\n".join(lines))
    tasks = db.scalars(select(Task).where(Task.contact_id == c.id, Task.status != "done").order_by(Task.created_at.desc()).limit(3)).all()
    if tasks:
        parts.append("Open with the team (do not promise more than this): " + "; ".join(t.title for t in tasks))
    return "\n\n".join(parts)


def refresh(db: Session, c: Contact) -> bool:
    """Fold texts that left the window into ai_memory. Returns True when it was updated. Never raises."""
    from app.assistant import ai
    from app.services.transcribe import _post
    try:
        total = db.scalar(select(func.count()).select_from(Message).where(Message.contact_id == c.id, Message.channel == "sms")) or 0
        if total <= WINDOW:
            return False
        recent = db.scalars(select(Message.created_at).where(Message.contact_id == c.id, Message.channel == "sms")
                            .order_by(Message.created_at.desc(), Message.id.desc()).limit(WINDOW)).all()
        cutoff = min(recent)
        since = crm.aware(c.ai_memory_at) if c.ai_memory_at else None
        q = select(Message).where(Message.contact_id == c.id, Message.channel == "sms", Message.created_at < cutoff)
        if since:
            q = q.where(Message.created_at >= since)
        old = db.scalars(q.order_by(Message.created_at).limit(200)).all()
        if len(old) < REFRESH_EVERY and c.ai_memory:
            return False
        if not old:
            return False
        cfg = ai.get_config(db)
        if not cfg.get("key_enc"):
            return False
        convo = "\n".join(f"{'Client' if m.direction == 'in' else 'Clinic'}: {(m.body or '').strip()[:400]}" for m in old)
        content = _post(cfg, {"model": cfg["model"], "temperature": 0.1, "max_tokens": 500,
                              "messages": [{"role": "system", "content": PROMPT},
                                           {"role": "user", "content": f"Old notes:\n{c.ai_memory or '(none)'}\n\nOlder texts:\n{convo}"}],
                              "response_format": {"type": "json_schema", "json_schema": {"name": "client_memory", "strict": True, "schema": SCHEMA}}})
        mem = str(json.loads(re.search(r"\{.*\}", content, re.S).group(0)).get("memory") or "").strip()
        if not mem or ai.FORBIDDEN.search(mem):
            return False
        c.ai_memory, c.ai_memory_at = mem[:1500], cutoff
        return True
    except Exception as e:  # noqa: BLE001 - memory is a bonus; never break a reply
        log.warning("memory refresh skipped (%s)", type(e).__name__)
        return False


def context(db: Session, c: Contact) -> dict[str, Any]:
    return {"memory": c.ai_memory, "updated": timeutil.iso(c.ai_memory_at) if c.ai_memory_at else None, "block": block(db, c)}
