"""Situational suggestions from the approved product list. Off unless the owner turns it on.

At most one suggestion, only when a product's triggers match the situation (visit type or words in the message), only for the
pet species it suits, never in an emergency, a medical question, a complaint or a difficult moment, and not to the same person more
than once every few days. The sentence is the product's approved pitch, word for word.
"""
from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Contact, Pet, Product, UpsellLog
from app.services import settings_store, timeutil

KEY = "upsell"
NEVER = re.compile(r"\b(passed away|put down|euthan\w*|died|dying|cremat\w*|angry|upset|complain\w*|refund|ridiculous|disappointed)\b", re.I)


def get(db: Session) -> dict[str, Any]:
    cfg = {"enabled": False, "days_between": 14}
    stored = settings_store.get_raw(db, KEY, None)
    if isinstance(stored, dict):
        cfg.update({k: v for k, v in stored.items() if k in cfg})
    return cfg


def _words(text: str) -> set[str]:
    from app.assistant.replies import _tokens
    return _tokens(text)


def line(p: Product) -> str:
    pitch = p.pitch.strip()
    if p.price_cents is not None and "$" not in pitch:
        price = f"${p.price_cents / 100:.2f}".replace(".00", "")
        pitch = pitch.rstrip(".") + f" ({price}{(' ' + p.unit) if p.unit else ''})."
    return pitch


def recently_offered(db: Session, contact: Contact) -> bool:
    since = timeutil.now_utc() - timedelta(days=get(db)["days_between"])
    return db.scalar(select(UpsellLog.id).where(UpsellLog.contact_id == contact.id, UpsellLog.at > since).limit(1)) is not None


def eligible(db: Session, contact: Contact, text: str) -> list[Product]:
    if not get(db)["enabled"] or NEVER.search(text or "") or recently_offered(db, contact):
        return []
    species = {(s or "").lower() for s in db.scalars(select(Pet.species).where(Pet.contact_id == contact.id, Pet.status == "active")).all()}
    return [p for p in db.scalars(select(Product).where(Product.status == "approved").order_by(Product.id)).all()
            if p.pitch.strip() and (not p.species or not species or p.species.lower() in species)]


def pick(db: Session, contact: Contact, text: str, visit: str | None = None) -> Product | None:
    """The one product whose triggers best match the message or the visit type, if any."""
    words = _words(text) | (_words(visit) if visit else set())
    best, score = None, 0
    for p in eligible(db, contact, text):
        trig = {t.strip().lower() for t in p.triggers.split(",") if t.strip()}
        tw = set()
        for t in trig:
            tw |= _words(t)
        s = len(words & tw) + (2 if visit and visit.lower() in trig else 0)
        if s > score:
            best, score = p, s
    return best


def log(db: Session, contact: Contact, product_id: int) -> None:
    db.add(UpsellLog(contact_id=contact.id, product_id=product_id, at=timeutil.now_utc()))
