"""Automations: "when this happens (or at this time), do that", set up by the people who use the app every day."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UTCDateTime, utcnow


class Automation(Base):
    __tablename__ = "automations"
    __table_args__ = (CheckConstraint("trigger_type in ('schedule','event')", name="trigger_type"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    trigger_type: Mapped[str] = mapped_column(String(10), default="schedule")
    # schedule: {"days": ["mon",...], "time": "07:45"}; event: {"event": "missed_call"}
    trigger_json: Mapped[str] = mapped_column(Text, default="{}")
    action: Mapped[str] = mapped_column(String(20))
    # e.g. {"report": "morning", "channel": "sms", "to": "+1604..."} or {"to_user": 3, "text": "..."}
    action_json: Mapped[str] = mapped_column(Text, default="{}")
    template: Mapped[str] = mapped_column(String(40), default="")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_ok: Mapped[bool | None] = mapped_column(Boolean)
    last_result: Mapped[str] = mapped_column(String(300), default="")
    run_count: Mapped[int] = mapped_column(Integer, default=0)


class AutomationRun(Base):
    __tablename__ = "automation_runs"
    __table_args__ = (Index("ix_automation_runs_automation_id", "automation_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    automation_id: Mapped[int] = mapped_column(ForeignKey("automations.id", ondelete="CASCADE"))
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    detail: Mapped[str] = mapped_column(String(500), default="")
