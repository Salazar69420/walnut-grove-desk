"""Operational tables (all demo data is invented)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UTCDateTime, utcnow


class Suggestion(Base):
    __tablename__ = "suggestions"
    __table_args__ = (CheckConstraint("status in ('open','approved','dismissed')", name="status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    rule_key: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(300))
    why: Mapped[str] = mapped_column(Text, default="")
    evidence_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(12), default="open")
    decided_by: Mapped[str | None] = mapped_column(String(120))
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        CheckConstraint("channel in ('text','call')", name="channel"),
        CheckConstraint("kind in ('booking','emergency','callback')", name="kind"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    channel: Mapped[str] = mapped_column(String(8))
    client_label: Mapped[str] = mapped_column(String(120), default="")
    pet_label: Mapped[str] = mapped_column(String(120), default="")
    kind: Mapped[str] = mapped_column(String(12))
    # booking: new|confirmed|entered|called ; emergency: new|handled ; callback: new|handled
    status: Mapped[str] = mapped_column(String(12), default="new")
    preferred_slot: Mapped[str | None] = mapped_column(String(80))
    summary: Mapped[str] = mapped_column(Text, default="")
    messages_json: Mapped[str] = mapped_column(Text, default="[]")
    source: Mapped[str] = mapped_column(String(12), default="seed")
    is_test: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Callback(Base):
    __tablename__ = "callbacks"
    __table_args__ = (
        CheckConstraint("status in ('new','claimed','done')", name="status"),
        CheckConstraint("priority in ('high','normal')", name="priority"),
        Index("ix_callbacks_status", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    client_label: Mapped[str] = mapped_column(String(120))
    pet_label: Mapped[str] = mapped_column(String(120), default="")
    what: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(20))
    via: Mapped[str] = mapped_column(String(20))
    received_at: Mapped[datetime] = mapped_column(UTCDateTime)
    priority: Mapped[str] = mapped_column(String(8), default="normal")
    red_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    owner_name: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(8), default="new")
    source: Mapped[str] = mapped_column(String(12), default="seed")
    conversation_id: Mapped[int | None] = mapped_column(ForeignKey("conversations.id"))
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class LabResult(Base):
    __tablename__ = "lab_results"
    __table_args__ = (CheckConstraint("stage between 0 and 2", name="stage"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    pet_label: Mapped[str] = mapped_column(String(120))
    owner_label: Mapped[str] = mapped_column(String(120))
    test: Mapped[str] = mapped_column(String(120))
    received_at: Mapped[datetime] = mapped_column(UTCDateTime)
    stage: Mapped[int] = mapped_column(Integer, default=0)  # 0 Received, 1 Doctor reviewed, 2 Client told
    reviewed_by: Mapped[str | None] = mapped_column(String(120))
    told_by: Mapped[str | None] = mapped_column(String(120))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Draft(Base):
    __tablename__ = "drafts"
    __table_args__ = (CheckConstraint("status in ('pending','sent')", name="status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(80))
    to_label: Mapped[str] = mapped_column(String(160))
    body: Mapped[str] = mapped_column(Text)
    channel: Mapped[str] = mapped_column(String(8), default="text")
    status: Mapped[str] = mapped_column(String(8), default="pending")
    created_by_tool: Mapped[str] = mapped_column(String(40), default="Recall sender")
    edited_by: Mapped[str | None] = mapped_column(String(120))
    approved_by: Mapped[str | None] = mapped_column(String(120))
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    suggestion_id: Mapped[int | None] = mapped_column(ForeignKey("suggestions.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Shift(Base):
    __tablename__ = "shifts"
    __table_args__ = (
        CheckConstraint("weekday between 0 and 6", name="weekday"),
        CheckConstraint("role in ('desk','nurse','doc','open')", name="role"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    weekday: Mapped[int] = mapped_column(Integer)  # 0=Mon .. 6=Sun
    person_label: Mapped[str] = mapped_column(String(120))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    role: Mapped[str] = mapped_column(String(8))
    start_min: Mapped[int] = mapped_column(Integer)
    end_min: Mapped[int] = mapped_column(Integer)
    proposed: Mapped[bool] = mapped_column(Boolean, default=False)
    suggestion_id: Mapped[int | None] = mapped_column(ForeignKey("suggestions.id"))


class Hospital(Base):
    __tablename__ = "hospitals"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    place: Mapped[str] = mapped_column(String(120), default="")
    phone: Mapped[str] = mapped_column(String(40), default="")
    address: Mapped[str] = mapped_column(String(300), default="")
    hours_claim: Mapped[str] = mapped_column(String(300), default="")
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    confirmed_by: Mapped[str | None] = mapped_column(String(120))
    is_placeholder: Mapped[bool] = mapped_column(Boolean, default=False)
    sort: Mapped[int] = mapped_column(Integer, default=0)


class VisitType(Base):
    __tablename__ = "visit_types"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    minutes: Mapped[int] = mapped_column(Integer)
    bookable: Mapped[bool] = mapped_column(Boolean, default=False)
    sort: Mapped[int] = mapped_column(Integer, default=0)


class OpenSlot(Base):
    __tablename__ = "open_slots"

    id: Mapped[int] = mapped_column(primary_key=True)
    start: Mapped[datetime] = mapped_column(UTCDateTime)
    visit_type_id: Mapped[int | None] = mapped_column(ForeignKey("visit_types.id"))
    provider: Mapped[str] = mapped_column(String(120), default="Dr. Garg")
    taken: Mapped[bool] = mapped_column(Boolean, default=False)


class AppointmentRequest(Base):
    __tablename__ = "appointment_requests"
    __table_args__ = (
        CheckConstraint("status in ('requested','confirmed','entered','called')", name="status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), index=True)
    visit_type_id: Mapped[int | None] = mapped_column(ForeignKey("visit_types.id"))
    slot_start: Mapped[datetime | None] = mapped_column(UTCDateTime)
    status: Mapped[str] = mapped_column(String(12), default="requested")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class EmergencyRouting(Base):
    __tablename__ = "emergency_routings"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), index=True)
    hospital_id: Mapped[int | None] = mapped_column(ForeignKey("hospitals.id"))
    matched_terms: Mapped[str] = mapped_column(Text, default="[]")  # JSON list
    layer: Mapped[str] = mapped_column(String(8), default="1")  # '1' | '2' | 'unsure'
    followed_up_by: Mapped[str | None] = mapped_column(String(120))
    followed_up_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class SimSession(Base):
    __tablename__ = "sim_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    channel: Mapped[str] = mapped_column(String(8), default="text")
    node: Mapped[str] = mapped_column(String(40), default="start")
    picks_json: Mapped[str] = mapped_column(Text, default="{}")
    messages_json: Mapped[str] = mapped_column(Text, default="[]")
    done: Mapped[bool] = mapped_column(Boolean, default=False)
    conversation_id: Mapped[int | None] = mapped_column(ForeignKey("conversations.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Outbox(Base):
    """Nothing is ever sent. Adapters write rows with status would_send."""

    __tablename__ = "outbox"
    __table_args__ = (
        CheckConstraint("channel in ('sms','voice')", name="channel"),
        CheckConstraint("status in ('would_send')", name="status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    channel: Mapped[str] = mapped_column(String(8))
    to_label: Mapped[str] = mapped_column(String(160))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12), default="would_send")
    source_type: Mapped[str] = mapped_column(String(40), default="")
    source_id: Mapped[str] = mapped_column(String(40), default="")
    created_by: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class CallLogDemo(Base):
    """Synthetic example calls (source=demo) for the missed-call and week-compare numbers."""

    __tablename__ = "call_log_demo"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime)
    weekday: Mapped[int] = mapped_column(Integer)
    hour: Mapped[int] = mapped_column(Integer)
    answered: Mapped[bool] = mapped_column(Boolean, default=True)
    rang_again_within_hour: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(12), default="demo")


class CallAnalytic(Base):
    """Aggregate only (source=derived_from_real_calls). Never holds a call id or text."""

    __tablename__ = "call_analytics"
    __table_args__ = (Index("ix_call_analytics_metric", "metric"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(40), default="derived_from_real_calls")
    metric: Mapped[str] = mapped_column(String(60))
    dim1: Mapped[str] = mapped_column(String(60), default="")
    dim2: Mapped[str] = mapped_column(String(60), default="")
    value: Mapped[float] = mapped_column(Float, default=0.0)
    n: Mapped[int] = mapped_column(Integer, default=0)
    loaded_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ImportedCall(Base):
    """Rows with contains_private_content stay stored but are hidden by the visible_calls view."""

    __tablename__ = "imported_calls"

    id: Mapped[int] = mapped_column(primary_key=True)
    call_id: Mapped[str] = mapped_column(String(80), unique=True)
    timestamp: Mapped[datetime | None] = mapped_column(UTCDateTime)
    caller_type: Mapped[str] = mapped_column(String(40), default="")
    intent: Mapped[str] = mapped_column(String(60), default="")
    outcome: Mapped[str] = mapped_column(String(60), default="")
    urgency: Mapped[str] = mapped_column(String(20), default="")
    red_flags: Mapped[str] = mapped_column(String(300), default="")
    promised_followup: Mapped[bool] = mapped_column(Boolean, default=False)
    contains_private_content: Mapped[bool] = mapped_column(Boolean, default=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(40), default="import")
    imported_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class PimsClient(Base):
    __tablename__ = "pims_clients"

    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str] = mapped_column(String(60), unique=True)
    name: Mapped[str] = mapped_column(String(160), default="")
    last_visit: Mapped[datetime | None] = mapped_column(UTCDateTime)
    recall_overdue: Mapped[bool] = mapped_column(Boolean, default=False)
    synced_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class PimsPatient(Base):
    __tablename__ = "pims_patients"

    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str] = mapped_column(String(60), unique=True)
    client_external_id: Mapped[str] = mapped_column(String(60), default="")
    name: Mapped[str] = mapped_column(String(120), default="")
    species: Mapped[str] = mapped_column(String(40), default="")
    breed: Mapped[str] = mapped_column(String(80), default="")
    last_exam: Mapped[datetime | None] = mapped_column(UTCDateTime)
    synced_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class PimsAppointment(Base):
    __tablename__ = "pims_appointments"

    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str] = mapped_column(String(60), unique=True)
    start: Mapped[datetime] = mapped_column(UTCDateTime)
    client_external_id: Mapped[str] = mapped_column(String(60), default="")
    patient_external_id: Mapped[str] = mapped_column(String(60), default="")
    provider: Mapped[str] = mapped_column(String(120), default="")
    visit_type: Mapped[str] = mapped_column(String(120), default="")
    status: Mapped[str] = mapped_column(String(30), default="")
    synced_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class PimsLabResult(Base):
    __tablename__ = "pims_lab_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str] = mapped_column(String(60), unique=True)
    patient_external_id: Mapped[str] = mapped_column(String(60), default="")
    test: Mapped[str] = mapped_column(String(120), default="")
    resulted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    status: Mapped[str] = mapped_column(String(30), default="")
    synced_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
