"""Booking by text (offers of open times) and the product list for situational suggestions."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UTCDateTime, utcnow


class SmsOffer(Base):
    """Times the assistant offered one person by text. The person answers 1, 2 or 3 (or names a time) within a few hours."""

    __tablename__ = "sms_offers"
    __table_args__ = (
        CheckConstraint("status in ('offered','requested','expired','declined')", name="status"),
        Index("ix_sms_offers_contact_id", "contact_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"))
    visit_type_id: Mapped[int | None] = mapped_column(ForeignKey("visit_types.id", ondelete="SET NULL"))
    slots_json: Mapped[str] = mapped_column(Text, default="[]")  # ISO UTC start times, in the order offered
    status: Mapped[str] = mapped_column(String(10), default="offered")
    event_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class Product(Base):
    """A product or service the assistant may suggest when it fits the situation. Used only once approved."""

    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint("status in ('pending','approved','retired')", name="status"),
        Index("ix_products_status", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    price_cents: Mapped[int | None] = mapped_column(Integer)
    unit: Mapped[str] = mapped_column(String(40), default="")
    pitch: Mapped[str] = mapped_column(String(300), default="")  # one client-facing sentence, used word for word
    triggers: Mapped[str] = mapped_column(String(400), default="")  # comma separated words or visit types that make it relevant
    species: Mapped[str] = mapped_column(String(40), default="")  # "dog", "cat" or empty for any
    status: Mapped[str] = mapped_column(String(10), default="pending")
    source: Mapped[str] = mapped_column(String(20), default="manual")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class WebsiteAudit(Base):
    """One crawl of the clinic website: every page found, every SEO issue, and how it compares with the run before."""

    __tablename__ = "website_audits"
    __table_args__ = (CheckConstraint("status in ('running','done','failed')", name="status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    base_url: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(8), default="running")
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    started_by: Mapped[str] = mapped_column(String(120), default="")
    pages_json: Mapped[str] = mapped_column(Text, default="[]")
    issues_json: Mapped[str] = mapped_column(Text, default="[]")
    summary_json: Mapped[str] = mapped_column(Text, default="{}")
    ai_plan: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(String(200), default="")


class UpsellLog(Base):
    """Each suggestion sent, so one person is not offered things too often."""

    __tablename__ = "upsell_log"
    __table_args__ = (Index("ix_upsell_log_contact_id", "contact_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"))
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"))
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
