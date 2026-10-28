from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import serializers
from app.db import get_db
from app.models import Shift, User
from app.security.auth import require
from app.services.timeutil import DAY_NAMES

router = APIRouter(prefix="/api/shifts", tags=["shifts"])

NINE_AM = 9 * 60


def desk_at_9(shifts: list[Shift]) -> int:
    """People on the phones at 9:00: desk shifts with start <= 9:00 < end (reference deskAt9)."""
    return sum(1 for s in shifts if s.role == "desk" and s.start_min <= NINE_AM < s.end_min)


@router.get("")
def list_shifts(user: User = Depends(require("schedule.view")), db: Session = Depends(get_db)):
    rows = db.scalars(select(Shift).order_by(Shift.weekday, Shift.start_min, Shift.id)).all()
    days = []
    for wd, name in enumerate(DAY_NAMES):
        todays = [s for s in rows if s.weekday == wd]
        n = desk_at_9(todays)
        if wd == 6:
            cover = None  # Sunday: closed, voicemail only
        else:
            cover = "ok" if n >= 2 else ("warn" if n == 1 else "high")
        days.append(
            {
                "weekday": wd,
                "day": name,
                "closed": not todays,
                "shifts": [serializers.shift(s) for s in todays],
                "desk_at_9": None if wd == 6 else n,
                "cover": cover,
                "busiest": wd == 5,  # reference note: Saturday is the busiest hour of the week
            }
        )
    return {"items": days}
