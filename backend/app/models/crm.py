"""Clinic CRM tables (docs/CONTRACT_CRM_P1.md). Every row can be demo data (is_demo)."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, CheckConstraint, Date, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UTCDateTime, utcnow

CONSENTS = "('full','transactional','none','unsubscribed')"


class Contact(Base):
    __tablename__ = "contacts"
    __table_args__ = (
        CheckConstraint("kind in ('inquiry','patient')", name="kind"),
        CheckConstraint(f"email_consent in {CONSENTS}", name="email_consent"),
        CheckConstraint(f"sms_consent in {CONSENTS}", name="sms_consent"),
        CheckConstraint("source in ('phone','walk_in','website','referral','after_hours','import','other')", name="source"),
        Index("ix_contacts_last_active_at", "last_active_at"),
        Index("ix_contacts_owner_user_id", "owner_user_id"),
        Index("uq_contacts_phone", "phone", unique=True),
        Index("uq_contacts_email", "email", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(10), default="inquiry")
    display_name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str | None] = mapped_column(String(254))
    email_consent: Mapped[str] = mapped_column(String(14), default="none")
    phone: Mapped[str | None] = mapped_column(String(20))
    sms_consent: Mapped[str] = mapped_column(String(14), default="none")
    consent_source: Mapped[str] = mapped_column(String(200), default="")
    consent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    source: Mapped[str] = mapped_column(String(12), default="other")
    owner_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    ai_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    preference_notes: Mapped[str] = mapped_column(Text, default="")
    # The assistant's memory of older conversations (a short summary), so it remembers beyond the last texts. Erased with the contact.
    ai_memory: Mapped[str] = mapped_column(Text, default="", server_default="")
    ai_memory_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_active_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    # Everything after this moment that came in from the client counts as unread.
    last_read_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    erased_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class ContactNoteHistory(Base):
    __tablename__ = "contact_note_history"
    __table_args__ = (Index("ix_contact_note_history_contact_id", "contact_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"))
    old_text: Mapped[str] = mapped_column(Text, default="")
    new_text: Mapped[str] = mapped_column(Text, default="")
    edited_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    edited_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class Pet(Base):
    __tablename__ = "pets"
    __table_args__ = (
        CheckConstraint("status in ('active','deceased','moved')", name="status"),
        Index("ix_pets_contact_id", "contact_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(80))
    species: Mapped[str] = mapped_column(String(40), default="")
    breed: Mapped[str] = mapped_column(String(80), default="")
    sex: Mapped[str] = mapped_column(String(12), default="")
    birth_date: Mapped[date | None] = mapped_column(Date)
    weight_kg: Mapped[float | None] = mapped_column(Float)
    notes: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(10), default="active")
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class ContactLink(Base):
    __tablename__ = "contact_links"
    __table_args__ = (
        CheckConstraint(
            "relationship in ('friend','spouse','partner','parent','child','neighbour','family','caregiver','other')",
            name="relationship",
        ),
        Index("ix_contact_links_contact_id", "contact_id"),
        Index("ix_contact_links_related_contact_id", "related_contact_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"))
    related_contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"))
    relationship: Mapped[str] = mapped_column(String(12), default="other")
    note: Mapped[str] = mapped_column(String(300), default="")
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class Visit(Base):
    __tablename__ = "visits"
    __table_args__ = (
        CheckConstraint("kind in ('checkup','vaccine','surgery','dental','sick','emergency','other')", name="kind"),
        CheckConstraint("source in ('import','app')", name="source"),
        Index("ix_visits_contact_id", "contact_id"),
        Index("ix_visits_pet_id", "pet_id"),
        Index("ix_visits_visited_at", "visited_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"))
    pet_id: Mapped[int | None] = mapped_column(ForeignKey("pets.id", ondelete="SET NULL"))
    visited_at: Mapped[datetime] = mapped_column(UTCDateTime)
    kind: Mapped[str] = mapped_column(String(12), default="checkup")
    summary: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(8), default="import")
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint("channel in ('sms','email','call')", name="channel"),
        CheckConstraint("direction in ('in','out')", name="direction"),
        CheckConstraint("sender_kind in ('client','staff','ai')", name="sender_kind"),
        CheckConstraint("delivery_state in ('received','dry_run','scheduled_dry_run','queued','sent','failed')", name="delivery_state"),
        Index("ix_messages_contact_id_created_at", "contact_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"))
    channel: Mapped[str] = mapped_column(String(8))
    direction: Mapped[str] = mapped_column(String(4))
    sender_kind: Mapped[str] = mapped_column(String(8))
    sender_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    subject: Mapped[str] = mapped_column(String(150), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    delivery_state: Mapped[str] = mapped_column(String(20), default="received")
    # For a call line: the phone call it describes (transcript and recording buttons).
    phone_call_id: Mapped[int | None] = mapped_column(Integer, index=True)
    # Real sending: the sender's job id (to find its result file) and a fixed failure code.
    send_job_id: Mapped[str | None] = mapped_column(String(32))
    delivery_code: Mapped[str | None] = mapped_column(String(24))
    scheduled_for: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class Note(Base):
    __tablename__ = "notes"
    __table_args__ = (Index("ix_notes_contact_id_created_at", "contact_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"))
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    body: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class NoteMention(Base):
    __tablename__ = "note_mentions"

    note_id: Mapped[int] = mapped_column(ForeignKey("notes.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class Reminder(Base):
    __tablename__ = "reminders"
    __table_args__ = (
        Index("ix_reminders_contact_id", "contact_id"),
        Index("ix_reminders_owner_id", "owner_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"))
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    due_at: Mapped[datetime] = mapped_column(UTCDateTime)
    text: Mapped[str] = mapped_column(String(200), default="")
    done_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    notified_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class Attachment(Base):
    __tablename__ = "attachments"
    __table_args__ = (
        Index("ix_attachments_contact_id", "contact_id"),
        Index("ix_attachments_message_id", "message_id"),
        Index("ix_attachments_note_id", "note_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"))
    message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id", ondelete="SET NULL"))
    note_id: Mapped[int | None] = mapped_column(ForeignKey("notes.id", ondelete="SET NULL"))
    filename: Mapped[str] = mapped_column(String(160), default="")
    mime: Mapped[str] = mapped_column(String(60), default="")
    bytes: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64), default="")
    stored_name: Mapped[str] = mapped_column(String(64), unique=True)
    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint(
            "kind in ('unknown_knowledge','escalation','callback','refill','booking','complaint','emergency_followup','results','other')",
            name="kind",
        ),
        CheckConstraint("classification in ('inquiry','patient')", name="classification"),
        CheckConstraint("priority in ('high','normal','low')", name="priority"),
        CheckConstraint("status in ('open','in_progress','done')", name="status"),
        CheckConstraint("raised_by in ('ai','human','rule')", name="raised_by"),
        Index("ix_tasks_status", "status"),
        Index("ix_tasks_contact_id", "contact_id"),
        Index("ix_tasks_assigned_to", "assigned_to"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), default="other")
    classification: Mapped[str] = mapped_column(String(10), default="inquiry")
    title: Mapped[str] = mapped_column(String(200))
    reason: Mapped[str] = mapped_column(Text, default="")
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"))
    # Set when the after-hours assistant raised the task from an overnight conversation.
    conversation_id: Mapped[int | None] = mapped_column(ForeignKey("conversations.id", ondelete="SET NULL"))
    assigned_to: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    priority: Mapped[str] = mapped_column(String(8), default="normal")
    status: Mapped[str] = mapped_column(String(12), default="open")
    due_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    raised_by: Mapped[str] = mapped_column(String(8), default="human")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    done_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    done_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class CalendarEvent(Base):
    __tablename__ = "calendar_events"
    __table_args__ = (
        CheckConstraint(
            "kind in ('checkup','vaccine','surgery','dental','consult','inquiry_call','staff','reminder','other')",
            name="kind",
        ),
        CheckConstraint("classification in ('inquiry','patient','none')", name="classification"),
        CheckConstraint("status in ('booked','requested','done','cancelled')", name="status"),
        CheckConstraint("source in ('import','app')", name="source"),
        Index("ix_calendar_events_starts_at", "starts_at"),
        Index("ix_calendar_events_contact_id", "contact_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(14), default="other")
    classification: Mapped[str] = mapped_column(String(10), default="none")
    title: Mapped[str] = mapped_column(String(200))
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"))
    pet_id: Mapped[int | None] = mapped_column(ForeignKey("pets.id", ondelete="SET NULL"))
    staff_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    starts_at: Mapped[datetime] = mapped_column(UTCDateTime)
    ends_at: Mapped[datetime] = mapped_column(UTCDateTime)
    status: Mapped[str] = mapped_column(String(10), default="booked")
    source: Mapped[str] = mapped_column(String(8), default="app")
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        CheckConstraint("kind in ('mention','task_assigned','reminder')", name="kind"),
        Index("ix_notifications_user_id_created_at", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(14))
    text: Mapped[str] = mapped_column(String(300), default="")
    link: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    read_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class SavedView(Base):
    __tablename__ = "saved_views"
    __table_args__ = (Index("ix_saved_views_user_id_screen", "user_id", "screen"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    screen: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(60))
    filters_json: Mapped[str] = mapped_column(Text, default="{}")
    columns_json: Mapped[str] = mapped_column(Text, default="[]")
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class AccessLog(Base):
    """Every look at a contact. contact_id has no foreign key: erasing a contact blanks it, the row stays."""

    __tablename__ = "access_log"
    __table_args__ = (
        CheckConstraint("action in ('view','export','erase','send')", name="action"),
        Index("ix_access_log_contact_id", "contact_id"),
        Index("ix_access_log_at", "at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(Integer)
    contact_id: Mapped[int | None] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(8))
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    ip: Mapped[str] = mapped_column(String(64), default="")
    detail: Mapped[str] = mapped_column(String(200), default="")
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
