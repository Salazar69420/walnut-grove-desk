"""Live numbers for the Brief: aggregate rows computed from telus_calls (hashes only) and the labels table.

Binding contract: docs/CONTRACT_LIVE_BRIEF.md. Every function here returns counts and averages only. Phone
number hashes are used in memory to match calls to each other and are never put in a row, a log line or an
exception. All days are America/Vancouver local days (the `date`, `hour` and `weekday` columns of telus_calls
were computed in that zone, so daylight-saving changes need no special handling here).
"""
from __future__ import annotations

import json
from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone

from .. import db as dbm
from ..config import TZ_NAME
from ..rules import LABELER_ID

try:
    from zoneinfo import ZoneInfo
    _TZ = ZoneInfo(TZ_NAME)
except Exception:  # pragma: no cover
    _TZ = timezone.utc

MIN_CELL = 5
HOURS = range(8, 18)                       # 08:00 to 17:59 local, the clinic's opening hours
HOURLY_DAYS = 28                           # hourly averages look at the last 28 full local days
DAILY_DAYS = 14
UNANSWERED = ("missed", "voicemail", "hangup")
INBOUND_RESULTS = ("answered", "missed", "voicemail", "hangup", "other")
FOLLOWUP_BUCKETS = ("rang_again_1h", "called_back_4h", "called_back_24h", "no_contact_24h", "too_recent")
PERIODS = ("7d", "prev7d")
HOUR_S = 3600.0
RING_AGAIN_S = 1 * HOUR_S
CALLBACK_FAST_S = 4 * HOUR_S
CALLBACK_SLOW_S = 24 * HOUR_S
TOO_RECENT_S = 24 * HOUR_S


def is_after_hours(weekday: int, hour: int) -> bool:
    """Weekdays before 08:00 or from 18:00; Saturday before 09:00 or from 14:00; all Sunday."""
    if weekday <= 4:
        return hour < 8 or hour >= 18
    if weekday == 5:
        return hour < 9 or hour >= 14
    return True


def local_today(now: datetime) -> date:
    return now.astimezone(_TZ).date()


def period_of(day: date, today: date) -> str | None:
    """'7d' = the 7 full local days ending yesterday, 'prev7d' = the 7 before those."""
    ago = (today - day).days
    if 1 <= ago <= 7:
        return "7d"
    if 8 <= ago <= 14:
        return "prev7d"
    return None


def window(now: datetime) -> tuple[date, date]:
    """(first, last) local day covered by the rows: 28 full days ending yesterday."""
    today = local_today(now)
    return today - timedelta(days=HOURLY_DAYS), today - timedelta(days=1)


def _epoch(start_local: str) -> float | None:
    try:
        dt = datetime.fromisoformat(start_local)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_TZ)
    return dt.timestamp()


class _Row:
    __slots__ = ("period", "day", "hour", "weekday", "direction", "result", "t", "from_hash", "to_hash")


def _load(conn, first: date) -> list[_Row]:
    out = []
    q = ("SELECT start_local, date, hour, weekday, direction, result, from_hash, to_hash FROM telus_calls "
         "WHERE date >= ?")
    for r in conn.execute(q, (first.isoformat(),)):
        t = _epoch(r["start_local"])
        if t is None:
            continue
        x = _Row()
        x.day = date.fromisoformat(r["date"])
        x.hour, x.weekday, x.direction, x.result = r["hour"], r["weekday"], r["direction"], r["result"]
        x.t, x.from_hash, x.to_hash = t, r["from_hash"], r["to_hash"]
        out.append(x)
    return out


def followup_bucket(call_t: float, age_s: float, from_hash: str | None, inbound_by_caller: dict,
                    outbound_by_callee: dict) -> str:
    """Where one unanswered inbound call ends up. Earliest matching bucket in the listed order wins.

    Matching uses only the salted hashes: the same from_hash phoning again (inbound, later than this call,
    up to 1 h), else an outbound call whose to_hash equals that from_hash (up to 4 h, then up to 24 h).
    A call under 24 h old is too_recent; a call with no number can never be matched."""
    if age_s < TOO_RECENT_S:
        return "too_recent"
    if not from_hash:
        return "no_contact_24h"
    again = inbound_by_caller.get(from_hash)
    if again:
        i = bisect_right(again, call_t)
        if i < len(again) and again[i] - call_t <= RING_AGAIN_S:
            return "rang_again_1h"
    back = outbound_by_callee.get(from_hash)
    if back:
        i = bisect_right(back, call_t)
        if i < len(back):
            gap = back[i] - call_t
            if gap <= CALLBACK_FAST_S:
                return "called_back_4h"
            if gap <= CALLBACK_SLOW_S:
                return "called_back_24h"
    return "no_contact_24h"


def past_followup(conn, min_cell: int = MIN_CELL) -> tuple[float, int] | None:
    """(share, n) of the past labeled calls where a follow-up was promised. Same rules as analytics.py: the
    rules-v1 labels with gold winning, calls flagged private are left out, and the cell is dropped when
    min(numerator, denominator) is under 5. None when dropped or when there is nothing labeled."""
    labels = dbm.chosen_labels(conn, LABELER_ID)
    if not labels:
        return None
    n = promised = 0
    for r in conn.execute("SELECT call_ref FROM calls"):
        got = labels.get(r["call_ref"])
        if not got:
            continue
        try:
            lab = json.loads(got[1])
        except ValueError:
            continue
        if not isinstance(lab, dict) or lab.get("contains_private_content"):
            continue
        n += 1
        promised += 1 if lab.get("promised_followup") else 0
    if n == 0 or min(promised, n) < min_cell:
        return None
    return round(promised / n, 4), n


def compute(conn, now: datetime, min_cell: int = MIN_CELL) -> dict:
    """{"window": {"from","to"}, "rows": [...]} for analytics.json. `now` is generated_at (aware)."""
    now = now.astimezone(timezone.utc)
    first, last = window(now)
    today = local_today(now)
    rows: list[dict] = []

    def add(metric, dim1, dim2, value, n, small_ok=True):
        if not small_ok and n < min_cell:
            return
        rows.append({"metric": metric, "dim1": dim1, "dim2": dim2, "value": value, "n": n})

    if conn.execute("SELECT 1 FROM telus_calls LIMIT 1").fetchone() is None:
        return {"window": {"from": first.isoformat(), "to": last.isoformat()}, "rows": []}  # no data yet
    calls = _load(conn, first)
    inbound_by_caller: dict[str, list[float]] = defaultdict(list)
    outbound_by_callee: dict[str, list[float]] = defaultdict(list)
    for c in calls:
        if c.direction == "inbound" and c.from_hash:
            inbound_by_caller[c.from_hash].append(c.t)
        elif c.direction == "outbound" and c.to_hash:
            outbound_by_callee[c.to_hash].append(c.t)
    for lst in list(inbound_by_caller.values()) + list(outbound_by_callee.values()):
        lst.sort()

    total = Counter()                 # (period, direction all/inbound/outbound)
    inbound_result = Counter()        # (period, result)
    followup = Counter()              # (period, bucket)
    after_hours = Counter()           # (period, inbound/voicemail)
    hourly_in, hourly_missed = Counter(), Counter()
    daily = Counter()
    now_ts = now.timestamp()

    for c in calls:
        per = period_of(c.day, today)
        inbound = c.direction == "inbound"
        unanswered = inbound and c.result in UNANSWERED
        if per:
            total[(per, "all")] += 1
            if c.direction in ("inbound", "outbound"):
                total[(per, c.direction)] += 1
            if inbound:
                inbound_result[(per, c.result if c.result in INBOUND_RESULTS else "other")] += 1
                if is_after_hours(c.weekday, c.hour):
                    after_hours[(per, "inbound")] += 1
                    if c.result == "voicemail":
                        after_hours[(per, "voicemail")] += 1
            if unanswered:
                followup[(per, followup_bucket(c.t, now_ts - c.t, c.from_hash, inbound_by_caller,
                                               outbound_by_callee))] += 1
        if inbound and first <= c.day <= last:
            if c.weekday <= 4 and c.hour in HOURS:
                hourly_in[c.hour] += 1
                if unanswered:
                    hourly_missed[c.hour] += 1
            if (today - c.day).days <= DAILY_DAYS:
                daily[c.day.isoformat()] += 1

    for p in PERIODS:
        for d in ("all", "inbound", "outbound"):
            add("live_calls", p, d, total[(p, d)], total[(p, d)])
        for r in INBOUND_RESULTS:
            add("live_inbound_result", p, r, inbound_result[(p, r)], inbound_result[(p, r)])
        for b in FOLLOWUP_BUCKETS:
            add("live_missed_followup", p, b, followup[(p, b)], followup[(p, b)])
        for k in ("inbound", "voicemail"):
            add("live_after_hours", p, k, after_hours[(p, k)], after_hours[(p, k)])

    weekdays = sum(1 for i in range(HOURLY_DAYS) if (first + timedelta(days=i)).weekday() <= 4)
    avg = lambda n: round(n / weekdays, 3) if weekdays else 0.0  # noqa: E731
    for h in HOURS:
        add("live_inbound_by_hour", str(h), None, avg(hourly_in[h]), hourly_in[h], small_ok=False)
        add("live_missed_by_hour", str(h), None, avg(hourly_missed[h]), hourly_missed[h], small_ok=False)
    if hourly_in:
        busiest = min(HOURS, key=lambda h: (-hourly_in[h], h))
        add("live_busiest_hour", str(busiest), None, avg(hourly_in[busiest]), hourly_in[busiest], small_ok=False)
    for i in range(DAILY_DAYS, 0, -1):
        day = (today - timedelta(days=i)).isoformat()
        add("live_daily_inbound", day, None, daily[day], daily[day], small_ok=False)

    share = past_followup(conn, min_cell)
    if share is not None:
        add("live_past_followup_share", "past_recordings", None, share[0], share[1])

    return {"window": {"from": first.isoformat(), "to": last.isoformat()}, "rows": rows}
