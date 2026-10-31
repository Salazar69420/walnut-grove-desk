"""Reply syncing: fold incoming texts that the send helper left in <bridge>/send/inbound/ into the conversations.

Each file is one text: {"v":1,"id":"<TELUS id>","from":"+1...","text":"...","at":"<ISO>"}. It is read without following links,
checked strictly, stored once (the TELUS id is kept as "in-<id>"), then deleted. A number we do not know becomes a new inquiry
contact. STOP-type words unsubscribe the person from texts. Then the assistant may reply (services/autoreply.py).
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Contact, Message
from app.services import autoreply, crm, timeutil
from app.services import events as ev

log = logging.getLogger("walnut.inbound")
E164 = re.compile(r"^\+1[2-9][0-9]{2}[2-9][0-9]{6}$")
NAME = re.compile(r"^[0-9]{1,24}\.json$")
STOP_WORDS = {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT"}
START_WORDS = {"START", "UNSTOP", "YES"}


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
                        sms_consent="transactional", consent_source="Texted the clinic first (replies only)", source="other", ai_enabled=False,
                        preference_notes="Created when this number texted the clinic. Add their name.", created_at=at, last_active_at=at)
            db.add(c)
            db.flush()
        msg = Message(contact_id=c.id, channel="sms", direction="in", sender_kind="client", body=text, delivery_state="received", created_at=at,
                      send_job_id=key)
        db.add(msg)
        db.flush()
        word = text.upper().strip(" .!")
        if word in STOP_WORDS:
            c.sms_consent = "unsubscribed"
            ev.record(db, ev.CONTACTS, "sms_unsubscribed", "A person texted STOP and was unsubscribed from texts", "Texting", "contact", c.id)
        elif word in START_WORDS and c.sms_consent == "unsubscribed":
            c.sms_consent = "transactional"
        crm.touch_contact(db, c, at)
        ev.record(db, ev.CONVERSATIONS, "text_received", "A text came in", "Texting", "message", msg.id, {"contact_id": c.id})
        ev.queue_publish(db, "contacts", c.id, None)
        counts["stored"] += 1
        if word not in STOP_WORDS and autoreply.maybe_reply(db, c, msg) is not None:
            counts["replied"] += 1
        db.commit()
    return counts


def run_once() -> dict[str, int]:
    from app import db as app_db
    with app_db.get_sessionmaker()() as s:
        try:
            return ingest(s)
        except Exception as e:  # never let a bad file stop the loop; nothing from the text is logged
            s.rollback()
            log.error("inbound ingest failed (%s)", type(e).__name__)
            return {}
