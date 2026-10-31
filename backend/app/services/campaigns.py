"""Campaigns: who a message would reach, who is left out and why, and the dry-run send.

Consent rules (CASL): a care message (reminder, follow-up) may go to anyone with full or care-only consent. A promo message
(anything that sells or announces) needs full consent. Unsubscribed and no-consent contacts are never messaged. Quiet hours
block a run. Real sending is not switched on: every recipient only gets a "would send" row through send_to_contact().
"""
from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Campaign, CampaignRecipient, Contact, Pet, User
from app.services import crm, sender
from app.services import events as ev
from app.services import settings_store, timeutil

MERGE_FIELDS = ("first_name", "pet_name", "clinic_name")
SKIP_LABELS = {
    "unsubscribed": "Unsubscribed",
    "no_consent": "No consent on file",
    "care_only": "Care messages only (this is a promo)",
    "no_address": "No phone or email for this channel",
    "duplicate": "Same number or address as someone already included",
}
MAX_RECIPIENTS = 2000
QUIET_START, QUIET_END = 21, 8  # local hours: no run from 9 pm to 8 am
SEGMENT_KEYS = ("q", "kind", "owner", "email_consent", "sms_consent", "species", "visits_min", "visits_max",
                "last_visit_from", "last_visit_to", "has_open_task", "ai", "source")
_FIELD_RX = re.compile(r"\{(\w+)\}")


def check_template(body: str, subject: str = "") -> None:
    for text in (body, subject):
        for name in _FIELD_RX.findall(text or ""):
            if name not in MERGE_FIELDS:
                raise crm.CrmError(400, "Unknown merge field {" + name + "}. You can use " + ", ".join("{" + m + "}" for m in MERGE_FIELDS) + ".")


def in_quiet_hours(now=None) -> bool:
    h = timeutil.local(now or timeutil.now_utc()).hour
    return h >= QUIET_START or h < QUIET_END


def clean_segment(raw: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k in SEGMENT_KEYS:
        v = raw.get(k)
        if v is None or v == "":
            continue
        if isinstance(v, date):
            v = v.isoformat()
        out[k] = v
    return out


def filters_from(seg: dict[str, Any]):
    """Build the same filter object the Contacts list uses (every field passed explicitly)."""
    from app.api.contacts import ContactFilters

    def d(key):
        v = seg.get(key)
        return date.fromisoformat(v) if isinstance(v, str) and v else None

    return ContactFilters(
        q=seg.get("q"), kind=seg.get("kind"), owner=seg.get("owner"), email_consent=seg.get("email_consent"),
        sms_consent=seg.get("sms_consent"), species=seg.get("species"), visits_min=seg.get("visits_min"), visits_max=seg.get("visits_max"),
        last_visit_from=d("last_visit_from"), last_visit_to=d("last_visit_to"), has_open_task=seg.get("has_open_task"),
        ai=seg.get("ai"), source=seg.get("source"), sort="name", dir="asc",
    )


def render(db: Session, text: str, contact: Contact, clinic_name: str, channel: str, *, footer: bool) -> str:
    pet = db.scalar(select(Pet.name).where(Pet.contact_id == contact.id, Pet.status == "active").order_by(Pet.id).limit(1))
    first = (contact.display_name.split() or [""])[0]
    out = text.replace("{first_name}", first).replace("{pet_name}", pet or "your pet").replace("{clinic_name}", clinic_name)
    if footer:
        if channel == "sms" and "stop" not in out.lower():
            out += "\nReply STOP to opt out."
        if channel == "email" and "unsubscribe" not in out.lower():
            out += "\n\nTo stop these emails, reply with the word unsubscribe."
    return out


def skip_reason(contact: Contact, channel: str, purpose: str) -> str | None:
    consent = contact.sms_consent if channel == "sms" else contact.email_consent
    address = contact.phone if channel == "sms" else contact.email
    if consent == "unsubscribed":
        return "unsubscribed"
    if not address:
        return "no_address"
    if consent not in crm.CONSENT_CAN_SEND:
        return "no_consent"
    if purpose == "promo" and consent != "full":
        return "care_only"
    return None


def evaluate(db: Session, user: User, channel: str, purpose: str, segment: dict[str, Any]) -> tuple[list[tuple[Contact, str | None]], int]:
    """Every matched contact with the reason they are left out (None means they would get the message)."""
    from app.api.contacts import contacts_statement

    rows = db.scalars(contacts_statement(filters_from(segment), user).order_by(Contact.display_name, Contact.id).limit(MAX_RECIPIENTS + 1)).all()
    if len(rows) > MAX_RECIPIENTS:
        raise crm.CrmError(400, f"That segment is larger than {MAX_RECIPIENTS} contacts. Narrow the filters.")
    seen: set[str] = set()
    out: list[tuple[Contact, str | None]] = []
    for c in rows:
        reason = skip_reason(c, channel, purpose)
        if reason is None:
            key = (c.phone if channel == "sms" else c.email) or ""
            if key in seen:
                reason = "duplicate"
            else:
                seen.add(key)
        out.append((c, reason))
    return out, len(rows)


def tally(evaluated: list[tuple[Contact, str | None]]) -> dict[str, Any]:
    skipped: dict[str, int] = {}
    for _c, reason in evaluated:
        if reason:
            skipped[reason] = skipped.get(reason, 0) + 1
    eligible = sum(1 for _c, r in evaluated if r is None)
    return {"matched": len(evaluated), "eligible": eligible, "skipped": skipped,
            "skipped_list": [{"reason": k, "label": SKIP_LABELS[k], "count": v} for k, v in sorted(skipped.items(), key=lambda kv: -kv[1])]}


def samples(db: Session, evaluated, body: str, subject: str, channel: str, clinic_name: str, n: int = 3) -> list[dict[str, Any]]:
    out = []
    for c, reason in evaluated:
        if reason is None:
            out.append({"contact": {"id": c.id, "display_name": c.display_name},
                        "subject": render(db, subject, c, clinic_name, channel, footer=False) if channel == "email" else "",
                        "text": render(db, body, c, clinic_name, channel, footer=True)})
            if len(out) >= n:
                break
    return out


def clinic_name(db: Session) -> str:
    try:
        return str(settings_store.get(db, "clinic_name") or "the clinic")
    except Exception:  # noqa: BLE001 - a missing setting must not block a preview
        return "the clinic"


def run_dry(db: Session, camp: Campaign, user: User) -> dict[str, Any]:
    """Create one dry-run message per eligible recipient. Raises CrmError when not allowed."""
    if camp.status != "approved":
        raise crm.CrmError(409, "Only an approved campaign can be run.")
    if in_quiet_hours():
        raise crm.CrmError(409, "Quiet hours: campaigns do not run between 9 pm and 8 am.")
    segment = json.loads(camp.segment_json or "{}")
    evaluated, _ = evaluate(db, user, camp.channel, camp.purpose, segment)
    name = clinic_name(db)
    sent = 0
    skipped: dict[str, int] = {}
    for c, reason in evaluated:
        msg_id = None
        state = "skipped"
        if reason is None:
            try:
                msg = sender.send_to_contact(
                    db, c, user, channel=camp.channel, body=render(db, camp.body, c, name, camp.channel, footer=True),
                    subject=render(db, camp.subject, c, name, camp.channel, footer=False) if camp.channel == "email" else "",
                )
                msg_id, state, sent = msg.id, "dry_run_sent", sent + 1
            except crm.CrmError:
                reason = "no_consent"
        if state == "skipped":
            skipped[reason or "no_consent"] = skipped.get(reason or "no_consent", 0) + 1
        db.add(CampaignRecipient(campaign_id=camp.id, contact_id=c.id, state=state, skipped_reason=(reason or "") if state == "skipped" else "",
                                 message_id=msg_id, is_demo=camp.is_demo))
    camp.status = "done_dry_run"
    camp.ran_at = timeutil.now_utc()
    counts = {"matched": len(evaluated), "sent": sent, "skipped": skipped}
    camp.counts_json = json.dumps(counts)
    ev.record(db, ev.CAMPAIGNS, "campaign_run", f"{user.display_name} ran the campaign {camp.name} in practice mode ({sent} messages)", user,
              "campaign", camp.id, {"sent": sent})
    return counts
