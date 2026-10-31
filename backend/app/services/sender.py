"""The one place a message to a client is created.

Every send goes through send_to_contact(): it checks consent and the address, then writes the
message as a dry run plus an outbox row (would_send). Nothing is ever really sent.
"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Attachment, Contact, Message, Outbox, User
from app.services import crm
from app.services import events as ev
from app.services import timeutil

log = logging.getLogger("walnut.send")

MAX_SCHEDULE_DAYS = 366


def consent_problem(contact: Contact, channel: str) -> str | None:
    """A plain sentence when this contact may not be written to on this channel, else None."""
    if contact.erased_at is not None:
        return "This contact has been erased."
    if channel == "sms":
        if not contact.phone:
            return "This contact has no phone number, so a text cannot be sent."
        consent = contact.sms_consent
        word = "texts"
    elif channel == "email":
        if not contact.email:
            return "This contact has no email address, so an email cannot be sent."
        consent = contact.email_consent
        word = "emails"
    else:
        return "Only texts and emails can be sent."
    if consent == "unsubscribed":
        return f"This contact unsubscribed from {word}. Nothing was sent."
    if consent not in crm.CONSENT_CAN_SEND:
        return f"This contact has not agreed to {word}. Ask for their consent first."
    return None


def link_attachments(db: Session, contact_id: int, ids: list[int], *, message_id: int | None = None, note_id: int | None = None) -> None:
    if not ids:
        return
    rows = db.scalars(select(Attachment).where(Attachment.id.in_(set(ids)))).all()
    if len(rows) != len(set(ids)):
        raise crm.CrmError(400, "One of the attachments was not found.")
    for a in rows:
        if a.contact_id != contact_id:
            raise crm.CrmError(400, "That attachment belongs to someone else.")
        if a.message_id is not None or a.note_id is not None:
            raise crm.CrmError(400, "That attachment is already used.")
    for a in rows:
        a.message_id = message_id
        a.note_id = note_id


def send_to_contact(
    db: Session,
    contact: Contact,
    user: User | None,
    *,
    channel: str,
    body: str,
    subject: str = "",
    scheduled_for: datetime | None = None,
    attachment_ids: list[int] | None = None,
    sender_kind: str = "staff",
) -> Message:
    """Create a dry-run message. Raises CrmError(409) when consent or the address is missing."""
    problem = consent_problem(contact, channel)
    if problem:
        raise crm.CrmError(409, problem)
    now = timeutil.now_utc()
    if scheduled_for is not None:
        if scheduled_for <= now:
            raise crm.CrmError(400, "Pick a time in the future for a scheduled message.")
        if (scheduled_for - now).days > MAX_SCHEDULE_DAYS:
            raise crm.CrmError(400, "Scheduled messages can be at most a year away.")
    msg = Message(
        contact_id=contact.id, channel=channel, direction="out", sender_kind=sender_kind,
        sender_user_id=user.id if user else None, subject=(subject or "")[:150] if channel == "email" else "",
        body=body, delivery_state="scheduled_dry_run" if scheduled_for else "dry_run", scheduled_for=scheduled_for, created_at=now,
    )
    db.add(msg)
    db.flush()
    link_attachments(db, contact.id, attachment_ids or [], message_id=msg.id)
    out_body = f"Subject: {msg.subject}\n\n{body}" if channel == "email" and msg.subject else body
    db.add(Outbox(channel=channel, to_label=contact.display_name[:160], body=out_body, status="would_send",
                  source_type="message", source_id=str(msg.id), created_by=user.display_name if user else "AI assistant",
                  created_at=now))
    crm.log_access(db, user, contact.id, "send", detail=channel)
    crm.touch_contact(db, contact, now)
    word = "text" if channel == "sms" else "email"
    who = user.display_name if user else "The assistant"
    ev.record(
        db, ev.CONVERSATIONS, "message_scheduled" if scheduled_for else "message_queued",
        f"{who} {'scheduled' if scheduled_for else 'sent'} a {word} (practice mode)", user or "AI assistant", "message", msg.id,
        {"contact_id": contact.id, "channel": channel},
    )
    ev.queue_publish(db, "contacts", contact.id, None)
    log.info("would send %s (message %s)", channel, msg.id)
    return msg
