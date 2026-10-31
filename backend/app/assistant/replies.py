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
from app.models import Contact, Fee, Hospital, KnowledgeEntry, Message, Pet, Product, Task
from app.services import crm
from app.services import events as ev
from app.services import timeutil

STOP = set("""a an the and or but if so to of in on at for with from by is are was were be been am i you your my our we us it its this that these those
there here what when where why how who which can could would should will do does did have has had not no yes please thanks thank hello hi hey just
about any some more also get got need want know like ok okay""".split())
PRICE_WORDS = {"price", "cost", "fee", "charge", "much", "pay", "quote"}
MEDICAL = re.compile(
    r"\b(dose|dosage|how much (?:\w+ ){0,3}(?:give|should)|how many (?:mg|ml|pills|tablets)|\d+\s?(?:mg|ml|milligrams?)|can i give|is it safe to give|safe to give|"
    r"should i give|what can i give|what should i give|diagnos\w*|what is wrong|what's wrong|is (?:he|she|my \w+) (?:ok|okay|fine|going to be)|is it (?:going to be|serious)|will (?:he|she|it) be (?:ok|okay|fine)|"
    r"is this normal|is that normal|do you think it is serious|should i be worried|what does it mean)\b", re.I)
SKIP_CATEGORIES = {"laws_regulations"}
MIN_SCORE = 2


def _stem(w: str) -> str:
    """Very light: "bird's" -> bird, "nails" -> nail, "puppies" -> puppy, "trims" -> trim. Enough for clinic questions."""
    w = w.replace("\u2019", "'")
    if w.endswith("'s"):
        w = w[:-2]
    w = w.strip("'")
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
        return w[:-1]
    return w


def _tokens(text: str) -> set[str]:
    out = set()
    for w in re.findall(r"[a-z0-9'\u2019]+", (text or "").lower()):
        w = _stem(w)
        if len(w) >= 3 and w not in STOP:
            out.add(w)
    return out


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


def _titles(db: Session, ids: list[str]) -> list[dict[str, Any]]:
    out = []
    for i in ids:
        if i.startswith("K") and i[1:].isdigit() and (e := db.get(KnowledgeEntry, int(i[1:]))):
            out.append({"id": e.id, "title": e.title, "kind": "knowledge"})
        elif i.startswith("F") and i[1:].isdigit() and (f := db.get(Fee, int(i[1:]))):
            out.append({"id": f.id, "title": f.service, "kind": "fee"})
        elif i.startswith("P") and i[1:].isdigit() and (p := db.get(Product, int(i[1:]))):
            out.append({"id": p.id, "title": p.name, "kind": "product"})
    return out


_THANKS = re.compile(r"\b(thanks?|thank you|thank u|thx|ty|tysm|appreciate it|much appreciated|cheers)\b", re.I)
_BYE = re.compile(r"\b(bye|goodbye|see (you|ya|u)|take care|have a (good|great|nice|lovely) (one|day|night|evening|weekend)|talk soon|ttyl)\b", re.I)
_HELLO = re.compile(r"^\s*(hi+|hello|hey+|hiya|good (morning|afternoon|evening))( there)?[\s!.,]*$", re.I)
_OK = re.compile(r"^\s*(ok(ay)?|k|kk|great|perfect|sounds (good|great)|awesome|cool|got it|alright|all right|will do|yes|yep|yeah|sure|lovely|nice|amazing|wonderful|noted)"
                 r"([\s,!.]+(thanks?|thank you|great|perfect|sounds good))?[\s!.]*$", re.I)


def smalltalk(text: str) -> str | None:
    """thanks / bye / greeting / ok when the message is ONLY that (short, no question), else None."""
    t = (text or "").strip()
    if not t or "?" in t or len(t.split()) > 10:
        return None
    if _THANKS.search(t):
        return "thanks"
    if _BYE.search(t):
        return "bye"
    if _HELLO.match(t):
        return "greeting"
    if _OK.match(t):
        return "ok"
    return None


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


def suggest(db: Session, contact: Contact, text: str, *, act: bool = False) -> dict[str, Any]:
    first = (T.first_name(contact.display_name) or "there")
    pet = db.scalar(select(Pet.name).where(Pet.contact_id == contact.id, Pet.status == "active").order_by(Pet.id).limit(1))
    who = f"{first}" + (f" ({pet})" if pet else "")
    verdict = em.detect(text, kw.load_keywords(db))
    if verdict["emergency"]:
        t = _task(db, contact, "emergency_followup", f"Possible emergency: {who}", "The assistant saw emergency wording in a message. Call this client now.", "high")
        return {"kind": "emergency", "draft": str(T.emergency_text(first, pet, _confirmed_hospital(db))), "sources": [], "task": {"id": t.id, "title": t.title},
                "note": "Emergency wording was found. A high-priority task was raised. Check the draft, then send or call now."}
    if MEDICAL.search(text):
        t = _task(db, contact, "escalation", f"Medical question from {who}", "The client asked something medical. The assistant gave no advice. A veterinary team member should answer.", "high")
        return {"kind": "medical", "draft": str(T.reply_medical(first, pet)), "sources": [], "task": {"id": t.id, "title": t.title},
                "note": "This looks like a medical question, so the assistant does not answer it. A task was raised for the veterinary team."}
    from app.assistant import ai, booking, upsell
    b = booking.handle(db, contact, text, act=act)
    if b is not None and b.get("team_books") and ai.allowed_for(db, contact):
        # the team books this visit; the AI may still answer what they asked (drop-off times, fasting) around that fact
        try:
            r = ai.draft(db, contact, text, note=f"A {b['team_books'].lower()} is booked by our team, not by text. A task was raised; say warmly that someone will "
                                               "contact them to set up a time, and answer anything else they asked only from the knowledge.")
            return {**b, "draft": r["reply"], "sources": _titles(db, r["ids"]), "model": r["model"], "ai": True}  # the booking task is already raised
        except ai.AiFailed:
            pass
    if b is not None:
        if b.get("visit") and (p := upsell.pick(db, contact, text, b["visit"])):
            b["draft"] = f"{b['draft']} {upsell.line(p)}"
            b["sources"] = b["sources"] + [{"id": p.id, "title": p.name, "kind": "product"}]
            upsell.log(db, contact, p.id)
        return b
    chat = smalltalk(text)
    if ai.allowed_for(db, contact):
        try:
            r = ai.draft(db, contact, text)
        except ai.AiFailed as e:
            ev.record(db, ev.CONVERSATIONS, "ai_fallback", f"The AI reply could not be used ({e}), the rules answered instead", "AI assistant",
                      "contact", contact.id, {"code": str(e)}, publish=False)
        else:
            titles = _titles(db, r["ids"])
            meta = {"model": r["model"], "seconds": r["seconds"], "usage": r["usage"], "ai": True}
            appt = r.get("appointment")
            if appt and appt.get("start"):
                e = booking.request_from_conversation(db, contact, appt["start"], appt.get("visit") or "") if act else None
                return {"kind": "booking", "draft": r["reply"], "sources": titles, "task": None, "event_id": e.id if e else None, "visit": appt.get("visit") or None,
                        "note": ("The time they asked for was sent to the Calendar for approval." if act
                                 else f"Sending this would ask for {booking.label(appt['start'])}; the automatic replies add it to the Calendar for approval."), **meta}
            if act and "A1" in r["ids"] and (times := booking.availability(db)):
                booking.remember_offer(db, contact, times)  # they were told these times: "2" or "the 9 am" in their next text books it
            if r["answered"]:
                return {"kind": "answer", "draft": r["reply"], "sources": titles, "task": None,
                        "note": f"Written by the AI from approved knowledge ({r['model']}). Every number was checked against the knowledge.", **meta}
            if chat and not r["needs_human"]:
                return {"kind": "chat", "draft": r["reply"], "sources": titles, "task": None,
                        "note": "A friendly reply to a thank-you, greeting or goodbye. No task is needed.", **meta}
            kind = "escalation" if r["needs_human"] else "unknown_knowledge"
            title = (r.get("task_title") or "").strip() or (f"The assistant asked for a person: {who}" if r["needs_human"] else f"No approved answer for {who}")
            note = (r.get("task_note") or "").strip() or r["reason"] or "Not covered by approved knowledge."
            t = _task(db, contact, kind, f"{title} ({who})" if who not in title else title, f"{note}\n\nClient wrote: {text[:400]}", "normal")
            return {"kind": "unknown", "draft": r["reply"], "sources": [], "task": {"id": t.id, "title": t.title},
                    "note": "The AI had no approved answer, so it wrote a holding reply and raised a task. Add the answer in Knowledge & Fees to teach it.", **meta}
    if chat:
        nxt = booking.upcoming(db, contact, 1)
        draft = T.reply_smalltalk(first, chat, pet, booking.label(nxt[0].starts_at) if nxt else None, seed=len(text) + contact.id)
        return {"kind": "chat", "draft": str(draft), "sources": [], "task": None,
                "note": "A friendly reply to a thank-you, greeting or goodbye. No task is needed."}
    hits = match_knowledge(db, text)
    if hits:
        return {"kind": "answer", "draft": str(T.reply_answers(first, [h["text"] for h in hits])),
                "sources": [{"id": h["id"], "title": h["title"], "kind": h["kind"]} for h in hits], "task": None,
                "note": "Built word for word from approved clinic knowledge. Read it before you send."}
    t = _task(db, contact, "unknown_knowledge", f"No approved answer for {who}", "The assistant had no approved knowledge for this message.", "normal")
    return {"kind": "unknown", "draft": str(T.reply_followup(first)), "sources": [], "task": {"id": t.id, "title": t.title},
            "note": "There is no approved knowledge for this, so the assistant will not guess. A task was raised. Add the answer in Knowledge & Fees to teach it."}
