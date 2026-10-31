"""Knowledge & Fees: the approved knowledge the assistant may use, the Pending queue, and the fee list."""
from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.schemas import Strict
from app.db import get_db
from app.models import Fee, KnowledgeEntry, User
from app.security.auth import require
from app.services import crm
from app.services import events as ev
from app.services import knowledge as kn
from app.services import timeutil

router = APIRouter(prefix="/api", tags=["knowledge"])

Category = Literal["clinic_info", "fees", "patient_knowledge", "patient_communication", "inquiry_communication", "laws_regulations", "specials"]
Status = Literal["pending", "approved", "declined", "retired", "all"]


class EntryIn(Strict):
    category: Category
    title: str = Field(min_length=2, max_length=200)
    body: str = Field(min_length=2, max_length=4000)
    replaces_id: int | None = None  # an approved entry this suggestion would replace


class EntryPatch(Strict):
    category: Category | None = None
    title: str | None = Field(default=None, min_length=2, max_length=200)
    body: str | None = Field(default=None, min_length=2, max_length=4000)


class DeclineIn(Strict):
    reason: str = Field(default="", max_length=200)


class BulkIn(Strict):
    ids: list[int] = Field(min_length=1, max_length=200)
    action: Literal["approve", "decline"]
    conflicts_only: bool = False


def _get(db: Session, entry_id: int) -> KnowledgeEntry:
    e = db.get(KnowledgeEntry, entry_id)
    if e is None:
        raise HTTPException(status_code=404, detail="No such knowledge entry")
    return e


@router.get("/knowledge")
def list_knowledge(
    status: Status = "approved",
    category: Category | None = None,
    q: str | None = Query(default=None, max_length=80),
    conflicts: bool = False,
    proposed_by: Literal["human", "ai", "import"] | None = None,
    user: User = Depends(require("knowledge.view")),
    db: Session = Depends(get_db),
):
    stmt = select(KnowledgeEntry)
    if status != "all":
        stmt = stmt.where(KnowledgeEntry.status == status)
    if category:
        stmt = stmt.where(KnowledgeEntry.category == category)
    if conflicts:
        stmt = stmt.where(KnowledgeEntry.conflicts.is_(True))
    if proposed_by:
        stmt = stmt.where(KnowledgeEntry.proposed_by == proposed_by)
    if q and q.strip():
        like = crm.like_pattern(q.strip())
        stmt = stmt.where(or_(KnowledgeEntry.title.like(like, escape="\\"), KnowledgeEntry.body.like(like, escape="\\")))
    order = (KnowledgeEntry.proposed_at.desc(), KnowledgeEntry.id.desc()) if status == "pending" else (KnowledgeEntry.category, KnowledgeEntry.title)
    rows = db.scalars(stmt.order_by(*order).limit(500)).all()
    return {"items": kn.entry_dicts(db, list(rows)), "counts": kn.status_counts(db), "categories": [[k, v] for k, v in kn.CATEGORY_LABELS.items()]}


@router.post("/knowledge", status_code=201)
def suggest_knowledge(body: EntryIn, user: User = Depends(require("knowledge.suggest")), db: Session = Depends(get_db)):
    e = kn.propose(db, category=body.category, title=body.title, body=body.body, proposed_by="human", user=user,
                   supersedes_id=body.replaces_id)
    db.commit()
    return {"item": kn.entry_dicts(db, [e])[0]}


@router.get("/knowledge/{entry_id}")
def get_knowledge(entry_id: int, user: User = Depends(require("knowledge.view")), db: Session = Depends(get_db)):
    return {"item": kn.entry_dicts(db, [_get(db, entry_id)])[0]}


@router.patch("/knowledge/{entry_id}")
def edit_pending(entry_id: int, body: EntryPatch, user: User = Depends(require("knowledge.suggest")), db: Session = Depends(get_db)):
    e = _get(db, entry_id)
    if e.status != "pending":
        raise HTTPException(status_code=409, detail="Only a pending entry can be edited. Suggest a replacement for an approved one.")
    ch = body.model_dump(exclude_unset=True)
    for key in ("category", "title", "body"):
        if key in ch and ch[key] is not None:
            setattr(e, key, ch[key])
    ev.record(db, ev.KNOWLEDGE, "knowledge_edited", f"{user.display_name} edited a pending entry: {e.title}", user, "knowledge", e.id)
    db.commit()
    return {"item": kn.entry_dicts(db, [e])[0]}


@router.post("/knowledge/bulk")
def bulk_decide(body: BulkIn, user: User = Depends(require("knowledge.decide")), db: Session = Depends(get_db)):
    rows = db.scalars(select(KnowledgeEntry).where(KnowledgeEntry.id.in_(set(body.ids)), KnowledgeEntry.status == "pending")).all()
    done = 0
    for e in rows:
        if body.conflicts_only and not e.conflicts:
            continue
        if body.action == "approve":
            kn.approve(db, e, user)
        else:
            kn.decline(db, e, user)
        done += 1
    db.commit()
    return {"done": done, "skipped": len(set(body.ids)) - done}


@router.post("/knowledge/{entry_id}/approve")
def approve_entry(entry_id: int, user: User = Depends(require("knowledge.decide")), db: Session = Depends(get_db)):
    e = _get(db, entry_id)
    kn.approve(db, e, user)
    db.commit()
    return {"item": kn.entry_dicts(db, [e])[0]}


@router.post("/knowledge/{entry_id}/decline")
def decline_entry(entry_id: int, body: DeclineIn, user: User = Depends(require("knowledge.decide")), db: Session = Depends(get_db)):
    e = _get(db, entry_id)
    kn.decline(db, e, user, body.reason)
    db.commit()
    return {"item": kn.entry_dicts(db, [e])[0]}


@router.post("/knowledge/{entry_id}/retire")
def retire_entry(entry_id: int, user: User = Depends(require("knowledge.decide")), db: Session = Depends(get_db)):
    e = _get(db, entry_id)
    kn.retire(db, e, user)
    db.commit()
    return {"item": kn.entry_dicts(db, [e])[0]}


# ---------------------------------------------------------------- fees
class FeeIn(Strict):
    service: str = Field(min_length=2, max_length=120)
    price_dollars: float | None = Field(default=None, ge=0, le=100000)
    price_prefix: Literal["", "from", "about"] = ""
    unit: str = Field(default="", max_length=40)
    note: str = Field(default="", max_length=300)
    effective_from: date | None = None
    effective_to: date | None = None


class FeePatch(Strict):
    service: str | None = Field(default=None, min_length=2, max_length=120)
    price_dollars: float | None = Field(default=None, ge=0, le=100000)
    price_prefix: Literal["", "from", "about"] | None = None
    unit: str | None = Field(default=None, max_length=40)
    note: str | None = Field(default=None, max_length=300)
    effective_from: date | None = None
    effective_to: date | None = None


def fee_dict(f: Fee, who: dict[int, str]) -> dict:
    return {
        "id": f.id, "service": f.service, "price_cents": f.price_cents, "price_dollars": None if f.price_cents is None else f.price_cents / 100,
        "price_prefix": f.price_prefix, "unit": f.unit, "note": f.note, "status": f.status, "proposed_by": f.proposed_by,
        "effective_from": f.effective_from.isoformat() if f.effective_from else None,
        "effective_to": f.effective_to.isoformat() if f.effective_to else None,
        "decider": who.get(f.decided_by), "decided_at": timeutil.iso(f.decided_at),
    }


def _cents(dollars: float | None) -> int | None:
    return None if dollars is None else int(round(dollars * 100))


@router.get("/fees")
def list_fees(status: Literal["pending", "approved", "retired", "all"] = "approved", user: User = Depends(require("knowledge.view")), db: Session = Depends(get_db)):
    stmt = select(Fee)
    if status != "all":
        stmt = stmt.where(Fee.status == status)
    rows = db.scalars(stmt.order_by(Fee.service, Fee.id)).all()
    who = kn.names(db, {r.decided_by for r in rows})
    pending = len(db.scalars(select(Fee.id).where(Fee.status == "pending")).all())
    return {"items": [fee_dict(f, who) for f in rows], "pending": pending}


@router.post("/fees", status_code=201)
def add_fee(body: FeeIn, user: User = Depends(require("knowledge.suggest")), db: Session = Depends(get_db)):
    if body.effective_from and body.effective_to and body.effective_to < body.effective_from:
        raise HTTPException(status_code=400, detail="The end date must be after the start date.")
    f = Fee(service=body.service, price_cents=_cents(body.price_dollars), price_prefix=body.price_prefix, unit=body.unit, note=body.note,
            effective_from=body.effective_from, effective_to=body.effective_to, status="pending", proposed_by="human")
    db.add(f)
    db.flush()
    ev.record(db, ev.KNOWLEDGE, "fee_proposed", f"{user.display_name} suggested a fee: {f.service}", user, "fee", f.id)
    db.commit()
    return {"item": fee_dict(f, {})}


@router.patch("/fees/{fee_id}")
def edit_fee(fee_id: int, body: FeePatch, user: User = Depends(require("knowledge.suggest")), db: Session = Depends(get_db)):
    f = db.get(Fee, fee_id)
    if f is None:
        raise HTTPException(status_code=404, detail="No such fee")
    if f.status != "pending":
        raise HTTPException(status_code=409, detail="Only a pending fee can be edited. Add a new fee to replace an approved one.")
    ch = body.model_dump(exclude_unset=True)
    if "price_dollars" in ch:
        f.price_cents = _cents(ch.pop("price_dollars"))
    for key, val in ch.items():
        if val is None and key in ("service",):
            continue
        setattr(f, key, val)
    ev.record(db, ev.KNOWLEDGE, "fee_edited", f"{user.display_name} edited a pending fee: {f.service}", user, "fee", f.id)
    db.commit()
    return {"item": fee_dict(f, {})}


@router.post("/fees/{fee_id}/approve")
def approve_fee(fee_id: int, user: User = Depends(require("knowledge.decide")), db: Session = Depends(get_db)):
    f = db.get(Fee, fee_id)
    if f is None:
        raise HTTPException(status_code=404, detail="No such fee")
    if f.status != "pending":
        raise HTTPException(status_code=409, detail="That fee was already decided.")
    today = timeutil.today_local()
    for old in db.scalars(select(Fee).where(Fee.status == "approved", Fee.service == f.service, Fee.id != f.id)).all():
        old.status = "retired"
        old.effective_to = old.effective_to or today
        old.decided_by, old.decided_at = user.id, timeutil.now_utc()
    f.status = "approved"
    f.decided_by, f.decided_at = user.id, timeutil.now_utc()
    f.effective_from = f.effective_from or today
    ev.record(db, ev.KNOWLEDGE, "fee_approved", f"{user.display_name} approved the fee: {f.service}", user, "fee", f.id)
    db.commit()
    return {"item": fee_dict(f, {})}


@router.post("/fees/{fee_id}/retire")
def retire_fee(fee_id: int, user: User = Depends(require("knowledge.decide")), db: Session = Depends(get_db)):
    f = db.get(Fee, fee_id)
    if f is None:
        raise HTTPException(status_code=404, detail="No such fee")
    if f.status == "retired":
        raise HTTPException(status_code=409, detail="That fee is already retired.")
    f.status = "retired"
    f.effective_to = f.effective_to or timeutil.today_local()
    f.decided_by, f.decided_at = user.id, timeutil.now_utc()
    ev.record(db, ev.KNOWLEDGE, "fee_retired", f"{user.display_name} retired the fee: {f.service}", user, "fee", f.id)
    db.commit()
    return {"item": fee_dict(f, {})}
