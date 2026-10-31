"""Suggest a reply to a client's message. Stage A of the plan: the assistant only drafts, a person reads, edits and sends.

Rules (docs/PLAN_CLINIC_CRM.md section 6), all enforced here and not by a model:
1. Emergency check first, with the clinic's emergency rules (the built-in floor plus the owner's list). A hit gives the fixed emergency
   text, naming a hospital only if the clinic has confirmed one, and raises a high-priority task.
2. Medical or dose questions are never answered. A fixed holding reply is drafted and a task is raised for the veterinary team.
3. Anything else is answered only from knowledge a person has APPROVED (and approved fees), word for word. No match means no answer:
   a holding reply and a "missing answer" task. Nothing is invented and no outside AI service sees the client's words.
The draft is returned with its sources so the person can see where every sentence came from.
"""
from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.aftercare import emergency as em
from app.aftercare import keywords as kw
from app.aftercare import templates as T
from app.models import Contact, Fee, Hospital, KnowledgeEntry, Message, Pet, Task
from app.services import crm
from app.services import events as ev
from app.services import timeutil

STOP = set("""a an the and or but if so to of in on at for with from by is are was were be been am i you your my our we us it its this that these those
there here what when where why how who which can could would should will do does did have has had not no yes please thanks thank hello hi hey just
about any some more also get got need want know like ok okay""".split())
PRICE_WORDS = {"price", "cost", "costs", "fee", "fees", "charge", "much", "pay", "quote"}
MEDICAL = re.compile(
    r"\b(dose|dosage|how much (?:\w+ ){0,3}(?:give|should)|how many (?:mg|ml|pills|tablets)|\d+\s?(?:mg|ml|milligrams?)|can i give|is it safe to give|safe to give|"
    r"should i give|what can i give|what should i give|diagnos\w*|what is wrong|what's wrong|is (?:he|she|it|my \w+) (?:ok|okay|fine|going to be)|will (?:he|she|it) be (?:ok|okay|fine)|"
    r"is this normal|is that normal|do you think it is serious|should i be worried|what does it mean)\b", re.I)
SKIP_CATEGORIES = {"laws_regulations"}
MIN_SCORE = 2


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9']+", (text or "").lower()) if len(w) >= 3 and w not in STOP}


def latest_client_text(db: Session, contact: Contact) -> str | None:
    m = db.scalar(select(Message).where(Message.contact_id == contact.id, Message.direction == "in", Message.channel.in_(("sms", "email")))
                  .order_by(Message.created_at.desc(), Message.id.desc()).limit(1))
    return ((m.subject + " " if m and m.subject else "") + m.body).strip() if m else None


def _confirmed_hospital(db: Session) -> dict | None:
    h = db.scalar(select(Hospital).where(Hospital.confirmed.is_(True), Hospital.is_placeholder.is_(False)).order_by(Hospital.sort, Hospital.id).limit(1))
    return None if h is None else {"name": h.name, "place": h.place, "phone": h.phone, "address": h.address, "hours_claim": h.hours_claim}


def match_knowledge(db: Session, text: str) -> list[dict[str, Any]]:
    q = _tokens(text)
    if not q:
        return []
    scored: list[tuple[int, dict[str, Any]]] = []
    for e in db.scalars(select(KnowledgeEntry).where(KnowledgeEntry.status == "approved")).all():
        if e.category in SKIP_CATEGORIES:
            continue
        t, b = _tokens(e.title), _tokens(e.body)
        title_hits, body_hits = len(q & t), len(q & (b - t))
        score = 2 * title_hits + body_hits
        if score >= MIN_SCORE and (title_hits >= 1 or body_hits >= 3):
            scored.append((score, {"id": e.id, "title": e.title, "kind": "knowledge", "text": e.body.strip()}))
    if q & PRICE_WORDS:
        for f in db.scalars(select(Fee).where(Fee.status == "approved")).all():
            hits = len(q & _tokens(f.service))
            if hits >= 1 and f.price_cents is not None:
                price = f"${f.price_cents / 100:.2f}".replace(".00", "")
                line = f"{f.service}: {(f.price_prefix + ' ') if f.price_prefix else ''}{price}{(' ' + f.unit) if f.unit else ''}."
                scored.append((2 * hits + 2, {"id": f.id, "title": f.service, "kind": "fee", "text": line}))
    scored.sort(key=lambda x: -x[0])
    return [x[1] for x in scored[:2]]


def _task(db: Session, contact: Contact, kind: str, title: str, reason: str, priority: str) -> Task:
    """One open task of this kind per person per day, so a flurry of messages does not make a pile."""
    since = timeutil.now_utc() - timedelta(hours=24)
    t = db.scalar(select(Task).where(Task.contact_id == contact.id, Task.kind == kind, Task.status.in_(crm.ACTIVE_TASK), Task.created_at > since).limit(1))
    if t is None:
        t = Task(kind=kind, classification=contact.kind, title=title[:200], reason=reason[:2000], contact_id=contact.id, priority=priority,
                 status="open", raised_by="ai", created_at=timeutil.now_utc(), is_demo=False)
        db.add(t)
        db.flush()
        ev.record(db, ev.TASKS, "task_raised_by_assistant", f"The assistant raised a task: {title[:80]}", "AI assistant", "task", t.id, {"kind": kind})
    return t


def suggest(db: Session, contact: Contact, text: str) -> dict[str, Any]:
    first = (contact.display_name.split() or ["there"])[0]
    pet = db.scalar(select(Pet.name).where(Pet.contact_id == contact.id, Pet.status == "active").order_by(Pet.id).limit(1))
    who = f"{first}" + (f" ({pet})" if pet else "")
    verdict = em.detect(text, kw.load_keywords(db))
    if verdict["emergency"]:
        t = _task(db, contact, "emergency_followup", f"Possible emergency: {who}", "The assistant saw emergency wording in a message. Call this client now.", "high")
        return {"kind": "emergency", "draft": str(T.emergency(_confirmed_hospital(db))), "sources": [], "task": {"id": t.id, "title": t.title},
                "note": "Emergency wording was found. A high-priority task was raised. Check the draft, then send or call now."}
    if MEDICAL.search(text):
        t = _task(db, contact, "escalation", f"Medical question from {who}", "The client asked something medical. The assistant gave no advice. A veterinary team member should answer.", "high")
        return {"kind": "medical", "draft": str(T.reply_medical(first)), "sources": [], "task": {"id": t.id, "title": t.title},
                "note": "This looks like a medical question, so the assistant does not answer it. A task was raised for the veterinary team."}
    hits = match_knowledge(db, text)
    if hits:
        return {"kind": "answer", "draft": str(T.reply_answers(first, [h["text"] for h in hits])),
                "sources": [{"id": h["id"], "title": h["title"], "kind": h["kind"]} for h in hits], "task": None,
                "note": "Built word for word from approved clinic knowledge. Read it before you send."}
    t = _task(db, contact, "unknown_knowledge", f"No approved answer for {who}", "The assistant had no approved knowledge for this message.", "normal")
    return {"kind": "unknown", "draft": str(T.reply_followup(first)), "sources": [], "task": {"id": t.id, "title": t.title},
            "note": "There is no approved knowledge for this, so the assistant will not guess. A task was raised. Add the answer in Knowledge & Fees to teach it."}
