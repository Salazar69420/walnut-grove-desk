"""Knowledge and fees, the phone call log, and campaigns (docs/PLAN_CLINIC_CRM.md phases 4, 5 and 7).

Every row can be demo data (is_demo). Nothing here sends anything: campaigns only create dry-run messages.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, CheckConstraint, Date, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UTCDateTime, utcnow

KNOWLEDGE_CATEGORIES = (
    "clinic_info", "fees", "patient_knowledge", "patient_communication", "inquiry_communication", "laws_regulations", "specials",
)
_CATS = "(" + ",".join(f"'{c}'" for c in KNOWLEDGE_CATEGORIES) + ")"


class KnowledgeEntry(Base):
    """One fact the assistant may use. Only status 'approved' entries are ever used. Pending entries wait for a person."""

    __tablename__ = "knowledge_entries"
    __table_args__ = (
        CheckConstraint(f"category in {_CATS}", name="category"),
        CheckConstraint("status in ('pending','approved','declined','retired')", name="status"),
        CheckConstraint("proposed_by in ('human','ai','import')", name="proposed_by"),
        CheckConstraint("source_kind in ('manual','conversation','call','answer_sheet')", name="source_kind"),
        Index("ix_knowledge_entries_status", "status"),
        Index("ix_knowledge_entries_category", "category"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    category: Mapped[str] = mapped_column(String(24), default="clinic_info")
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(10), default="pending")
    proposed_by: Mapped[str] = mapped_column(String(6), default="human")
    source_kind: Mapped[str] = mapped_column(String(14), default="manual")
    # A short pointer such as "call 2026-10-14 09:12" or a conversation number. Never a phone number or a name.
    source_ref: Mapped[str] = mapped_column(String(80), default="")
    source_quote: Mapped[str] = mapped_column(String(300), default="")
    confidence: Mapped[int | None] = mapped_column(Integer)
    # The approved entry this one would replace, and its text at the time (for the side-by-side view).
    supersedes_id: Mapped[int | None] = mapped_column(ForeignKey("knowledge_entries.id", ondelete="SET NULL"))
    previous_body: Mapped[str] = mapped_column(Text, default="")
    conflicts: Mapped[bool] = mapped_column(Boolean, default=False)
    proposed_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    proposed_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    decline_reason: Mapped[str] = mapped_column(String(200), default="")
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class Fee(Base):
    """A price the assistant may quote once approved. Prices in whole cents; null means "ask the clinic"."""

    __tablename__ = "fees"
    __table_args__ = (
        CheckConstraint("status in ('pending','approved','retired')", name="status"),
        Index("ix_fees_status", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    service: Mapped[str] = mapped_column(String(120))
    price_cents: Mapped[int | None] = mapped_column(Integer)
    price_prefix: Mapped[str] = mapped_column(String(12), default="")  # "from", "about"
    unit: Mapped[str] = mapped_column(String(40), default="")  # "per visit", "per night"
    note: Mapped[str] = mapped_column(String(300), default="")
    effective_from: Mapped[date | None] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(10), default="pending")
    proposed_by: Mapped[str] = mapped_column(String(6), default="human")
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class PhoneCall(Base):
    """One call in the call log. Who handled it: a person, the assistant, or nobody (missed)."""

    __tablename__ = "phone_calls"
    __table_args__ = (
        CheckConstraint("direction in ('inbound','outbound','unknown')", name="direction"),
        CheckConstraint("handled_by in ('human','ai','none')", name="handled_by"),
        CheckConstraint("result in ('answered','booked','callback_task','voicemail','missed','emergency_routed','info_given')", name="result"),
        CheckConstraint("call_type in ('inquiry','patient','unknown')", name="call_type"),
        CheckConstraint("source in ('demo','telus','retell','import')", name="source"),
        Index("ix_phone_calls_started_at", "started_at"),
        Index("uq_phone_calls_external_ref", "external_ref", unique=True),
        Index("ix_phone_calls_contact_id", "contact_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime)
    direction: Mapped[str] = mapped_column(String(8), default="inbound")
    handled_by: Mapped[str] = mapped_column(String(6), default="human")
    agent_label: Mapped[str] = mapped_column(String(80), default="")
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id", ondelete="SET NULL"))
    phone: Mapped[str | None] = mapped_column(String(20))
    duration_seconds: Mapped[int] = mapped_column(Integer, default=0)
    result: Mapped[str] = mapped_column(String(18), default="answered")
    call_type: Mapped[str] = mapped_column(String(8), default="unknown")
    short_note: Mapped[str] = mapped_column(String(240), default="")
    # Only invented demo text lives here. Real transcripts stay in the private store.
    transcript: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(8), default="demo")
    # Keyed hash of the source recording's identity, so an import can be run again without duplicates. Never a file name or number.
    external_ref: Mapped[str | None] = mapped_column(String(40))
    # TELUS recording id (digits only), used to fetch the audio on request. Not a phone number.
    recording_id: Mapped[str | None] = mapped_column(String(24))
    # Made by the AI from the transcript: a short summary (owners and tasks only) and the reason for the call (for reports).
    summary: Mapped[str | None] = mapped_column(Text)
    intent: Mapped[str | None] = mapped_column(String(30))
    # '' not started, 'queued' waiting for the audio copy, 'done', 'failed', 'skipped' (too short)
    transcript_state: Mapped[str] = mapped_column(String(10), default="", server_default="")
    transcript_tries: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class Campaign(Base):
    """A message to many contacts. Runs only as a dry run: each recipient gets a "would send" row."""

    __tablename__ = "campaigns"
    __table_args__ = (
        CheckConstraint("channel in ('sms','email')", name="channel"),
        CheckConstraint("purpose in ('care','promo')", name="purpose"),
        CheckConstraint("status in ('draft','awaiting_approval','approved','done_dry_run','cancelled')", name="status"),
        Index("ix_campaigns_status", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    channel: Mapped[str] = mapped_column(String(5), default="sms")
    # care: reminders and follow-ups for existing patients. promo: anything that sells or announces. promo needs full consent.
    purpose: Mapped[str] = mapped_column(String(5), default="care")
    status: Mapped[str] = mapped_column(String(18), default="draft")
    segment_json: Mapped[str] = mapped_column(Text, default="{}")
    subject: Mapped[str] = mapped_column(String(150), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    ran_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    counts_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class CampaignRecipient(Base):
    __tablename__ = "campaign_recipients"
    __table_args__ = (
        CheckConstraint("state in ('dry_run_sent','skipped')", name="state"),
        Index("ix_campaign_recipients_campaign_id", "campaign_id"),
        Index("ix_campaign_recipients_contact_id", "contact_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"))
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"))
    state: Mapped[str] = mapped_column(String(12))
    skipped_reason: Mapped[str] = mapped_column(String(24), default="")
    message_id: Mapped[int | None] = mapped_column(Integer)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
