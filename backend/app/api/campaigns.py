"""Campaigns: segment, preview, approve, and a practice-mode run. Nothing is ever really sent."""
from __future__ import annotations

import json
from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import Strict
from app.db import get_db
from app.models import Campaign, CampaignRecipient, Contact, User
from app.security.auth import require
from app.services import campaigns as cp
from app.services import events as ev
from app.services import knowledge as kn
from app.services import timeutil

router = APIRouter(prefix="/api", tags=["campaigns"])

Channel = Literal["sms", "email"]
Purpose = Literal["care", "promo"]


class Segment(Strict):
    q: str | None = Field(default=None, max_length=80)
    kind: Literal["inquiry", "patient"] | None = None
    owner: str | None = Field(default=None, max_length=20)
    email_consent: Literal["full", "transactional", "none", "unsubscribed"] | None = None
    sms_consent: Literal["full", "transactional", "none", "unsubscribed"] | None = None
    species: str | None = Field(default=None, max_length=40)
    visits_min: int | None = Field(default=None, ge=0, le=10000)
    visits_max: int | None = Field(default=None, ge=0, le=10000)
    last_visit_from: date | None = None
    last_visit_to: date | None = None
    has_open_task: bool | None = None
    ai: Literal["on", "off"] | None = None
    source: Literal["phone", "walk_in", "website", "referral", "after_hours", "import", "other"] | None = None


class CampaignIn(Strict):
    name: str = Field(min_length=2, max_length=120)
    channel: Channel
    purpose: Purpose
    segment: Segment = Field(default_factory=Segment)
    subject: str = Field(default="", max_length=150)
    body: str = Field(min_length=2, max_length=1600)


class CampaignPatch(Strict):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    channel: Channel | None = None
    purpose: Purpose | None = None
    segment: Segment | None = None
    subject: str | None = Field(default=None, max_length=150)
    body: str | None = Field(default=None, min_length=2, max_length=1600)


STATUS_LABELS = {"draft": "Draft", "awaiting_approval": "Waiting for approval", "approved": "Approved", "done_dry_run": "Done (practice mode)", "cancelled": "Cancelled"}


def _seg(c: Campaign) -> dict[str, Any]:
    try:
        return json.loads(c.segment_json or "{}")
    except ValueError:
        return {}


def camp_dict(db: Session, c: Campaign, who: dict[int, str]) -> dict[str, Any]:
    try:
        counts = json.loads(c.counts_json or "{}")
    except ValueError:
        counts = {}
    return {
        "id": c.id, "name": c.name, "channel": c.channel, "purpose": c.purpose, "status": c.status, "status_label": STATUS_LABELS.get(c.status, c.status),
        "segment": _seg(c), "subject": c.subject, "body": c.body, "created_by": who.get(c.created_by), "approved_by": who.get(c.approved_by),
        "created_at": timeutil.iso(c.created_at), "approved_at": timeutil.iso(c.approved_at), "ran_at": timeutil.iso(c.ran_at), "counts": counts,
    }


def _get(db: Session, camp_id: int) -> Campaign:
    c = db.get(Campaign, camp_id)
    if c is None:
        raise HTTPException(status_code=404, detail="No such campaign")
    return c


def _seg_json(seg: Segment) -> str:
    return json.dumps(cp.clean_segment(seg.model_dump(mode="json", exclude_none=True)), sort_keys=True)


@router.get("/campaigns")
def list_campaigns(user: User = Depends(require("campaigns.view")), db: Session = Depends(get_db)):
    rows = db.scalars(select(Campaign).order_by(Campaign.created_at.desc(), Campaign.id.desc()).limit(200)).all()
    who = kn.names(db, {r.created_by for r in rows} | {r.approved_by for r in rows})
    return {"items": [camp_dict(db, c, who) for c in rows], "quiet_hours": cp.in_quiet_hours(), "merge_fields": list(cp.MERGE_FIELDS)}


class PreviewIn(Strict):
    channel: Channel
    purpose: Purpose
    segment: Segment = Field(default_factory=Segment)
    subject: str = Field(default="", max_length=150)
    body: str = Field(default="", max_length=1600)


@router.post("/campaigns/preview")
def preview(body: PreviewIn, user: User = Depends(require("campaigns.edit")), db: Session = Depends(get_db)):
    cp.check_template(body.body, body.subject)
    seg = cp.clean_segment(body.segment.model_dump(mode="json", exclude_none=True))
    evaluated, _ = cp.evaluate(db, user, body.channel, body.purpose, seg)
    out = cp.tally(evaluated)
    out["samples"] = cp.samples(db, evaluated, body.body or "Hi {first_name}", body.subject, body.channel, cp.clinic_name(db)) if body.body else []
    out["quiet_hours"] = cp.in_quiet_hours()
    return out


@router.post("/campaigns", status_code=201)
def create_campaign(body: CampaignIn, user: User = Depends(require("campaigns.edit")), db: Session = Depends(get_db)):
    cp.check_template(body.body, body.subject)
    if body.channel == "email" and not body.subject.strip():
        raise HTTPException(status_code=400, detail="An email needs a subject line.")
    c = Campaign(name=body.name, channel=body.channel, purpose=body.purpose, segment_json=_seg_json(body.segment), subject=body.subject if body.channel == "email" else "",
                 body=body.body, status="draft", created_by=user.id)
    db.add(c)
    db.flush()
    ev.record(db, ev.CAMPAIGNS, "campaign_created", f"{user.display_name} started a campaign: {c.name}", user, "campaign", c.id)
    db.commit()
    return {"item": camp_dict(db, c, {user.id: user.display_name})}


@router.get("/campaigns/{camp_id}")
def get_campaign(camp_id: int, user: User = Depends(require("campaigns.view")), db: Session = Depends(get_db)):
    c = _get(db, camp_id)
    who = kn.names(db, {c.created_by, c.approved_by})
    item = camp_dict(db, c, who)
    rec = db.execute(select(CampaignRecipient, Contact).join(Contact, Contact.id == CampaignRecipient.contact_id)
                     .where(CampaignRecipient.campaign_id == c.id).order_by(Contact.display_name).limit(300)).all()
    item["recipients"] = [{"contact": {"id": ct.id, "display_name": ct.display_name}, "state": r.state,
                           "skipped_label": cp.SKIP_LABELS.get(r.skipped_reason, "") if r.skipped_reason else ""} for r, ct in rec]
    return {"item": item}


@router.patch("/campaigns/{camp_id}")
def patch_campaign(camp_id: int, body: CampaignPatch, user: User = Depends(require("campaigns.edit")), db: Session = Depends(get_db)):
    c = _get(db, camp_id)
    if c.status not in ("draft", "awaiting_approval"):
        raise HTTPException(status_code=409, detail="Only a draft can be changed.")
    ch = body.model_dump(exclude_unset=True)
    nb, ns = ch.get("body", c.body), ch.get("subject", c.subject)
    cp.check_template(nb or "", ns or "")
    if "segment" in ch and body.segment is not None:
        c.segment_json = _seg_json(body.segment)
    for key in ("name", "channel", "purpose", "subject", "body"):
        if key in ch and ch[key] is not None:
            setattr(c, key, ch[key])
    if c.channel == "sms":
        c.subject = ""
    elif not c.subject.strip():
        raise HTTPException(status_code=400, detail="An email needs a subject line.")
    c.status = "draft"  # any change sends it back for a fresh look
    ev.record(db, ev.CAMPAIGNS, "campaign_edited", f"{user.display_name} edited the campaign {c.name}", user, "campaign", c.id)
    db.commit()
    return {"item": camp_dict(db, c, kn.names(db, {c.created_by, c.approved_by}))}


def _move(db: Session, camp_id: int, user: User, allowed: tuple[str, ...], to: str, action: str, text: str) -> dict[str, Any]:
    c = _get(db, camp_id)
    if c.status not in allowed:
        raise HTTPException(status_code=409, detail="That campaign is not in a state where this can be done.")
    c.status = to
    if to == "approved":
        c.approved_by, c.approved_at = user.id, timeutil.now_utc()
    ev.record(db, ev.CAMPAIGNS, action, f"{user.display_name} {text}: {c.name}", user, "campaign", c.id)
    db.commit()
    return {"item": camp_dict(db, c, kn.names(db, {c.created_by, c.approved_by}))}


@router.post("/campaigns/{camp_id}/submit")
def submit(camp_id: int, user: User = Depends(require("campaigns.edit")), db: Session = Depends(get_db)):
    return _move(db, camp_id, user, ("draft",), "awaiting_approval", "campaign_submitted", "sent for approval")


@router.post("/campaigns/{camp_id}/approve")
def approve(camp_id: int, user: User = Depends(require("campaigns.approve")), db: Session = Depends(get_db)):
    return _move(db, camp_id, user, ("awaiting_approval",), "approved", "campaign_approved", "approved the campaign")


@router.post("/campaigns/{camp_id}/cancel")
def cancel(camp_id: int, user: User = Depends(require("campaigns.edit")), db: Session = Depends(get_db)):
    return _move(db, camp_id, user, ("draft", "awaiting_approval", "approved"), "cancelled", "campaign_cancelled", "cancelled the campaign")


@router.post("/campaigns/{camp_id}/run")
def run(camp_id: int, user: User = Depends(require("campaigns.approve")), db: Session = Depends(get_db)):
    c = _get(db, camp_id)
    counts = cp.run_dry(db, c, user)
    db.commit()
    return {"item": camp_dict(db, c, kn.names(db, {c.created_by, c.approved_by})), "counts": counts}
