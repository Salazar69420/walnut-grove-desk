"""Time helpers. Everything is stored in UTC and displayed in America/Vancouver."""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

VANCOUVER = ZoneInfo("America/Vancouver")
DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

_clock_override: datetime | None = None


def now_utc() -> datetime:
    if _clock_override is not None:
        return _clock_override
    return datetime.now(timezone.utc)


def set_clock(dt: datetime | None) -> None:
    """Freeze the clock (tests only). Pass None to release."""
    global _clock_override
    _clock_override = dt


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def local(dt: datetime) -> datetime:
    return dt.astimezone(VANCOUVER)


def local_date(dt: datetime) -> date:
    return local(dt).date()


def today_local(now: datetime | None = None) -> date:
    return local(now or now_utc()).date()


def clock_text(dt: datetime) -> str:
    """9:31 am style."""
    d = local(dt)
    h = d.hour % 12 or 12
    return f"{h}:{d.minute:02d} {'am' if d.hour < 12 else 'pm'}"


def display_time(dt: datetime, now: datetime | None = None) -> str:
    """'9:31 am' for today, 'Oct 27, 9:31 am' for other days (Vancouver dates)."""
    d = local(dt)
    if d.date() == today_local(now):
        return clock_text(dt)
    return f"{d.strftime('%b')} {d.day}, {clock_text(dt)}"


def slot_display(dt: datetime) -> str:
    """'Thu Oct 29, 9:30 am'."""
    d = local(dt)
    return f"{DAY_NAMES[d.weekday()]} {d.strftime('%b')} {d.day}, {clock_text(dt)}"


def hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def age_days(received_at: datetime, now: datetime | None = None) -> int:
    """Whole Vancouver calendar days between received_at and now."""
    return max(0, (today_local(now) - local_date(received_at)).days)


_CLOCK_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*(am|pm)\s*$", re.I)


def parse_clock(text: str) -> tuple[int, int, bool]:
    """'9:31 am' -> (9, 31, is_pm). Raises ValueError on a bad string."""
    m = _CLOCK_RE.match(text)
    if not m:
        raise ValueError(f"bad clock text: {text!r}")
    h, mi, ap = int(m.group(1)), int(m.group(2)), m.group(3).lower()
    if not (1 <= h <= 12 and 0 <= mi <= 59):
        raise ValueError(f"bad clock text: {text!r}")
    h = h % 12 + (12 if ap == "pm" else 0)
    return h, mi, ap == "pm"


def at_local(day: date, hour: int, minute: int = 0) -> datetime:
    """A Vancouver wall-clock time on a date, as an aware UTC datetime."""
    return datetime.combine(day, time(hour, minute), tzinfo=VANCOUVER).astimezone(timezone.utc)


def anchor(clock: str, now: datetime | None = None, pm_is_yesterday: bool = False) -> datetime:
    """Place a reference clock string ('9:31 am') on today (Vancouver).

    With pm_is_yesterday, 'pm' times land on the previous day (overnight items).
    """
    h, mi, is_pm = parse_clock(clock)
    day = today_local(now)
    if pm_is_yesterday and is_pm:
        day = day - timedelta(days=1)
    return at_local(day, h, mi)
