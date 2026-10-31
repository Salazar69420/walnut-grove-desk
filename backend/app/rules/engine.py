"""Rules that propose changes. Nothing happens until the owner approves a suggestion."""
from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import CallLogDemo, Suggestion
from app.services import events as ev
from app.services import metrics, settings_store, timeutil

COOLDOWN_DAYS = 7


def _blocked(db: Session, rule_key: str, now) -> bool:
    """A suggestion for this rule is open, or was approved or dismissed in the last 7 days."""
    rows = db.scalars(select(Suggestion).where(Suggestion.rule_key == rule_key)).all()
    for r in rows:
        if r.status == "open":
            return True
        if r.status in ("approved", "dismissed") and r.decided_at and now - r.decided_at < timedelta(days=COOLDOWN_DAYS):
            return True
    return False


def _hour_text(h: int) -> str:
    return f"{h % 12 or 12} {'am' if h < 12 else 'pm'}"


def _desk_shift(db: Session, th: dict[str, Any]) -> dict[str, Any] | None:
    real, kind = metrics.real_hourly(db)
    if real:
        calls = real
        source = "real, from the phone system" if kind == "live" else "real recorded calls"
    else:
        calls = [float(c) for c in metrics.ref.EXAMPLE_CALLS]
        source = "example numbers"
    cover = [max(c, 1) for c in metrics.cover_by_hour(db)]
    cover = [max(1, round(c)) for c in cover]
    hot = [h for h, c, cv in zip(metrics.HOURS, calls, cover) if c / cv > th["load_limit"]]
    # the rule needs the load on at least min_weekdays weekdays; averages apply to every weekday
    if not hot or th["min_weekdays"] > 5:
        return None
    lo, hi = min(hot), max(hot) + 1
    peak_i = max(range(len(calls)), key=lambda i: calls[i] / cover[i])
    peak_h = metrics.HOURS[peak_i]
    return {
        "title": f"Add an open desk shift, {lo:02d}:00 to {hi:02d}:00, Monday to Friday",
        "why": (f"Calls peak at {_hour_text(peak_h)} (about {round(calls[peak_i])} an hour, {source}) with {cover[peak_i]} "
                f"{'person' if cover[peak_i] == 1 else 'people'} on the phones. Add a second person and each person's load drops."),
        "evidence": {"start_min": lo * 60, "end_min": hi * 60, "hours": hot, "limit": th["load_limit"], "source": source,
                     "example": not real},
    }


def _missed_texts(db: Session, th: dict[str, Any]) -> dict[str, Any] | None:
    if settings_store.get(db, "tog_missed", False):
        return None
    m = metrics.missed_last_week(db)
    if m["value"] < th["missed_min"]:
        return None
    if m.get("live"):
        nc = f" and {m['never']} of those callers had no contact within 24 hours" if m.get("never") is not None else ""
        why = f"{m['value']} calls went unanswered last week{nc} (real, from the phone system). A text with a booking link reaches them."
    else:
        never = f" and {m['never']} of those callers never rang again" if m.get("never") is not None else ""
        why = f"{m['value']} calls went unanswered last week{never}. A text with a booking link reaches them."
    return {
        "title": "Turn on automatic texts to missed callers",
        "why": why,
        "evidence": {"missed": m["value"], "never": m.get("never"), "example": m["demo"], "live": bool(m.get("live"))},
    }


def _recall_batch(db: Session, th: dict[str, Any]) -> dict[str, Any] | None:
    r = metrics.recall_overdue(db)
    if r["value"] < th["recall_min"]:
        return None
    return {
        "title": f"Start the annual exam recall batch ({r['value']} clients overdue)",
        "why": f"Drafts go to the front desk for approval, {th['recall_batch']} at a time, so no one is overloaded.",
        "evidence": {"overdue": r["value"], "batch": th["recall_batch"], "example": r["demo"]},
    }


def _old_results(db: Session, th: dict[str, Any]) -> dict[str, Any] | None:
    waiting = [r for r in metrics.results_waiting(db, min_age=th["results_age_days"]) if r.stage == 0]
    if not waiting:
        return None
    n = len(waiting)
    return {
        "title": "Review lab results older than two days" if th["results_age_days"] == 2 else f"Review lab results older than {th['results_age_days']} days",
        "why": f"{n} {'result is' if n == 1 else 'results are'} waiting for a doctor review. Nurses can call once you mark them reviewed.",
        "evidence": {"count": n, "result_ids": [r.id for r in waiting], "min_age_days": th["results_age_days"]},
    }


RULES = {
    "desk_shift": _desk_shift,
    "missed_texts": _missed_texts,
    "recall_batch": _recall_batch,
    "old_results": _old_results,
}


def run_rules(db: Session, settings: dict[str, Any] | None = None) -> list[Suggestion]:
    """Create a suggestion for each rule that fires. Idempotent while one is open or recently decided."""
    th = metrics.rules(db)
    if settings:
        th.update({k: v for k, v in settings.items() if k in th})
    now = timeutil.now_utc()
    created: list[Suggestion] = []
    for key, fn in RULES.items():
        if _blocked(db, key, now):
            continue
        found = fn(db, th)
        if not found:
            continue
        s = Suggestion(rule_key=key, title=found["title"], why=found["why"], evidence_json=json.dumps(found["evidence"]),
                       status="open", created_at=now)
        db.add(s)
        db.flush()
        ev.record(db, ev.ADMIN, "suggestion_created", f"New suggestion: {s.title}", None, "suggestion", s.id, {"rule_key": key})
        created.append(s)
    return created
