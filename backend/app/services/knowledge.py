"""Knowledge entries: the rules for proposing, approving and declining.

Only approved entries are ever used by the assistant. A model (or an import) can only propose; a person decides.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import KnowledgeEntry, User
from app.models.clinic import KNOWLEDGE_CATEGORIES
from app.services import crm
from app.services import events as ev
from app.services import timeutil

CATEGORY_LABELS = {
    "clinic_info": "Clinic info",
    "fees": "Fees",
    "patient_knowledge": "Patient knowledge",
    "patient_communication": "Patient communication",
    "inquiry_communication": "Inquiry communication",
    "laws_regulations": "Laws and regulations",
    "specials": "Specials",
}
STATUSES = ("pending", "approved", "declined", "retired")


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def find_approved(db: Session, category: str, title: str) -> KnowledgeEntry | None:
    """The approved entry with this title (ignoring case and punctuation), if there is one."""
    key = _norm(title)
    for e in db.scalars(select(KnowledgeEntry).where(KnowledgeEntry.category == category, KnowledgeEntry.status == "approved")).all():
        if _norm(e.title) == key:
            return e
    return None


def propose(
    db: Session,
    *,
    category: str,
    title: str,
    body: str,
    proposed_by: str,
    user: User | None = None,
    source_kind: str = "manual",
    source_ref: str = "",
    source_quote: str = "",
    confidence: int | None = None,
    supersedes_id: int | None = None,
    is_demo: bool = False,
    now: datetime | None = None,
) -> KnowledgeEntry:
    """Create a pending entry. If an approved entry has the same title, the new one replaces it when approved.

    A proposal whose text differs from the approved entry is flagged as conflicting. An identical text, or the same
    pending text already waiting, is refused so the queue does not fill with repeats.
    """
    if category not in KNOWLEDGE_CATEGORIES:
        raise crm.CrmError(400, "Unknown knowledge category.")
    title, body = title.strip(), body.strip()
    if not title or not body:
        raise crm.CrmError(400, "A title and some text are both needed.")
    old: KnowledgeEntry | None = db.get(KnowledgeEntry, supersedes_id) if supersedes_id else find_approved(db, category, title)
    if supersedes_id and (old is None or old.status != "approved"):
        raise crm.CrmError(400, "Only an approved entry can be replaced.")
    if old is not None and _norm(old.body) == _norm(body):
        raise crm.CrmError(409, "That is already in the approved knowledge.")
    for p in db.scalars(select(KnowledgeEntry).where(KnowledgeEntry.status == "pending", KnowledgeEntry.category == category)).all():
        if _norm(p.title) == _norm(title) and _norm(p.body) == _norm(body):
            raise crm.CrmError(409, "That is already waiting in Pending knowledge.")
    e = KnowledgeEntry(
        category=category, title=title[:200], body=body, status="pending", proposed_by=proposed_by, source_kind=source_kind,
        source_ref=source_ref[:80], source_quote=source_quote[:300], confidence=confidence,
        supersedes_id=old.id if old else None, previous_body=old.body if old else "", conflicts=old is not None,
        proposed_user_id=user.id if user else None, proposed_at=now or timeutil.now_utc(), is_demo=is_demo,
    )
    db.add(e)
    db.flush()
    who = user.display_name if user else "The assistant"
    ev.record(db, ev.KNOWLEDGE, "knowledge_proposed", f"{who} suggested knowledge: {e.title}", user or "AI assistant", "knowledge", e.id,
              {"category": category, "by": proposed_by})
    return e


def approve(db: Session, e: KnowledgeEntry, user: User, *, now: datetime | None = None) -> None:
    if e.status != "pending":
        raise crm.CrmError(409, "That entry was already decided.")
    now = now or timeutil.now_utc()
    if e.supersedes_id:
        old = db.get(KnowledgeEntry, e.supersedes_id)
        if old is not None and old.status == "approved":
            old.status = "retired"
            old.decided_by, old.decided_at = user.id, now
            e.version = old.version + 1
    e.status = "approved"
    e.decided_by, e.decided_at = user.id, now
    ev.record(db, ev.KNOWLEDGE, "knowledge_approved", f"{user.display_name} approved: {e.title}", user, "knowledge", e.id, {"category": e.category})


def decline(db: Session, e: KnowledgeEntry, user: User, reason: str = "", *, now: datetime | None = None) -> None:
    if e.status != "pending":
        raise crm.CrmError(409, "That entry was already decided.")
    e.status = "declined"
    e.decided_by, e.decided_at = user.id, now or timeutil.now_utc()
    e.decline_reason = reason[:200]
    ev.record(db, ev.KNOWLEDGE, "knowledge_declined", f"{user.display_name} declined: {e.title}", user, "knowledge", e.id, {"category": e.category})


def retire(db: Session, e: KnowledgeEntry, user: User) -> None:
    if e.status != "approved":
        raise crm.CrmError(409, "Only an approved entry can be retired.")
    e.status = "retired"
    e.decided_by, e.decided_at = user.id, timeutil.now_utc()
    ev.record(db, ev.KNOWLEDGE, "knowledge_retired", f"{user.display_name} retired: {e.title}", user, "knowledge", e.id, {"category": e.category})


def names(db: Session, ids: set[int | None]) -> dict[int, str]:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    return {u.id: u.display_name for u in db.scalars(select(User).where(User.id.in_(ids))).all()}


def entry_dicts(db: Session, rows: list[KnowledgeEntry]) -> list[dict[str, Any]]:
    who = names(db, {r.proposed_user_id for r in rows} | {r.decided_by for r in rows})
    out = []
    for e in rows:
        out.append({
            "id": e.id, "category": e.category, "category_label": CATEGORY_LABELS.get(e.category, e.category),
            "title": e.title, "body": e.body, "version": e.version, "status": e.status, "proposed_by": e.proposed_by,
            "source_kind": e.source_kind, "source_ref": e.source_ref, "source_quote": e.source_quote, "confidence": e.confidence,
            "supersedes_id": e.supersedes_id, "previous_body": e.previous_body, "conflicts": e.conflicts,
            "proposer": who.get(e.proposed_user_id), "decider": who.get(e.decided_by),
            "proposed_at": timeutil.iso(e.proposed_at), "decided_at": timeutil.iso(e.decided_at), "decline_reason": e.decline_reason,
        })
    return out


def status_counts(db: Session) -> dict[str, Any]:
    by_status = dict(db.execute(select(KnowledgeEntry.status, func.count()).group_by(KnowledgeEntry.status)).all())
    pending_cat = dict(db.execute(select(KnowledgeEntry.category, func.count()).where(KnowledgeEntry.status == "pending").group_by(KnowledgeEntry.category)).all())
    approved_cat = dict(db.execute(select(KnowledgeEntry.category, func.count()).where(KnowledgeEntry.status == "approved").group_by(KnowledgeEntry.category)).all())
    conflicts = db.scalar(select(func.count()).select_from(KnowledgeEntry).where(KnowledgeEntry.status == "pending", KnowledgeEntry.conflicts.is_(True))) or 0
    return {
        "pending": by_status.get("pending", 0), "approved": by_status.get("approved", 0), "declined": by_status.get("declined", 0),
        "retired": by_status.get("retired", 0), "conflicts": conflicts, "pending_by_category": pending_cat, "approved_by_category": approved_cat,
    }
