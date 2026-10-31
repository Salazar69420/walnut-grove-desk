"""Reply syncing: fold incoming texts that the send helper left in <bridge>/send/inbound/ into the conversations.

Each file is one text: {"v":1,"id":"<TELUS id>","from":"+1...","text":"...","at":"<ISO>"}. It is read without following links,
checked strictly, stored once (the TELUS id is kept as "in-<id>"), then deleted. A number we do not know becomes a new inquiry
contact. STOP-type words unsubscribe the person from texts.

Opt-in: a person who has not agreed to texts (consent "none", which is everyone we only know from calls or a first text) gets ONE
short opt-in question when they text us (at most once a day), and nothing else. Replying Y opts them in; then their earlier question
is answered. Nobody is ever texted the opt-in question unless they texted first. Then the assistant may reply (services/autoreply.py).
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from datetime import timedelta

from app.aftercare import templates as T
from app.models import Contact, Message
from app.services import autoreply, crm, sender, sms_mode, timeutil
from app.services import events as ev

log = logging.getLogger("walnut.inbound")
E164 = re.compile(r"^\+1[2-9][0-9]{2}[2-9][0-9]{6}$")
NAME = re.compile(r"^[0-9]{1,24}\.json$")
STOP_WORDS = {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT"}
START_WORDS = {"START", "UNSTOP", "YES"}
OPTIN_WORDS = {"Y", "YES", "YEP", "YEAH", "START", "UNSTOP", "OK", "OKAY", "SURE", "YES PLEASE", "Y PLEASE"}
OPTIN_EVERY = timedelta(hours=24)


def _dir() -> str:
    from app.api import admin_telus as b
    return os.path.join(b._bridge(), "send", "inbound")


def ingest(db: Session) -> dict[str, int]:
    from app.api import admin_telus as b
    d = _dir()
    counts = {"stored": 0, "duplicate": 0, "bad": 0, "replied": 0}
    if not b._real_dir(d):
        return counts
    try:
        names = sorted(n for n in os.listdir(d) if NAME.fullmatch(n))[:100]
    except OSError:
        return counts
    for name in names:
        data = b.read_json_file(d, name)
        try:
            os.unlink(os.path.join(d, name))
        except OSError:
            pass
        ok = (isinstance(data, dict) and data.get("v") == 1 and isinstance(data.get("id"), str) and re.fullmatch(r"[0-9]{1,24}", data["id"])
              and isinstance(data.get("from"), str) and E164.fullmatch(data["from"]) and isinstance(data.get("text"), str) and 0 < len(data["text"]) <= 1600)
        if not ok:
            counts["bad"] += 1
            continue
        key = f"in-{data['id']}"
        if db.scalar(select(Message.id).where(Message.send_job_id == key)) is not None:
            counts["duplicate"] += 1
            continue
        try:
            at = crm.aware(datetime.fromisoformat(str(data.get("at", "")).replace("Z", "+00:00")))
        except ValueError:
            at = timeutil.now_utc()
        phone, text = data["from"], data["text"].strip()
        c = db.scalar(select(Contact).where(Contact.phone == phone, Contact.erased_at.is_(None)))
        if c is None:
            c = Contact(kind="inquiry", display_name=f"Text from {crm.phone_display(phone)}", phone=phone, email_consent="none",
                        sms_consent="none", consent_source="Texted the clinic first, not opted in yet", source="other",
                        ai_enabled=phone in sms_mode.get(db)["test_numbers"],  # a texting test number is a pilot: assistant on
                        preference_notes="Created when this number texted the clinic. Add their name.", created_at=at, last_active_at=at)
            db.add(c)
            db.flush()
        msg = Message(contact_id=c.id, channel="sms", direction="in", sender_kind="client", body=text, delivery_state="received", created_at=at,
                      send_job_id=key)
        db.add(msg)
        db.flush()
        word = re.sub(r"\s+", " ", text.upper().strip(" .!"))
        crm.touch_contact(db, c, at)
        ev.record(db, ev.CONVERSATIONS, "text_received", "A text came in", "Texting", "message", msg.id, {"contact_id": c.id})
        ev.queue_publish(db, "contacts", c.id, None)
        counts["stored"] += 1
        if word in STOP_WORDS:
            c.sms_consent = "unsubscribed"
            ev.record(db, ev.CONTACTS, "sms_unsubscribed", "A person texted STOP and was unsubscribed from texts", "Texting", "contact", c.id)
        elif word in OPTIN_WORDS and c.sms_consent in ("none", "unsubscribed") and (c.sms_consent == "unsubscribed" or _asked(db, c)):
            opted_in(db, c, at)
            counts["opted_in"] = counts.get("opted_in", 0) + 1
            earlier = _waiting_question(db, c, msg)
            if earlier is not None and autoreply.maybe_reply(db, c, earlier) is not None:
                counts["replied"] += 1
        elif c.sms_consent == "none":
            if ask_optin(db, c):
                counts["optin_asked"] = counts.get("optin_asked", 0) + 1
        elif autoreply.maybe_reply(db, c, msg) is not None:
            counts["replied"] += 1
        db.commit()
    return counts


def _optin_sent(db: Session, c: Contact, since) -> bool:
    body = str(T.optin_prompt())
    return db.scalar(select(Message.id).where(Message.contact_id == c.id, Message.direction == "out", Message.body == body,
                                              Message.created_at > since).limit(1)) is not None


def _asked(db: Session, c: Contact) -> bool:
    """We asked them to reply Y (a lone "ok" from someone we never asked is not an opt-in)."""
    return _optin_sent(db, c, timeutil.now_utc() - timedelta(days=30))


def ask_optin(db: Session, c: Contact) -> bool:
    """Send the opt-in question once a day at most, and only because they just texted us."""
    if _optin_sent(db, c, timeutil.now_utc() - OPTIN_EVERY):
        return False
    try:
        m = sender.send_to_contact(db, c, None, channel="sms", body=str(T.optin_prompt()), sender_kind="ai", optin=True)
    except crm.CrmError as e:
        ev.record(db, ev.CONTACTS, "optin_not_sent", f"The opt-in question was not sent: {e.detail}", "Texting", "contact", c.id, publish=False)
        return False
    ev.record(db, ev.CONTACTS, "optin_asked", "A person who is not opted in texted us, so they were asked to reply Y to get texts", "Texting",
              "message", m.id, {"contact_id": c.id})
    return True


def opted_in(db: Session, c: Contact, at) -> None:
    c.sms_consent = "transactional"
    c.consent_source = f"Replied Y by text on {timeutil.local(at).strftime('%Y-%m-%d')}"
    ev.record(db, ev.CONTACTS, "sms_opted_in", f"{c.display_name} replied Y and opted in to texts", "Texting", "contact", c.id)
    first = T.first_name(c.display_name)
    try:
        sender.send_to_contact(db, c, None, channel="sms", body=str(T.optin_confirmed(first)), sender_kind="ai")
    except crm.CrmError:
        pass


def _waiting_question(db: Session, c: Contact, yes: Message) -> Message | None:
    """Their last real text before they said Y (within a day), so it can be answered now that they opted in."""
    since = timeutil.now_utc() - timedelta(hours=24)
    rows = db.scalars(select(Message).where(Message.contact_id == c.id, Message.direction == "in", Message.channel == "sms", Message.id != yes.id,
                                            Message.created_at > since).order_by(Message.created_at.desc(), Message.id.desc()).limit(5)).all()
    for m in rows:
        w = re.sub(r"\s+", " ", (m.body or "").upper().strip(" .!"))
        if w not in OPTIN_WORDS and w not in STOP_WORDS and len(w) > 2:
            return m
    return None


def run_once() -> dict[str, int]:
    from app import db as app_db
    with app_db.get_sessionmaker()() as s:
        try:
            return ingest(s)
        except Exception as e:  # never let a bad file stop the loop; nothing from the text is logged
            s.rollback()
            log.error("inbound ingest failed (%s)", type(e).__name__)
            return {}
