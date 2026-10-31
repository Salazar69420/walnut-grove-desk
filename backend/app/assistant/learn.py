"""Learn from how a person resolved something the assistant could not.

When a task the assistant raised (missing answer or a hand-over) is marked done, and a staff member texted or emailed the client after
the task was raised, the model reads the client's question and the staff reply and may suggest ONE general, reusable knowledge entry.
It lands in Knowledge & Fees as PENDING for an owner to approve; it is never approved by itself. It is skipped when the staff reply
was specific to that one client, medical, or there was no reply at all. Only for people the assistant is allowed to serve with AI.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters import llm as llm_adapter
from app.assistant import ai
from app.models import Contact, KnowledgeEntry, Message, Note, Pet, Task, User
from app.models.clinic import KNOWLEDGE_CATEGORIES
from app.security import crypto
from app.services import crm
from app.services import events as ev
from app.services import knowledge as kn

log = logging.getLogger("walnut.assistant.learn")
KINDS = ("unknown_knowledge", "escalation")
SCHEMA = {
    "type": "object",
    "properties": {
        "should_learn": {"type": "boolean"},
        "category": {"type": "string", "enum": [c for c in KNOWLEDGE_CATEGORIES if c != "laws_regulations"]},
        "title": {"type": "string"},
        "text": {"type": "string", "description": "the general answer, written the way the clinic would text it"},
        "reason": {"type": "string"},
    },
    "required": ["should_learn", "category", "title", "text", "reason"],
    "additionalProperties": False,
}
PROMPT = """You help a vet clinic's text assistant learn from its staff.
You get a question a client texted, and how a staff member answered it. Decide whether the staff answer contains a GENERAL clinic fact or policy that would answer the same question from any other client next time (hours, services, prices, procedures, what to bring, how something works).
- If it does, write it as one or two short sentences the clinic could text to anyone. Use only what the staff member said. No client names, pet names, dates or details that are about this one client.
- If the answer was only about this client's own pet, appointment, bill or results, or it was medical advice, set should_learn to false.
- Title: a few words, like a question topic ("Grooming for cats").
Reply with JSON only."""


def _context(db: Session, t: Task) -> tuple[str, str] | None:
    q = db.scalar(select(Message).where(Message.contact_id == t.contact_id, Message.direction == "in", Message.channel.in_(("sms", "email")),
                                        Message.created_at <= t.created_at).order_by(Message.created_at.desc(), Message.id.desc()).limit(1))
    replies = db.scalars(select(Message).where(Message.contact_id == t.contact_id, Message.direction == "out", Message.sender_kind == "staff",
                                               Message.created_at >= t.created_at).order_by(Message.created_at).limit(4)).all()
    notes = db.scalars(select(Note).where(Note.contact_id == t.contact_id, Note.created_at >= t.created_at).order_by(Note.created_at).limit(3)).all()
    if q is None or not replies:
        return None
    staff = "\n".join(f"- {m.body.strip()[:600]}" for m in replies)
    if notes:
        staff += "\nInternal notes:\n" + "\n".join(f"- {n.body.strip()[:400]}" for n in notes)
    return q.body.strip()[:800], staff


def suggest_from_task(db: Session, task_id: int, user_id: int) -> KnowledgeEntry | None:
    t = db.get(Task, task_id)
    if t is None or t.status != "done" or t.raised_by != "ai" or t.kind not in KINDS or not t.contact_id:
        return None
    c = db.get(Contact, t.contact_id)
    cfg = ai.get_config(db)
    if c is None or not cfg.get("key_enc") or not ai.allowed_for(db, c):
        return None
    ctx = _context(db, t)
    if ctx is None:
        return None
    question, staff = ctx
    key = crypto.decrypt(cfg["key_enc"])
    payload = {"model": cfg["model"], "temperature": 0.1, "max_tokens": 400,
               "messages": [{"role": "system", "content": PROMPT}, {"role": "user", "content": f"Client asked:\n{question}\n\nStaff answered:\n{staff}"}],
               "response_format": {"type": "json_schema", "json_schema": {"name": "learned_knowledge", "strict": True, "schema": SCHEMA}},
               "provider": {"require_parameters": True, **({"data_collection": "deny"} if cfg["deny_data_collection"] else {})}}
    db.commit()  # release the database before waiting for the model
    try:
        with httpx.Client(timeout=ai.TIMEOUT, transport=llm_adapter.TRANSPORT) as h:
            r = h.post(ai.URL, json=payload, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "X-Title": "Walnut Grove Desk"})
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
        data: dict[str, Any] = json.loads(re.search(r"\{.*\}", content, re.S).group(0))
    except Exception as e:  # noqa: BLE001 - learning is a bonus; never break closing a task
        log.warning("learning skipped (%s)", type(e).__name__)
        return None
    if data.get("should_learn") is not True:
        return None
    title, text = str(data.get("title") or "").strip()[:200], re.sub(r"\s+", " ", str(data.get("text") or "")).strip()[:1000]
    personal = {w.lower() for w in (c.display_name or "").split() if len(w) > 2}
    personal |= {p.name.lower() for p in db.scalars(select(Pet).where(Pet.contact_id == c.id)).all() if len(p.name) > 2}
    words = set(re.findall(r"[a-z']+", f"{title} {text}".lower()))
    if len(title) < 3 or len(text) < 10 or ai.FORBIDDEN.search(text) or words & personal:
        return None
    category = data.get("category") if data.get("category") in KNOWLEDGE_CATEGORIES else "clinic_info"
    staff_user = db.get(User, user_id)
    try:
        e = kn.propose(db, category=category, title=title, body=text, proposed_by="ai", source_kind="conversation",
                       source_ref=f"Learned from how {staff_user.display_name if staff_user else 'staff'} handled a task", source_quote=question[:300])
    except crm.CrmError:
        return None  # already known or already waiting
    ev.record(db, ev.KNOWLEDGE, "knowledge_learned", f"The assistant suggested knowledge after a task was done: {e.title}", "AI assistant", "knowledge", e.id,
              {"task_id": t.id})
    db.commit()
    return e


def after_done(task_id: int, user_id: int) -> None:
    """Run after the response (its own session), so closing a task is never slowed down or broken by this."""
    from app import db as app_db
    with app_db.get_sessionmaker()() as s:
        try:
            suggest_from_task(s, task_id, user_id)
        except Exception as e:  # noqa: BLE001
            s.rollback()
            log.warning("learning failed (%s)", type(e).__name__)
