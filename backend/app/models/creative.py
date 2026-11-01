"""Creative studio: images and short videos for the clinic's campaigns, made through a short interview."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UTCDateTime, utcnow


class Creative(Base):
    __tablename__ = "creatives"
    __table_args__ = (
        CheckConstraint("kind in ('image','video')", name="kind"),
        CheckConstraint("status in ('interview','review','generating','done','failed')", name="status"),
        Index("ix_creatives_status", "status"),
        Index("uq_creatives_public_token", "public_token", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(6))
    format: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(160), default="")
    status: Mapped[str] = mapped_column(String(10), default="interview")
    # the whole interview as a real conversation (system + turns), so context builds up like a person taking notes
    messages_json: Mapped[str] = mapped_column(Text, default="[]")
    qa_json: Mapped[str] = mapped_column(Text, default="[]")
    question_json: Mapped[str] = mapped_column(Text, default="{}")  # the question waiting for an answer
    plan_json: Mapped[str] = mapped_column(Text, default="{}")      # hook, arc, checks: the writer's own working note
    prompt: Mapped[str] = mapped_column(Text, default="")
    caption: Mapped[str] = mapped_column(Text, default="")
    sms_text: Mapped[str] = mapped_column(String(320), default="")
    options_json: Mapped[str] = mapped_column(Text, default="{}")   # aspect, quality, resolution, duration
    reference_url: Mapped[str] = mapped_column(String(600), default="")
    task_url: Mapped[str] = mapped_column(String(600), default="")
    output_url: Mapped[str] = mapped_column(String(800), default="")
    file_name: Mapped[str] = mapped_column(String(120), default="")
    public_token: Mapped[str | None] = mapped_column(String(40))
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)      # generation (estimated from the provider's price)
    llm_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)  # interview and writing (what OpenRouter reported)
    error: Mapped[str] = mapped_column(String(300), default="")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    generated_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    done_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
