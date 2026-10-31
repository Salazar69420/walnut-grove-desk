"""Numbers for the Brief, the rules and the assistant. Every figure comes from a database query."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import case, func, select, text
from sqlalchemy.orm import Session

from app.models import CallAnalytic, Callback, CallLogDemo, Conversation, LabResult, PimsPatient, Shift
from app.seed import reference_data as ref
from app.services import live_ingest, settings_store, timeutil

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


# ---------------------------------------------------------------- live numbers (docs/CONTRACT_LIVE_BRIEF.md)
UNANSWERED = ("missed", "voicemail", "hangup")
FOLLOWUP_JUDGED = ("rang_again_1h", "called_back_4h", "called_back_24h", "no_contact_24h")
LIVE_TAGS = {"missed_7d": "Missed calls last 7 days", "no_contact_24h": "Callers who never rang back",
             "calls_7d": "Calls last 7 days", "after_hours_7d": "After-hours calls last 7 days"}


def _hour_label(h: int) -> str:
    return f"{h % 12 or 12} {'am' if h < 12 else 'pm'}"


def _pct(part: float, whole: float) -> int | None:
    return round(100 * part / whole) if whole > 0 else None


class Live:
    """Read-only view of the telus_live rows. A metric that is not there is None, never a made-up zero."""

    def __init__(self, db: Session):
        rows = db.execute(select(CallAnalytic.metric, CallAnalytic.dim1, CallAnalytic.dim2, CallAnalytic.value, CallAnalytic.n)
                          .where(CallAnalytic.source == live_ingest.SOURCE)).all()
        self.rows = {(m, d1 or "", d2 or ""): (v, n) for m, d1, d2, v, n in rows}
        m = live_ingest.meta(db)
        self.as_of: str | None = m.get("generated_at") if self.rows and isinstance(m.get("generated_at"), str) else None

    def has(self, metric: str, period: str | None = None) -> bool:
        return any(k[0] == metric and (period is None or k[1] == period) for k in self.rows)

    def val(self, metric: str, d1: str, d2: str = "") -> float | None:
        r = self.rows.get((metric, d1, d2))
        return r[0] if r else None

    def total(self, metric: str, period: str, parts) -> int | None:
        """Sum of the parts that exist. The pipeline leaves out cells under 5, so a missing part counts as 0."""
        if not self.has(metric, period):
            return None
        return int(round(sum(self.val(metric, period, p) or 0 for p in parts)))

    @property
    def available(self) -> bool:
        return bool(self.rows)

    # -- the four numbers
    def missed(self, period: str = "7d") -> int | None:
        return self.total("live_inbound_result", period, UNANSWERED)

    def followup(self, period: str = "7d") -> dict[str, Any] | None:
        if not self.has("live_missed_followup", period):
            return None
        parts = {k: int(round(self.val("live_missed_followup", period, k) or 0)) for k in (*FOLLOWUP_JUDGED, "too_recent")}
        judged = sum(parts[k] for k in FOLLOWUP_JUDGED)
        return {**parts, "total": judged + parts["too_recent"], "judged": judged,
                "never_percent": _pct(parts["no_contact_24h"], judged)}

    def calls(self, period: str = "7d") -> dict[str, int | None] | None:
        if not self.has("live_calls", period):
            return None
        inb, out = self.val("live_calls", period, "inbound"), self.val("live_calls", period, "outbound")
        allc = self.val("live_calls", period, "all")
        if allc is None and inb is not None and out is not None:
            allc = inb + out
        if allc is None and inb is None and out is None:
            return None
        r = lambda x: None if x is None else int(round(x))  # noqa: E731
        return {"all": r(allc), "inbound": r(inb), "outbound": r(out)}

    def after_hours(self, period: str = "7d") -> dict[str, int] | None:
        if not self.has("live_after_hours", period):
            return None
        return {"inbound": int(round(self.val("live_after_hours", period, "inbound") or 0)),
                "voicemail": int(round(self.val("live_after_hours", period, "voicemail") or 0))}

    # -- charts and extras
    def by_hour(self, metric: str = "live_inbound_by_hour") -> list[float] | None:
        if not self.has(metric):
            return None
        return [float(self.val(metric, str(h)) or 0.0) for h in HOURS]

    def daily(self) -> list[dict[str, Any]]:
        out = []
        for (m, d1, _d2), (v, _n) in self.rows.items():
            if m == "live_daily_inbound":
                out.append({"date": d1, "calls": int(round(v))})
        out.sort(key=lambda r: r["date"])
        return out[-14:]

    def busiest(self) -> dict[str, Any] | None:
        best = [(k[1], v) for k, (v, _n) in self.rows.items() if k[0] == "live_busiest_hour"]
        if not best:
            return None
        h, v = max(best, key=lambda x: x[1])
        return {"hour": int(h), "label": _hour_label(int(h)), "average": round(v, 1)}

    def past_share(self) -> dict[str, Any] | None:
        r = self.rows.get(("live_past_followup_share", "past_recordings", ""))
        if not r or not 0 <= r[0] <= 1 or r[1] < 5:
            return None
        return {"share": round(r[0], 3), "percent": round(100 * r[0]), "calls": r[1]}


def live(db: Session) -> Live:
    return Live(db)


def live_brief(db: Session) -> dict[str, Any]:
    """The `live` block of GET /api/brief. Counts and rates only."""
    lv = Live(db)

    def change(now: int | None, before: int | None) -> dict[str, Any] | None:
        if now is None or before is None:
            return None
        return {"previous": before, "delta": now - before}

    m7, mp = lv.missed("7d"), lv.missed("prev7d")
    fu = lv.followup("7d")
    c7, cp = lv.calls("7d"), lv.calls("prev7d")
    ah, ahp = lv.after_hours("7d"), lv.after_hours("prev7d")
    tiles = [
        {"key": "missed_7d", "label": LIVE_TAGS["missed_7d"], "available": m7 is not None, "value": m7,
         "change": change(m7, mp), "detail": None},
        {"key": "no_contact_24h", "label": LIVE_TAGS["no_contact_24h"], "available": fu is not None,
         "value": fu["no_contact_24h"] if fu else None, "out_of": fu["judged"] if fu else None,
         "percent": fu["never_percent"] if fu else None, "change": None, "detail": None},
        {"key": "calls_7d", "label": LIVE_TAGS["calls_7d"], "available": c7 is not None, "value": c7["all"] if c7 else None,
         "inbound": c7["inbound"] if c7 else None, "outbound": c7["outbound"] if c7 else None,
         "change": change(c7["all"], cp["all"]) if c7 and cp else None, "detail": None},
        {"key": "after_hours_7d", "label": LIVE_TAGS["after_hours_7d"], "available": ah is not None,
         "value": ah["inbound"] if ah else None, "voicemail": ah["voicemail"] if ah else None,
         "change": change(ah["inbound"], ahp["inbound"]) if ah and ahp else None, "detail": None},
    ]
    for t in tiles:
        t.pop("detail")
        t["origin"] = "live"
    return {
        "available": lv.available, "as_of": lv.as_of, "tiles": tiles,
        "missed_followup": ({"available": True, **fu} if fu else {"available": False}),
        "after_hours": ({"available": True, **ah, "previous_inbound": ahp["inbound"] if ahp else None} if ah else {"available": False}),
        "busiest_hour": ({"available": True, **lv.busiest()} if lv.busiest() else {"available": False}),
        "daily": lv.daily(),
        "past_followup_share": lv.past_share(),
    }


def data_origins(db: Session) -> dict[str, str]:
    """'example' when every row behind a tile is seeded demo data, otherwise 'live' (real or entered by staff)."""
    cb_total = db.scalar(select(func.count()).select_from(Callback)) or 0
    cb_real = db.scalar(select(func.count()).select_from(Callback).where(Callback.source != "seed")) or 0
    cv_total = db.scalar(select(func.count()).select_from(Conversation)) or 0
    cv_real = db.scalar(select(func.count()).select_from(Conversation).where(
        Conversation.source != "seed", Conversation.is_test.is_(False))) or 0
    return {
        "callbacks_open": "example" if cb_total and not cb_real else "live",
        "results_waiting": "example",  # only the seed creates lab results today; no route adds them
        "overnight_pending": "example" if cv_total and not cv_real else "live",
    }


# ---------------------------------------------------------------- missed calls
def _week_bounds(now: datetime, weeks_back: int) -> tuple[datetime, datetime]:
    today = timeutil.today_local(now)
    monday = today - timedelta(days=today.weekday()) - timedelta(days=7 * weeks_back)
    return timeutil.at_local(monday, 0), timeutil.at_local(monday + timedelta(days=7), 0)


def missed_last_week(db: Session, now: datetime | None = None) -> dict[str, Any]:
    """Unanswered calls last week. Real imported calls if any in the last 7 days, else the demo log."""
    now = now or timeutil.now_utc()
    lv = Live(db)
    live_missed = lv.missed("7d")
    if live_missed is not None:
        fu = lv.followup("7d")
        return {"value": live_missed, "demo": False, "live": True, "again": fu["rang_again_1h"] if fu else None,
                "never": fu["no_contact_24h"] if fu else None, "judged": fu["judged"] if fu else None}
    rows = db.execute(
        text("SELECT outcome FROM visible_calls WHERE timestamp >= :s AND timestamp <= :e"),
        {"s": (now - timedelta(days=7)).replace(tzinfo=None), "e": now.replace(tzinfo=None)},
    ).all()
    if rows:
        n = sum(1 for (o,) in rows if (o or "").lower() in MISSED_OUTCOMES)
        return {"value": n, "demo": False, "live": False, "again": None, "never": None}
    start, end = _week_bounds(now, 1)
    q = select(func.count(), func.sum(case((CallLogDemo.rang_again_within_hour.is_(True), 1), else_=0))).where(
        CallLogDemo.answered.is_(False), CallLogDemo.ts >= start, CallLogDemo.ts < end)
    n, again = db.execute(q).one()
    n, again = n or 0, int(again or 0)
    return {"value": n, "demo": True, "live": False, "again": again, "never": n - again}


def missed_prior_week(db: Session, now: datetime | None = None) -> dict[str, Any]:
    """The week before. `value` is None when real numbers exist for this week but not for the week before."""
    now = now or timeutil.now_utc()
    lv = Live(db)
    if lv.missed("7d") is not None:  # real numbers: the week before is real too, or not available (never mixed with the demo log)
        return {"value": lv.missed("prev7d"), "again": None, "never": None, "live": True}
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
    return real_hourly(db)[0]


def real_hourly(db: Session) -> tuple[list[float] | None, str | None]:
    """(calls per hour 8 am to 5 pm, 'live' | 'imported' | None). The phone system numbers win over imported ones."""
    lv = Live(db).by_hour()
    if lv is not None:
        return lv, "live"
    imported = _imported_by_hour(db)
    return imported, ("imported" if imported else None)


def _imported_by_hour(db: Session) -> list[float] | None:
    rows = db.execute(select(CallAnalytic.dim1, CallAnalytic.value).where(CallAnalytic.metric == "weekday_avg_calls_by_hour", CallAnalytic.source != live_ingest.SOURCE)).all()
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
    real, kind = real_hourly(db)
    available = ["real", "example"] if real else ["example"]
    chosen = mode if mode in available else ("real" if real else "example")
    calls = real if chosen == "real" and real else [float(c) for c in ref.EXAMPLE_CALLS]
    cover = [max(0, round(c)) for c in cover_by_hour(db)]
    per_person = [round(c / max(cv, 1), 1) for c, cv in zip(calls, cover)]
    return {
        "mode": chosen, "available_modes": available, "hours": HOUR_LABELS, "calls": [round(c, 1) for c in calls],
        "cover": cover, "per_person": per_person, "limit": rules(db)["load_limit"],
        "calls_label": ("Real recorded call volume, average weekday, last 4 weeks" if kind == "live"
                        else "Real recorded call volume, average weekday") if chosen == "real" else "Example numbers",
        "cover_label": "Assumption: example roster",
    }


# ---------------------------------------------------------------- week compare
def week_compare(db: Session, now: datetime | None = None) -> dict[str, Any]:
    now = now or timeutil.now_utc()
    last, prior = missed_last_week(db, now), missed_prior_week(db, now)
    delta = None if prior["value"] is None else last["value"] - prior["value"]
    return {"missed_last": last["value"], "missed_prior": prior["value"], "missed_delta": delta,
            "again_last": last["again"], "never_last": last["never"], "recall_bookings_last": DEMO_RECALL_BOOKINGS,
            "recall_bookings_prior": DEMO_RECALL_BOOKINGS, "demo": last["demo"], "live": last.get("live", False)}
