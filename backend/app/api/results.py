from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api import serializers
from app.api.schemas import AdvanceBody
from app.db import get_db
from app.models import LabResult, User
from app.security.auth import require
from app.services import events, timeutil

router = APIRouter(prefix="/api/results", tags=["results"])


@router.get("")
def list_results(user: User = Depends(require("results.view")), db: Session = Depends(get_db)):
    now = timeutil.now_utc()
    rows = db.scalars(select(LabResult)).all()
    # open ones first, oldest first; ties keep entry order
    rows.sort(key=lambda r: (r.stage == 2, -timeutil.age_days(r.received_at, now), r.id))
    return {"items": [serializers.lab_result(r, now) for r in rows]}


@router.post("/{result_id}/advance")
def advance(
    result_id: int,
    body: AdvanceBody | None = None,
    user: User = Depends(require("results.advance")),
    db: Session = Depends(get_db),
):
    r = db.get(LabResult, result_id)
    if r is None:
        raise HTTPException(status_code=404, detail="No such result")
    stage = r.stage
    if stage >= 2:
        raise HTTPException(status_code=409, detail=f"Already closed by {r.told_by or 'someone'}")
    if body is not None and body.from_stage is not None and body.from_stage != stage:
        who = r.reviewed_by if stage == 1 else None
        raise HTTPException(status_code=409, detail=f"Already taken by {who}" if who else "Already moved on")
    now = timeutil.now_utc()
    vals: dict = {"stage": stage + 1, "updated_at": now}
    if stage == 0:
        vals["reviewed_by"] = user.display_name
    else:
        vals["told_by"] = user.display_name
    res = db.execute(update(LabResult).where(LabResult.id == result_id, LabResult.stage == stage).values(**vals))
    if res.rowcount == 0:
        db.refresh(r)
        raise HTTPException(status_code=409, detail=f"Already taken by {r.told_by or r.reviewed_by or 'someone'}")
    db.refresh(r)
    what = "doctor reviewed" if r.stage == 1 else "client told"
    events.record(
        db, events.RESULTS_TRACKER, "result_advanced", f"{r.pet_label}, {r.test}: {what}",
        user, "result", r.id, {"stage": r.stage},
    )
    db.commit()
    return {"item": serializers.lab_result(r, now)}
