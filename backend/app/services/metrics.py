"""Numbers for the Brief, the rules and the assistant. Every figure comes from a database query."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import case, func, select, text
from sqlalchemy.orm import Session

from app.models import CallAnalytic, Callback, CallLogDemo, Conversation, LabResult, PimsPatient, Shift
from app.seed import reference_data as ref
from app.services import settings_store, timeutil

HOURS = list(range(8, 18))  # 8 am to 5 pm
HOUR_LABELS = ref.HOURS_LABELS
DEMO_RECALL_OVERDUE = 120  # example constant, used only when no patient data was imported
DEMO_RECALL_BOOKINGS = 18
RULES_KEY = "rules"
MISSED_OUTCOMES = ("no_answer", "voicemail")


def rules(db: Session) -> dict[str, Any]:
    out = dict(ref.RULES_DEFAULT)
    stored = settings_store.get(db, RULES_KEY, None)
    if isinstance(stored, dict):
        out.update({k: v for k, v in stored.items() if k in out and isinstance(v, (int, float))})
    return out


# ---------------------------------------------------------------- simple counts
def open_callbacks(db: Session) -> dict[str, int]:
    rows = db.execute(select(Callback.priority).where(Callback.status != "done")).all()
    return {"open": len(rows), "high": sum(1 for (p,) in rows if p == "high")}


def results_waiting(db: Session, now: datetime | None = None, min_age: int | None = None) -> list[LabResult]:
    now = now or timeutil.now_utc()
    min_age = rules(db)["results_age_days"] if min_age is None else min_age
    rows = db.scalars(select(LabResult).where(LabResult.stage < 2)).all()
    return [r for r in rows if timeutil.age_days(r.received_at, now) >= min_age]


def overnight_counts(db: Session) -> dict[str, int]:
    rows = db.execute(select(Conversation.kind, Conversation.status)).all()
    return {
        "total": len(rows),
        "booking": sum(1 for k, _ in rows if k == "booking"),
        "callback": sum(1 for k, _ in rows if k == "callback"),
        "emergency": sum(1 for k, _ in rows if k == "emergency"),
        "pending": sum(1 for _, s in rows if s == "new"),
        "emergency_pending": sum(1 for k, s in rows if k == "emergency" and s == "new"),
    }


# ---------------------------------------------------------------- missed calls
def _week_bounds(now: datetime, weeks_back: int) -> tuple[datetime, datetime]:
    today = timeutil.today_local(now)
    monday = today - timedelta(days=today.weekday()) - timedelta(days=7 * weeks_back)
    return timeutil.at_local(monday, 0), timeutil.at_local(monday + timedelta(days=7), 0)


def missed_last_week(db: Session, now: datetime | None = None) -> dict[str, Any]:
    """Unanswered calls last week. Real imported calls if any in the last 7 days, else the demo log."""
    now = now or timeutil.now_utc()
    rows = db.execute(
        text("SELECT outcome FROM visible_calls WHERE timestamp >= :s AND timestamp <= :e"),
        {"s": (now - timedelta(days=7)).replace(tzinfo=None), "e": now.replace(tzinfo=None)},
    ).all()
    if rows:
        n = sum(1 for (o,) in rows if (o or "").lower() in MISSED_OUTCOMES)
        return {"value": n, "demo": False, "again": None, "never": None}
    start, end = _week_bounds(now, 1)
    q = select(func.count(), func.sum(case((CallLogDemo.rang_again_within_hour.is_(True), 1), else_=0))).where(
        CallLogDemo.answered.is_(False), CallLogDemo.ts >= start, CallLogDemo.ts < end)
    n, again = db.execute(q).one()
    n, again = n or 0, int(again or 0)
    return {"value": n, "demo": True, "again": again, "never": n - again}


def missed_prior_week(db: Session, now: datetime | None = None) -> dict[str, Any]:
    now = now or timeutil.now_utc()
    start, end = _week_bounds(now, 2)
    q = select(func.count(), func.sum(case((CallLogDemo.rang_again_within_hour.is_(True), 1), else_=0))).where(
        CallLogDemo.answered.is_(False), CallLogDemo.ts >= start, CallLogDemo.ts < end)
    n, again = db.execute(q).one()
    n, again = n or 0, int(again or 0)
    return {"value": n, "again": again, "never": n - again}


def missed_gaps(db: Session, now: datetime | None = None, min_cell: int = 4) -> str:
    """'Tuesday and Thursday from 8 to 10 am, and Saturday at 9 am' from last week's busiest unanswered cells."""
    now = now or timeutil.now_utc()
    start, end = _week_bounds(now, 1)
    rows = db.execute(
        select(CallLogDemo.weekday, CallLogDemo.hour, func.count()).where(
            CallLogDemo.answered.is_(False), CallLogDemo.ts >= start, CallLogDemo.ts < end
        ).group_by(CallLogDemo.weekday, CallLogDemo.hour)
    ).all()
    cells = [(wd, h) for wd, h, c in rows if c >= min_cell]
    if not cells:
        return ""
    by_span: dict[tuple[int, int], list[int]] = {}
    byday: dict[int, list[int]] = {}
    for wd, h in cells:
        byday.setdefault(wd, []).append(h)
    for wd, hs in byday.items():
        hs.sort()
        by_span.setdefault((hs[0], hs[-1]), []).append(wd)
    full = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

    def hour_txt(h: int) -> str:
        return f"{h % 12 or 12}"

    parts = []
    for (lo, hi), days in sorted(by_span.items(), key=lambda kv: min(kv[1])):
        names = [full[d] for d in sorted(days)]
        who = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
        suffix = "am" if hi < 12 else "pm"
        if lo == hi:
            parts.append(f"{who} at {hour_txt(lo)} {suffix}")
        else:
            parts.append(f"{who} from {hour_txt(lo)} to {hour_txt(hi + 1)} {suffix}")
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + ", and " + parts[-1]


# ---------------------------------------------------------------- recalls
def recall_overdue(db: Session, now: datetime | None = None) -> dict[str, Any]:
    now = now or timeutil.now_utc()
    total = db.scalar(select(func.count()).select_from(PimsPatient)) or 0
    if total == 0:
        return {"value": DEMO_RECALL_OVERDUE, "demo": True}
    cutoff = now - timedelta(days=395)  # 13 months
    n = db.scalar(select(func.count()).select_from(PimsPatient).where(PimsPatient.last_exam < cutoff)) or 0
    return {"value": n, "demo": False}


# ---------------------------------------------------------------- phone cover and chart
def cover_by_hour(db: Session) -> list[float]:
    """Average, Mon to Fri, of desk or open shifts covering each hour (8 am to 5 pm)."""
    shifts = db.scalars(select(Shift).where(Shift.role.in_(("desk", "open")), Shift.weekday <= 4)).all()
    out = []
    for h in HOURS:
        total = sum(1 for s in shifts if s.start_min <= h * 60 < s.end_min)
        out.append(total / 5)
    return out


def real_calls_by_hour(db: Session) -> list[float] | None:
    rows = db.execute(select(CallAnalytic.dim1, CallAnalytic.value).where(CallAnalytic.metric == "weekday_avg_calls_by_hour")).all()
    if not rows:
        return None
    by = {}
    for dim, v in rows:
        try:
            by[int(dim)] = float(v)
        except ValueError:
            continue
    if not by:
        return None
    return [by.get(h, 0.0) for h in HOURS]


def chart(db: Session, mode: str | None = None) -> dict[str, Any]:
    real = real_calls_by_hour(db)
    available = ["real", "example"] if real else ["example"]
    chosen = mode if mode in available else ("real" if real else "example")
    calls = real if chosen == "real" and real else [float(c) for c in ref.EXAMPLE_CALLS]
    cover = [max(0, round(c)) for c in cover_by_hour(db)]
    per_person = [round(c / max(cv, 1), 1) for c, cv in zip(calls, cover)]
    return {
        "mode": chosen, "available_modes": available, "hours": HOUR_LABELS, "calls": [round(c, 1) for c in calls],
        "cover": cover, "per_person": per_person, "limit": rules(db)["load_limit"],
        "calls_label": "Real recorded call volume, average weekday" if chosen == "real" else "Example numbers",
        "cover_label": "Assumption: example roster",
    }


# ---------------------------------------------------------------- week compare
def week_compare(db: Session, now: datetime | None = None) -> dict[str, Any]:
    now = now or timeutil.now_utc()
    last, prior = missed_last_week(db, now), missed_prior_week(db, now)
    return {"missed_last": last["value"], "missed_prior": prior["value"], "missed_delta": last["value"] - prior["value"],
            "again_last": last["again"], "never_last": last["never"], "recall_bookings_last": DEMO_RECALL_BOOKINGS,
            "recall_bookings_prior": DEMO_RECALL_BOOKINGS, "demo": last["demo"]}
