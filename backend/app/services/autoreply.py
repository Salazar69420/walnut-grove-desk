"""Automatic replies to incoming texts. A pilot: ONLY for numbers on the texting test list, whatever the mode.

The reply is exactly what "Suggest reply" would draft (app/assistant/replies.py: emergency first, no medical advice, approved
knowledge only, a task when it does not know), signed as the automated assistant, and sent through the one send path, so consent,
the texting mode and the helper's own checks all still apply. At most MAX_PER_HOUR automatic replies per person, to stop loops
with other automatic senders.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.aftercare import templates as T
from app.assistant import replies
from app.models import Contact, Message, Task
from app.services import crm, sender, settings_store, sms_mode, timeutil
from app.services import events as ev

KEY = "auto_reply"
MAX_PER_HOUR = 6


def is_on(db: Session) -> bool:
    return bool(settings_store.get_raw(db, KEY, False))


def why_not(db: Session, contact: Contact) -> str | None:
    """None when an automatic reply may be sent to this person, else the reason (for the event log, no personal data)."""
    if not is_on(db):
        return "auto_reply_off"
    m = sms_mode.get(db)
    if m["mode"] == "practice":
        return "practice_mode"
    if not contact.phone or contact.phone not in m["test_numbers"]:
        return "not_a_test_number"
    if not contact.ai_enabled:
        return "assistant_off_for_person"
    since = timeutil.now_utc() - timedelta(hours=1)
    n = db.scalar(select(func.count()).select_from(Message).where(Message.contact_id == contact.id, Message.sender_kind == "ai",
                                                                     Message.direction == "out", Message.created_at > since)) or 0
    if n >= MAX_PER_HOUR:
        return "hourly_limit"
    return None


def maybe_reply(db: Session, contact: Contact, inbound: Message) -> Message | None:
    reason = why_not(db, contact)
    if reason:
        ev.record(db, ev.CONVERSATIONS, "auto_reply_skipped", f"No automatic reply ({reason.replace('_', ' ')})", "AI assistant", "contact", contact.id,
                  {"reason": reason}, publish=False)
        return None
    out = replies.suggest(db, contact, inbound.body, act=True)
    try:
        msg = sender.send_to_contact(db, contact, None, channel="sms", body=str(T.auto_signed(out["draft"])), sender_kind="ai")
    except crm.CrmError as e:
        db.add(Task(kind="escalation", classification=contact.kind, title=f"Automatic reply not sent to {contact.display_name}"[:200],
                    reason=f"The assistant could not send its reply: {e.detail}"[:2000], contact_id=contact.id, priority="high", status="open",
                    raised_by="ai", created_at=timeutil.now_utc()))
        return None
    ev.record(db, ev.CONVERSATIONS, "auto_reply_sent", f"The assistant replied by itself ({out['kind']}{', ' + out['model'] if out.get('model') else ', rules'})",
              "AI assistant", "message", msg.id, {"kind": out["kind"], "sources": [s["id"] for s in out["sources"]], "model": out.get("model"), "usage": out.get("usage")})
    return msg
