"""After-hours setup: toggles, bookable visit types, emergency words, hospitals, wording."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.aftercare import emergency as em
from app.aftercare import keywords as kw
from app.aftercare import service as svc
from app.api.schemas import Strict
from app.db import get_db
from app.models import Hospital, User, VisitType
from app.security.auth import require
from app.services import events as ev
from app.services import settings_store, timeutil

router = APIRouter(prefix="/api", tags=["afterhours"])

HOURS_NOTE_DEFAULT = "Example hours: closed after 6 pm on weekdays, and after 2 pm on Saturdays."
TOGGLE_LABELS = {"req": "appointment requests", "msg": "refill and message requests", "missed": "texts to missed callers"}


def visit_type_dict(v: VisitType) -> dict[str, Any]:
    return {"id": v.id, "name": v.name, "minutes": v.minutes, "bookable": v.bookable}


def settings_payload(db: Session) -> dict[str, Any]:
    s = settings_store.get_all(db)
    info = kw.load_keywords_info(db)
    return {
        "toggles": {"req": bool(s["tog_req"]), "msg": bool(s["tog_msg"]), "missed": bool(s["tog_missed"])},
        "hours_note": settings_store.get(db, "hours_note", HOURS_NOTE_DEFAULT),
        "visit_types": [visit_type_dict(v) for v in db.scalars(select(VisitType).order_by(VisitType.sort, VisitType.id))],
        "keywords": info["keywords"],
        "keywords_status": info["status"],
        "hospitals": [svc.hospital_dict(h) for h in db.scalars(select(Hospital).order_by(Hospital.sort, Hospital.id))],
        "wording": svc.wording(db),
    }


@router.get("/afterhours/settings")
def get_settings(user: User = Depends(require("afterhours.view")), db: Session = Depends(get_db)):
    return settings_payload(db)


class TogglesBody(Strict):
    req: bool | None = None
    msg: bool | None = None
    missed: bool | None = None


@router.put("/afterhours/toggles")
def put_toggles(body: TogglesBody, user: User = Depends(require("afterhours.edit")), db: Session = Depends(get_db)):
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    if not changes:
        raise HTTPException(status_code=400, detail="Nothing to change")
    for k, v in changes.items():
        settings_store.put(db, f"tog_{k}", v, by=user.display_name)
        ev.record(db, ev.AFTER_HOURS, "toggle_changed",
                  f"{user.display_name} turned {'on' if v else 'off'} {TOGGLE_LABELS[k]}", user, "setting", f"tog_{k}", {"value": v})
    db.commit()
    return {"toggles": settings_payload(db)["toggles"]}


class VisitTypeBody(Strict):
    bookable: bool


@router.put("/visit-types/{vid}")
def put_visit_type(vid: int, body: VisitTypeBody, user: User = Depends(require("afterhours.edit")), db: Session = Depends(get_db)):
    v = db.get(VisitType, vid)
    if v is None:
        raise HTTPException(status_code=404, detail="No such visit type")
    v.bookable = body.bookable
    ev.record(db, ev.AFTER_HOURS, "visit_type_changed",
              f"{user.display_name} set {v.name} to {'Assistant can book' if v.bookable else 'Front desk only'}",
              user, "visit_type", v.id, {"bookable": v.bookable})
    db.commit()
    return {"item": visit_type_dict(v)}


class KeywordsBody(Strict):
    keywords: list[str] = Field(max_length=400)
    status: Literal["placeholder", "draft", "approved"]


@router.put("/afterhours/keywords")
def put_keywords(body: KeywordsBody, user: User = Depends(require("afterhours.edit")), db: Session = Depends(get_db)):
    words: list[str] = []
    for k in body.keywords:
        k = k.strip()
        if not k:
            continue
        if len(k) > 400:
            raise HTTPException(status_code=400, detail="One of the words is too long")
        err = em.check_regex(k)
        if err:
            raise HTTPException(status_code=400, detail=err)
        if not k.startswith("re:") and not em.normalize(k):
            raise HTTPException(status_code=400, detail=f"'{k[:30]}' has no letters or numbers")
        if k not in words:
            words.append(k)
    if not words:
        raise HTTPException(status_code=400, detail="The emergency list cannot be empty")
    settings_store.put_raw(db, kw.KEY, {"keywords": words, "status": body.status}, by=user.display_name)
    ev.record(db, ev.AFTER_HOURS, "keywords_changed",
              f"{user.display_name} saved the emergency word list ({len(words)} entries, {body.status})", user, "setting", kw.KEY)
    db.commit()
    info = kw.load_keywords_info(db)
    return {"keywords": info["keywords"], "keywords_status": info["status"]}


class WordingBody(Strict):
    opening_line: str | None = Field(default=None, min_length=1, max_length=600)
    urgent_line: str | None = Field(default=None, min_length=1, max_length=600)
    never_line: str | None = Field(default=None, min_length=1, max_length=600)


@router.put("/afterhours/wording")
def put_wording(body: WordingBody, user: User = Depends(require("afterhours.edit")), db: Session = Depends(get_db)):
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    if not changes:
        raise HTTPException(status_code=400, detail="Nothing to change")
    if "opening_line" in changes and "automated assistant" not in changes["opening_line"].lower():
        raise HTTPException(status_code=400, detail="The opening line must say it is an automated assistant")
    w = svc.wording(db)
    w.update(changes)
    settings_store.put_raw(db, "wording", w, by=user.display_name)
    ev.record(db, ev.AFTER_HOURS, "wording_changed", f"{user.display_name} changed the assistant wording", user, "setting", "wording")
    db.commit()
    return {"wording": w}


class HospitalBody(Strict):
    name: str = Field(min_length=1, max_length=160)
    place: str = Field(default="", max_length=120)
    phone: str = Field(default="", max_length=40)
    address: str = Field(default="", max_length=300)
    hours_claim: str = Field(default="", max_length=300)


@router.post("/hospitals", status_code=201)
def add_hospital(body: HospitalBody, user: User = Depends(require("afterhours.edit")), db: Session = Depends(get_db)):
    last = db.scalars(select(Hospital).order_by(Hospital.sort.desc())).first()
    h = Hospital(**body.model_dump(), confirmed=False, is_placeholder=False, sort=(last.sort + 1) if last else 0)
    db.add(h)
    db.flush()
    ev.record(db, ev.AFTER_HOURS, "hospital_added", f"{user.display_name} added the emergency hospital {h.name}", user, "hospital", h.id)
    db.commit()
    return {"item": svc.hospital_dict(h)}


def _hospital(db: Session, hid: int) -> Hospital:
    h = db.get(Hospital, hid)
    if h is None:
        raise HTTPException(status_code=404, detail="No such hospital")
    return h


@router.put("/hospitals/{hid}")
def put_hospital(hid: int, body: HospitalBody, user: User = Depends(require("afterhours.edit")), db: Session = Depends(get_db)):
    h = _hospital(db, hid)
    for k, v in body.model_dump().items():
        setattr(h, k, v.strip())
    h.confirmed = False
    h.confirmed_by = None
    if h.name != "Backup hospital":
        h.is_placeholder = False
    ev.record(db, ev.AFTER_HOURS, "hospital_edited", f"{user.display_name} edited the emergency hospital {h.name}, now needs confirming",
              user, "hospital", h.id)
    db.commit()
    return {"item": svc.hospital_dict(h)}


@router.post("/hospitals/{hid}/confirm")
def confirm_hospital(hid: int, user: User = Depends(require("afterhours.edit")), db: Session = Depends(get_db)):
    h = _hospital(db, hid)
    if h.is_placeholder or not h.name.strip():
        raise HTTPException(status_code=400, detail="Add the hospital details before marking it confirmed")
    h.confirmed = True
    h.confirmed_by = user.display_name
    ev.record(db, ev.AFTER_HOURS, "hospital_confirmed", f"{user.display_name} confirmed the emergency hospital list",
              user, "hospital", h.id, {"hospital": h.name})
    db.commit()
    return {"item": svc.hospital_dict(h)}


@router.post("/hospitals/{hid}/unconfirm")
def unconfirm_hospital(hid: int, user: User = Depends(require("afterhours.edit")), db: Session = Depends(get_db)):
    h = _hospital(db, hid)
    h.confirmed = False
    h.confirmed_by = None
    ev.record(db, ev.AFTER_HOURS, "hospital_unconfirmed", f"{user.display_name} removed the confirmation for {h.name}",
              user, "hospital", h.id)
    db.commit()
    return {"item": svc.hospital_dict(h)}
