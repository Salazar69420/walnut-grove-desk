"""Live Brief numbers (telus-sync). All data is made up; all HTTP is mocked; nothing touches a real root.

Clock used by most tests: generated_at = 2025-11-05 14:20 UTC = Wed 06:20 PST. So "today" is 2025-11-05,
"7d" is 2025-10-29 to 2025-11-04 (it spans the daylight-saving change of Sun 2025-11-02) and "prev7d" is
2025-10-22 to 2025-10-28. Local = UTC-7 up to 2025-11-02 01:59 and UTC-8 afterwards.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import sqlite3
import stat
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx
import pytest

from pipeline import cli, db as dbm
from pipeline.telus import calllog, endpoints, live, sync, worker
from pipeline.telus import config as tc
from pipeline.telus.client import ReadOnlyClient

UTC = timezone.utc
NOW = datetime(2025, 11, 5, 14, 20, 0, tzinfo=UTC)
LOCAL = ZoneInfo("America/Vancouver")
SALT = b"fake-salt-for-tests-0123456789"
H = timedelta(hours=1)
M = timedelta(minutes=1)
S = timedelta(seconds=1)
B = datetime(2025, 10, 30, 18, 0, 0, tzinfo=UTC)  # Thu 11:00 PDT, inside "7d"

FAKE_ID, FAKE_SECRET = "FAKE_CLIENT_ID", "FAKE_CLIENT_SECRET"
FAKE_JWT = "FAKE_JWT.FAKE_PAYLOAD.FAKE_SIGNATURE"
GOOD_SCOPE = "ReadAccounts ReadCallLog ReadCallRecording ReadMessages"
SECRET_WORDS = ["FAKE_CLIENT", "FAKE_JWT", "FAKE_PAYLOAD", "FAKE_SIGNATURE", "FAKE_ACCESS", "FAKE_REFRESH"]
HEX_A = "a" * 32
HEX_B = "b" * 32
PLATFORM = "platform.devtest.ringcentral.com"


# ------------------------------------------------------------------ fixture helpers
def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def loc(y, mo, d, h, mi=0, s=0) -> datetime:
    """A local (America/Vancouver) wall-clock time as an aware datetime."""
    return datetime(y, mo, d, h, mi, s, tzinfo=LOCAL)


def phone(k: int) -> str:
    return f"+1604555{k:04d}"


def rec(cid, when, direction="Inbound", result="Accepted", who=None):
    """One made-up call-log record. `who` is the other party's fake number index (None: no number)."""
    r = {"id": f"FAKECALL{cid}", "startTime": iso(when), "duration": 20, "direction": direction,
         "result": result, "type": "Voice", "extension": {"id": "1234"}}
    side = "from" if direction == "Inbound" else "to"
    if who is not None:
        r[side] = {"phoneNumber": phone(who)}
    return r


def insert(conn, recs):
    for r in recs:
        row = calllog.transform(r, SALT)
        row["synced_at"] = "2025-11-05T14:20:00Z"
        conn.execute(calllog.UPSERT, row)
    conn.commit()


@pytest.fixture
def db(tmp_path):
    conn = dbm.connect(str(tmp_path / "private.db"), create=True)
    yield conn
    conn.close()


def compute(db, recs, now=NOW):
    insert(db, recs)
    return live.compute(db, now)


def rows_of(doc):
    out = {}
    for r in doc["rows"]:
        key = (r["metric"], r["dim1"], r["dim2"])
        assert key not in out, key
        out[key] = (r["value"], r["n"])
    return out


def counts(rows, metric, period, dims):
    return {d: rows[(metric, period, d)][0] for d in dims}


# ------------------------------------------------------------------ live_missed_followup: every bucket
def followup_fixture():
    return [
        # rang_again_1h: the same number phones again exactly 1 h later
        rec("1", B, result="Missed", who=1), rec("1b", B + H, who=1),
        # 1 s over the hour, no callback: no contact
        rec("2", B, result="Missed", who=2), rec("2b", B + H + S, who=2),
        # callback exactly 4 h later (voicemail), 4 h and 1 s (hang up), exactly 24 h, 24 h and 1 s
        rec("3", B, result="Voicemail", who=3), rec("3o", B + 4 * H, "Outbound", who=3),
        rec("4", B, result="Hang Up", who=4), rec("4o", B + 4 * H + S, "Outbound", who=4),
        rec("5", B, result="Missed", who=5), rec("5o", B + 24 * H, "Outbound", who=5),
        rec("6", B, result="Missed", who=6), rec("6o", B + 24 * H + S, "Outbound", who=6),
        # an outbound at 30 min AND a ring again at 50 min: the earliest bucket in the list wins
        rec("7", B, result="Missed", who=7), rec("7o", B + 30 * M, "Outbound", who=7), rec("7b", B + 50 * M, who=7),
        # callback at 30 min only
        rec("8", B, result="Missed", who=8), rec("8o", B + 30 * M, "Outbound", who=8),
        # no number at all: never matched, even by an outbound call that also has no number
        rec("9", B, result="Missed"), rec("9o", B + 10 * M, "Outbound"),
        # 1 s under 24 h old (too_recent even though the clinic phoned back), and exactly 24 h old (not recent)
        rec("10", NOW - 24 * H + S, result="Missed", who=10), rec("10o", NOW - 24 * H + S + 10 * M, "Outbound", who=10),
        rec("11", NOW - 24 * H, result="Missed", who=11),
        # answered calls are not unanswered; earlier contact does not count as a follow-up
        rec("12", B, who=12),
        rec("13a", B - 30 * M, who=13), rec("13", B, result="Missed", who=13),
        # two unanswered calls from one number: the first is followed by the second, the second by nothing
        rec("16a", B, result="Missed", who=16), rec("16b", B + 30 * M, result="Missed", who=16),
        # an outbound call BEFORE the missed call is not a callback
        rec("17o", B - H, "Outbound", who=17), rec("17", B, result="Missed", who=17),
        # previous week: callback after 2 h, and nothing
        rec("14", datetime(2025, 10, 24, 18, 0, 0, tzinfo=UTC), result="Missed", who=14),
        rec("14o", datetime(2025, 10, 24, 20, 0, 0, tzinfo=UTC), "Outbound", who=14),
        rec("15", datetime(2025, 10, 24, 18, 0, 0, tzinfo=UTC), result="Missed", who=15),
    ]


def test_followup_every_bucket_and_boundary(db):
    rows = rows_of(compute(db, followup_fixture()))
    seven = counts(rows, "live_missed_followup", "7d", live.FOLLOWUP_BUCKETS)
    assert seven == {"rang_again_1h": 3,        # 1, 7, 16a
                     "called_back_4h": 2,       # 3 (exactly 4 h), 8
                     "called_back_24h": 2,      # 4 (4 h + 1 s), 5 (exactly 24 h)
                     "no_contact_24h": 7,       # 2, 6, 9, 11, 13, 16b, 17
                     "too_recent": 1}           # 10
    prev = counts(rows, "live_missed_followup", "prev7d", live.FOLLOWUP_BUCKETS)
    assert prev == {"rang_again_1h": 0, "called_back_4h": 1, "called_back_24h": 0, "no_contact_24h": 1,
                    "too_recent": 0}
    # value and n are the same count, and every row of this metric is present (zeros included)
    assert rows[("live_missed_followup", "7d", "no_contact_24h")] == (7, 7)
    assert rows[("live_missed_followup", "prev7d", "too_recent")] == (0, 0)


def test_each_unanswered_call_is_counted_exactly_once(db):
    rows = rows_of(compute(db, followup_fixture()))
    for p, unanswered in (("7d", 15), ("prev7d", 2)):
        res = counts(rows, "live_inbound_result", p, live.INBOUND_RESULTS)
        assert res["missed"] + res["voicemail"] + res["hangup"] == unanswered
        assert sum(counts(rows, "live_missed_followup", p, live.FOLLOWUP_BUCKETS).values()) == unanswered
    res = counts(rows, "live_inbound_result", "7d", live.INBOUND_RESULTS)
    assert res == {"answered": 5, "missed": 13, "voicemail": 1, "hangup": 1, "other": 0}


def test_followup_bucket_function_edges():
    t = 1_000_000.0
    again = {"h": [t, t + 3600.0, t + 3601.0]}
    assert live.followup_bucket(t, 90_000, "h", again, {}) == "rang_again_1h"
    assert live.followup_bucket(t, 90_000, "h", {"h": [t, t + 3600.5]}, {}) == "no_contact_24h"
    back = {"h": [t + 14400.0]}
    assert live.followup_bucket(t, 90_000, "h", {}, back) == "called_back_4h"
    assert live.followup_bucket(t, 90_000, "h", {}, {"h": [t + 86400.0]}) == "called_back_24h"
    assert live.followup_bucket(t, 90_000, "h", {}, {"h": [t + 86400.1]}) == "no_contact_24h"
    assert live.followup_bucket(t, 90_000, "h", {}, {"h": [t - 5.0]}) == "no_contact_24h"
    assert live.followup_bucket(t, 86_399.9, "h", again, back) == "too_recent"
    assert live.followup_bucket(t, 86_400.0, "h", {}, {}) == "no_contact_24h"
    assert live.followup_bucket(t, 90_000, None, again, back) == "no_contact_24h"


def test_null_hashes_never_match_each_other(db):
    recs = [rec("a", B, result="Missed"), rec("b", B + 5 * M, result="Voicemail"),
            rec("c", B + 10 * M, "Outbound"), rec("d", B + 20 * M),                  # inbound, no number
            rec("e", NOW - 20 * H, result="Hang Up")]                                  # no number and recent
    rows = rows_of(compute(db, recs))
    assert counts(rows, "live_missed_followup", "7d", live.FOLLOWUP_BUCKETS) == {
        "rang_again_1h": 0, "called_back_4h": 0, "called_back_24h": 0, "no_contact_24h": 2, "too_recent": 1}
    assert db.execute("SELECT COUNT(*) FROM telus_calls WHERE from_hash IS NULL AND to_hash IS NULL"
                      ).fetchone()[0] == 5


def test_large_call_log_is_fast(db):
    """Index by hash, no N-squared scan: 60,000 calls from 20,000 numbers finish well inside a second or two."""
    import time
    recs = []
    for i in range(20_000):
        recs.append(rec(f"m{i}", B + i * S, result="Missed", who=i % 9000))
        recs.append(rec(f"o{i}", B + i * S + 2 * H, "Outbound", who=(i * 7) % 9000))
        recs.append(rec(f"r{i}", B + i * S + 30 * M, who=(i * 3) % 9000))
    insert(db, recs)
    t0 = time.monotonic()
    doc = live.compute(db, NOW)
    assert time.monotonic() - t0 < 10
    assert doc["rows"]


# ------------------------------------------------------------------ periods, local midnight, daylight saving
def period_fixture():
    z = lambda *a: datetime(*a, tzinfo=UTC)  # noqa: E731
    return [
        # outside: 1 s before prev7d starts (2025-10-21 23:59:59 PDT) and exactly today's midnight (PST)
        rec("x1", z(2025, 10, 22, 6, 59, 59)), rec("x2", z(2025, 11, 5, 8, 0, 0)),
        # prev7d: first second (2025-10-22 00:00:00 PDT) and last second (2025-10-28 23:59:59 PDT)
        rec("p1", z(2025, 10, 22, 7, 0, 0)), rec("p2", z(2025, 10, 29, 6, 59, 59)),
        # 7d: first second (2025-10-29 00:00:00 PDT), last second of Oct 31 PDT
        rec("a1", z(2025, 10, 29, 7, 0, 0)), rec("a2", z(2025, 11, 1, 6, 59, 59)),
        # Sunday 2025-11-02, the 25 hour day: 00:00 PDT, 01:30 PDT, 01:30 PST (repeated hour), noon PST,
        # and 23:59:59 PST
        rec("d1", z(2025, 11, 2, 7, 0, 0)), rec("d2", z(2025, 11, 2, 8, 30, 0)), rec("d3", z(2025, 11, 2, 9, 30, 0)),
        rec("d4", z(2025, 11, 2, 20, 0, 0)), rec("d5", z(2025, 11, 3, 7, 59, 59)),
        # Monday 2025-11-03 PST: 00:00:00, 00:00:01, noon, 21:00, 23:59:59 (next day in UTC)
        rec("e1", z(2025, 11, 3, 8, 0, 0)), rec("e2", z(2025, 11, 3, 8, 0, 1)), rec("e3", z(2025, 11, 3, 20, 0, 0)),
        rec("e4", z(2025, 11, 4, 5, 0, 0)), rec("e5", z(2025, 11, 4, 7, 59, 59)),
        # Tuesday 2025-11-04, last second before today's midnight
        rec("f1", z(2025, 11, 5, 7, 59, 59)),
    ]


def test_utc_calls_land_on_the_right_local_day_across_the_dst_change(db):
    insert(db, period_fixture())
    got = {r["telus_id"][8:]: (r["date"], r["hour"]) for r in db.execute("SELECT * FROM telus_calls")}
    assert got["x1"] == ("2025-10-21", 23) and got["x2"] == ("2025-11-05", 0)
    assert got["p1"] == ("2025-10-22", 0) and got["p2"] == ("2025-10-28", 23)
    assert got["a1"] == ("2025-10-29", 0) and got["a2"] == ("2025-10-31", 23)
    assert got["d1"] == ("2025-11-02", 0) and got["d2"] == ("2025-11-02", 1) and got["d3"] == ("2025-11-02", 1)
    assert got["d4"] == ("2025-11-02", 12) and got["d5"] == ("2025-11-02", 23)
    assert got["e1"] == ("2025-11-03", 0) and got["e5"] == ("2025-11-03", 23) and got["f1"] == ("2025-11-04", 23)


def test_period_boundaries_at_local_midnight_and_dst(db):
    doc = compute(db, period_fixture())
    rows = rows_of(doc)
    assert doc["window"] == {"from": "2025-10-08", "to": "2025-11-04"}
    assert counts(rows, "live_calls", "7d", ("all", "inbound", "outbound")) == {"all": 13, "inbound": 13, "outbound": 0}
    assert counts(rows, "live_calls", "prev7d", ("all", "inbound", "outbound")) == {"all": 2, "inbound": 2, "outbound": 0}
    # small weekly counts are kept (headline numbers are never dropped for n under 5)
    assert rows[("live_calls", "prev7d", "all")] == (2, 2) and rows[("live_calls", "prev7d", "outbound")] == (0, 0)
    # per-day cells: only days with 5 or more calls survive. Nov 2 (25 hours long) and Nov 3 have exactly 5
    daily = {k[1]: v for k, v in rows.items() if k[0] == "live_daily_inbound"}
    assert daily == {"2025-11-02": (5, 5), "2025-11-03": (5, 5)}


def test_period_of_and_window_pure_functions():
    from datetime import date
    today = date(2025, 11, 5)
    assert live.period_of(date(2025, 11, 5), today) is None
    assert live.period_of(date(2025, 11, 4), today) == "7d"
    assert live.period_of(date(2025, 10, 29), today) == "7d"
    assert live.period_of(date(2025, 10, 28), today) == "prev7d"
    assert live.period_of(date(2025, 10, 22), today) == "prev7d"
    assert live.period_of(date(2025, 10, 21), today) is None
    # the "today" that counts is the Vancouver day, not the UTC day
    assert live.local_today(datetime(2025, 11, 5, 7, 59, 59, tzinfo=UTC)) == date(2025, 11, 4)
    assert live.local_today(datetime(2025, 11, 5, 8, 0, 0, tzinfo=UTC)) == date(2025, 11, 5)
    assert live.local_today(datetime(2025, 10, 31, 6, 59, 59, tzinfo=UTC)) == date(2025, 10, 30)
    assert live.local_today(datetime(2025, 10, 31, 7, 0, 0, tzinfo=UTC)) == date(2025, 10, 31)


def test_shifting_generated_at_across_local_midnight_moves_the_periods(db):
    insert(db, [rec("a", datetime(2025, 11, 5, 9, 0, 0, tzinfo=UTC)), rec("b", datetime(2025, 11, 4, 20, 0, 0, tzinfo=UTC))])
    before = rows_of(live.compute(db, datetime(2025, 11, 5, 7, 59, 0, tzinfo=UTC)))     # still Nov 4 locally
    after = rows_of(live.compute(db, datetime(2025, 11, 5, 8, 1, 0, tzinfo=UTC)))       # now Nov 5 locally
    assert before[("live_calls", "7d", "all")][0] == 0   # Nov 4 is "today", Nov 3 and earlier hold nothing
    assert after[("live_calls", "7d", "all")][0] == 1    # Nov 4 12:00 is now yesterday; Nov 5 01:00 is today


# ------------------------------------------------------------------ after hours
def test_after_hours_definition_grid():
    for wd in range(5):                       # Monday to Friday
        for h in range(24):
            assert live.is_after_hours(wd, h) == (h < 8 or h >= 18), (wd, h)
    for h in range(24):                       # Saturday
        assert live.is_after_hours(5, h) == (h < 9 or h >= 14), h
        assert live.is_after_hours(6, h) is True   # Sunday: all day


def after_hours_fixture():
    return [
        # Monday 2025-11-03 (PST)
        rec("m1", loc(2025, 11, 3, 7, 59), who=1), rec("m2", loc(2025, 11, 3, 8, 0), who=2),
        rec("m3", loc(2025, 11, 3, 17, 59, 59), who=3), rec("m4", loc(2025, 11, 3, 18, 0), who=4),
        rec("m5", loc(2025, 11, 3, 12, 0), result="Voicemail", who=5),           # business hours voicemail
        # Friday 2025-10-31 (PDT)
        rec("f1", loc(2025, 10, 31, 7, 59, 59), who=6), rec("f2", loc(2025, 10, 31, 8, 0), who=7),
        # Saturday 2025-11-01 (PDT)
        rec("s1", loc(2025, 11, 1, 8, 59), who=8), rec("s2", loc(2025, 11, 1, 9, 0), who=9),
        rec("s3", loc(2025, 11, 1, 13, 59), who=10), rec("s4", loc(2025, 11, 1, 14, 0), who=11),
        rec("s5", loc(2025, 11, 1, 14, 30), result="Voicemail", who=12),
        # Sunday 2025-11-02 (the DST change day): all after hours; outbound is never counted
        rec("u1", loc(2025, 11, 2, 12, 0), who=13), rec("u2", loc(2025, 11, 2, 12, 5), result="Voicemail", who=14),
        rec("u3", loc(2025, 11, 2, 12, 10), "Outbound", who=15),
        # today (not in a period) and the previous week (one Sunday call)
        rec("t1", loc(2025, 11, 5, 3, 0), who=16), rec("p1", loc(2025, 10, 26, 10, 0), who=17),
    ]


def test_after_hours_counts_by_weekday_saturday_sunday(db):
    rows = rows_of(compute(db, after_hours_fixture()))
    # 7d after hours inbound: m1, m4, f1, s1, s4, s5(vm), u1, u2(vm) = 8, voicemail among them: s5, u2
    assert counts(rows, "live_after_hours", "7d", ("inbound", "voicemail")) == {"inbound": 8, "voicemail": 2}
    assert counts(rows, "live_after_hours", "prev7d", ("inbound", "voicemail")) == {"inbound": 1, "voicemail": 0}
    assert rows[("live_after_hours", "7d", "inbound")] == (8, 8)


# ------------------------------------------------------------------ hourly averages over 28 days, small cells
def hourly_fixture():
    recs = []
    day = datetime(2025, 10, 8).date()
    weekdays = []
    for i in range(28):                        # 2025-10-08 (Wed) to 2025-11-04 (Tue)
        d = day + timedelta(days=i)
        if d.weekday() <= 4:
            weekdays.append(d)
    assert len(weekdays) == 20
    n = 0
    for i, d in enumerate(weekdays):
        # hour 9: two calls every weekday = 40 calls, the first 10 days' first call is missed
        recs.append(rec(f"h9a{n}", loc(d.year, d.month, d.day, 9, 10), result="Missed" if i < 10 else "Accepted"))
        recs.append(rec(f"h9b{n}", loc(d.year, d.month, d.day, 9, 40)))
        # hour 14: 30 calls (1 or 2 a day); a voicemail counts as unanswered
        recs.append(rec(f"h14a{n}", loc(d.year, d.month, d.day, 14, 5), result="Voicemail" if i < 5 else "Accepted"))
        if i < 10:
            recs.append(rec(f"h14b{n}", loc(d.year, d.month, d.day, 14, 35)))
        n += 1
    for i, d in enumerate(weekdays[:5]):       # hour 10: only 4 calls (dropped), hour 11: exactly 5 (kept)
        if i < 4:
            recs.append(rec(f"h10{i}", loc(d.year, d.month, d.day, 10, 0)))
        recs.append(rec(f"h11{i}", loc(d.year, d.month, d.day, 11, 0), result="Hang Up"))
    # noise that must not count: weekend calls, hours 7 and 18, outbound, older than 28 days, today
    for k in range(6):
        recs.append(rec(f"w{k}", loc(2025, 10, 11, 9, 5 + k)))                       # Saturday 9 am
        recs.append(rec(f"v{k}", loc(2025, 10, 15, 7, 30 + k)))                      # hour 7
        recs.append(rec(f"z{k}", loc(2025, 10, 15, 18, k)))                          # hour 18
        recs.append(rec(f"o{k}", loc(2025, 10, 15, 9, 50 + k), "Outbound", who=k))   # outbound
        recs.append(rec(f"q{k}", loc(2025, 10, 7, 9, k)))                            # Oct 7, outside the 28 days
        recs.append(rec(f"t{k}", loc(2025, 11, 5, 9, k)))                            # today
    return recs


def test_hourly_averages_over_28_days_and_small_cells(db):
    rows = rows_of(compute(db, hourly_fixture()))
    byh = {k[1]: v for k, v in rows.items() if k[0] == "live_inbound_by_hour"}
    # 20 weekdays in the 28 day window. hour 9: 40 calls = 2.0 a weekday; hour 14: 30 = 1.5; hour 11: 5 = 0.25
    assert byh == {"9": (2.0, 40), "11": (0.25, 5), "14": (1.5, 30)}     # hour 10 (n=4) and empty hours are gone
    missed = {k[1]: v for k, v in rows.items() if k[0] == "live_missed_by_hour"}
    # unanswered: hour 9 -> 10 missed, hour 11 -> 5 hang ups, hour 14 -> 5 voicemails (n=5 kept)
    assert missed == {"9": (0.5, 10), "11": (0.25, 5), "14": (0.25, 5)}
    busiest = [(k, v) for k, v in rows.items() if k[0] == "live_busiest_hour"]
    assert busiest == [(("live_busiest_hour", "9", None), (2.0, 40))]


def test_busiest_hour_ties_go_to_the_earlier_hour_and_tiny_counts_are_dropped(db):
    d = [loc(2025, 10, 8 + i, 15, 0) for i in range(5)]      # 5 calls at 15:00 (Wed 8 to Sun 12 includes weekend)
    recs = [rec(f"x{i}", t) for i, t in enumerate(d)]
    recs += [rec(f"y{i}", loc(2025, 10, 13 + i, 12, 0)) for i in range(5)]   # Mon 13 to Fri 17 at 12:00
    recs += [rec(f"z{i}", loc(2025, 10, 13 + i, 16, 0)) for i in range(5)]   # same count at 16:00
    rows = rows_of(compute(db, recs))
    bh = [(k[1], v) for k, v in rows.items() if k[0] == "live_busiest_hour"]
    assert bh == [("12", (0.25, 5))]
    assert ("live_inbound_by_hour", "15", None) not in rows         # only 3 weekday calls: under 5, dropped


def test_empty_database_gives_no_rows(db):
    doc = live.compute(db, NOW)
    assert doc["rows"] == [] and doc["window"] == {"from": "2025-10-08", "to": "2025-11-04"}


# ------------------------------------------------------------------ past follow-up share (labels table)
def add_labeled_calls(conn, specs, labeler="rules-v1"):
    for i, (promised, private) in enumerate(specs):
        ref = f"FAKEREF{labeler[:3]}{i}"
        conn.execute("INSERT INTO calls(call_id, call_ref) VALUES (?,?)", (f"FAKEID{labeler[:3]}{i}", ref))
        conn.execute("INSERT INTO labels(call_ref, labeler, json, created_at) VALUES (?,?,?,?)",
                     (ref, labeler, json.dumps({"promised_followup": promised, "contains_private_content": private}),
                      "2025-10-27T00:00:00Z"))
    conn.commit()


def test_past_followup_share_counts_non_private_labeled_calls(db):
    add_labeled_calls(db, [(True, False)] * 6 + [(False, False)] * 4 + [(True, True)] * 3)   # private ones leave out
    db.execute("INSERT INTO calls(call_id, call_ref) VALUES ('FAKEIDnolabel', 'FAKEREFnolabel')")
    insert(db, [rec("a", B)])
    assert live.past_followup(db) == (0.6, 10)
    rows = rows_of(live.compute(db, NOW))
    assert rows[("live_past_followup_share", "past_recordings", None)] == (0.6, 10)


def test_past_followup_share_gold_wins_and_other_labelers_are_ignored(db):
    add_labeled_calls(db, [(True, False)] * 5 + [(False, False)] * 5)
    add_labeled_calls(db, [(True, False)] * 9, labeler="llm-x")
    db.execute("INSERT INTO gold(call_ref, json, created_at) VALUES (?,?,?)",
               ("FAKEREFrul5", json.dumps({"promised_followup": True, "contains_private_content": False}), "x"))
    db.commit()
    assert live.past_followup(db) == (0.6, 10)


def test_past_followup_share_small_cells_are_dropped(db):
    add_labeled_calls(db, [(True, False)] * 4 + [(False, False)] * 20)      # numerator 4: under 5
    assert live.past_followup(db) is None
    insert(db, [rec("a", B)])
    assert not [k for k in rows_of(live.compute(db, NOW)) if k[0] == "live_past_followup_share"]


def test_past_followup_share_absent_without_labels(db):
    insert(db, [rec("a", B)])
    assert live.past_followup(db) is None


# ------------------------------------------------------------------ validation and writing
def good_doc():
    return {"v": 1, "generated_at": "2025-11-05T14:20:00Z", "timezone": "America/Vancouver",
            "window": {"from": "2025-10-08", "to": "2025-11-04"},
            "rows": [{"metric": "live_calls", "dim1": "7d", "dim2": "all", "value": 3, "n": 3}]}


@pytest.mark.parametrize("mutate", [
    lambda d: d["rows"].__setitem__(0, {**d["rows"][0], "metric": "bad metric"}),
    lambda d: d["rows"].__setitem__(0, {**d["rows"][0], "dim1": "x" * 41}),
    lambda d: d["rows"].__setitem__(0, {**d["rows"][0], "dim2": "a/b"}),
    lambda d: d["rows"].__setitem__(0, {**d["rows"][0], "dim1": 7}),
    lambda d: d["rows"].__setitem__(0, {**d["rows"][0], "value": float("nan")}),
    lambda d: d["rows"].__setitem__(0, {**d["rows"][0], "value": True}),
    lambda d: d["rows"].__setitem__(0, {**d["rows"][0], "value": "3"}),
    lambda d: d["rows"].__setitem__(0, {**d["rows"][0], "n": -1}),
    lambda d: d["rows"].__setitem__(0, {**d["rows"][0], "n": 1.5}),
    lambda d: d["rows"].__setitem__(0, {**d["rows"][0], "extra": 1}),
    lambda d: d["rows"].__setitem__(0, {"metric": "m", "dim1": None, "dim2": None, "value": 1}),
    lambda d: d.__setitem__("rows", [d["rows"][0]] * 2001),
    lambda d: d.__setitem__("rows", "nope"),
])
def test_validate_rejects(mutate):
    d = good_doc()
    mutate(d)
    with pytest.raises(ValueError):
        sync.validate(d)


def test_validate_accepts_limits():
    d = good_doc()
    d["rows"] = [{"metric": "m" * 40, "dim1": "A-z_0.9:", "dim2": None, "value": 0.5, "n": 0}] * 2000
    sync.validate(d)


def test_write_analytics_is_atomic_mode_660_and_leaves_no_temp(tmp_path):
    bridge = tmp_path / "bridge"
    bridge.mkdir()
    old = bridge / "analytics.json"
    old.write_text("OLD")
    sync.write_analytics(str(bridge), good_doc())
    assert json.loads(old.read_text())["v"] == 1
    assert stat.S_IMODE(os.stat(old).st_mode) == 0o660
    assert os.listdir(bridge) == ["analytics.json"]


def test_write_failure_keeps_old_file_and_cleans_temp(tmp_path, monkeypatch):
    bridge = tmp_path / "bridge"
    bridge.mkdir()
    (bridge / "analytics.json").write_text("OLD")
    monkeypatch.setattr(os, "replace", lambda a, b: (_ for _ in ()).throw(OSError("boom")))
    with pytest.raises(OSError):
        sync.write_analytics(str(bridge), good_doc())
    monkeypatch.undo()
    assert (bridge / "analytics.json").read_text() == "OLD" and os.listdir(bridge) == ["analytics.json"]


def test_full_document_obeys_the_limits(db):
    insert(db, followup_fixture() + hourly_fixture() + after_hours_fixture() + period_fixture())
    doc = sync.build_document(db, NOW)
    assert doc["v"] == 1 and doc["timezone"] == "America/Vancouver" and doc["generated_at"] == "2025-11-05T14:20:00Z"
    assert 0 < len(doc["rows"]) <= 2000 and len(sync.encode(doc)) <= 256 * 1024
    assert set(doc) == {"v", "generated_at", "timezone", "window", "rows"}
    metrics = {r["metric"] for r in doc["rows"]}
    assert metrics == {"live_calls", "live_inbound_result", "live_missed_followup", "live_after_hours",
                       "live_inbound_by_hour", "live_missed_by_hour", "live_busiest_hour", "live_daily_inbound"}
    for r in doc["rows"]:
        assert set(r) == {"metric", "dim1", "dim2", "value", "n"}
        assert isinstance(r["n"], int) and math.isfinite(r["value"])
    # every dim has the expected shape per metric
    shapes = {"live_calls": (r"^(7d|prev7d)$", r"^(all|inbound|outbound)$"),
              "live_inbound_result": (r"^(7d|prev7d)$", r"^(answered|missed|voicemail|hangup|other)$"),
              "live_missed_followup": (r"^(7d|prev7d)$", "^(%s)$" % "|".join(live.FOLLOWUP_BUCKETS)),
              "live_after_hours": (r"^(7d|prev7d)$", r"^(inbound|voicemail)$"),
              "live_inbound_by_hour": (r"^(8|9|1[0-7])$", None), "live_missed_by_hour": (r"^(8|9|1[0-7])$", None),
              "live_busiest_hour": (r"^(8|9|1[0-7])$", None),
              "live_daily_inbound": (r"^\d{4}-\d{2}-\d{2}$", None)}
    for r in doc["rows"]:
        d1, d2 = shapes[r["metric"]]
        assert re.match(d1, r["dim1"])
        assert (r["dim2"] is None) if d2 is None else re.match(d2, r["dim2"])


def test_oversized_document_is_refused(db, monkeypatch):
    insert(db, followup_fixture())
    monkeypatch.setattr(sync, "MAX_BYTES", 100)
    with pytest.raises(ValueError):
        sync.build_document(db, NOW)


def test_compute_is_idempotent(db):
    insert(db, followup_fixture() + hourly_fixture())
    a = sync.encode(sync.build_document(db, NOW))
    insert(db, followup_fixture() + hourly_fixture())     # same calls loaded again: upsert, no duplicates
    assert sync.encode(sync.build_document(db, NOW)) == a


# ------------------------------------------------------------------ worker and CLI, mocked HTTP
class FakeTelus:
    def __init__(self, records, scope=GOOD_SCOPE, status=200):
        self.records, self.scope, self.status = records, scope, status
        self.log: list[tuple[str, str, str]] = []
        self.logins = 0
        self.token = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.log.append((request.method, request.url.host, request.url.path))
        if request.method == "POST":
            self.logins += 1
            self.token = f"FAKE_ACCESS_TOKEN_{self.logins}"
            return httpx.Response(200, json={"access_token": self.token, "expires_in": 3600, "scope": self.scope,
                                             "refresh_token": "FAKE_REFRESH_TOKEN"})
        if request.headers.get("authorization") != f"Bearer {self.token}":
            return httpx.Response(401, json={"error": "token_invalid"})
        if request.url.path == endpoints.CALL_LOG_PATH:
            if self.status != 200:
                return httpx.Response(self.status, headers={"retry-after": "1"}, json={"message": "FAKE_JWT leak?"})
            return httpx.Response(200, json={"records": self.records, "paging": {"page": 1, "totalPages": 1},
                                             "navigation": {}})
        return httpx.Response(404)

    def transport(self):
        return httpx.MockTransport(self.handler)


@pytest.fixture(autouse=True)
def _restore_write_status():
    real = worker.write_status
    yield
    worker.write_status = real


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "walnut"
    root.mkdir()
    tc.write_credentials(str(root), {"TELUS_ENV": "sandbox", "TELUS_SERVER_URL": endpoints.SERVER_URLS["sandbox"],
                                     "TELUS_CLIENT_ID": FAKE_ID, "TELUS_CLIENT_SECRET": FAKE_SECRET,
                                     "TELUS_JWT": FAKE_JWT})
    bridge = tmp_path / "bridge"
    (bridge / "requests").mkdir(parents=True)
    return SimpleNamespace(root=str(root), bridge=str(bridge), tmp=tmp_path, sleeps=[], writes=[])


def write_req(e, job_id=HEX_A, mode="sync", days=None, max_items=None, **over):
    req = {"v": 1, "id": job_id, "mode": mode, "days": days, "max_items": max_items,
           "requested_by": "Fake Admin", "requested_at": "2025-11-05T14:19:00Z"}
    req.update(over)
    with open(os.path.join(e.bridge, "requests", f"{job_id}.json"), "w") as f:
        json.dump(req, f)


def make_worker(e, rc):
    real_write = worker.write_status

    def spy(bridge, status):
        e.writes.append(json.dumps(status))
        real_write(bridge, status)
    worker.write_status = spy
    return worker.Worker(e.root, e.bridge, os.path.join(e.root, "private.db"), os.path.join(e.root, "salt.bin"),
                         sleep=e.sleeps.append, now=lambda: NOW, file_sleep=0,
                         client_factory=lambda c, sleep: ReadOnlyClient(c, transport=rc.transport(), sleep=sleep))


def run_worker(e, rc):
    code = make_worker(e, rc).run()
    return code, read_json(e, "status.json")


def read_json(e, name):
    with open(os.path.join(e.bridge, name)) as f:
        return json.load(f)


def db_rows(e, sql):
    conn = sqlite3.connect(os.path.join(e.root, "private.db"))
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql)]
    finally:
        conn.close()


PHONE_LIKE = re.compile(r"(?<!\d)(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]?\d{3}[ .-]\d{4}(?!\d)|\+\d{10,}|\(\d{3}\)")
DIGIT_RUN = re.compile(r"\d{7,}")
LONG_HEX = re.compile(r"[0-9a-fA-F]{16,}")


def assert_clean(e, texts=None):
    """analytics.json and every status document: no phone-like strings, no hash-like strings, no ids."""
    texts = list(texts or []) + [open(os.path.join(e.bridge, n)).read() for n in ("analytics.json", "status.json")
                                 if os.path.exists(os.path.join(e.bridge, n))]
    texts += e.writes
    db_path = os.path.join(e.root, "private.db")
    hashes = set()
    if os.path.exists(db_path):
        for r in db_rows(e, "SELECT from_hash, to_hash FROM telus_calls"):
            hashes |= {h for h in r.values() if h}
    analytics = os.path.join(e.bridge, "analytics.json")
    if os.path.exists(analytics):
        assert not DIGIT_RUN.search(open(analytics).read())     # no long digit runs at all (status has byte counts)
    for t in texts:
        assert not PHONE_LIKE.search(t), PHONE_LIKE.search(t).group()
        assert not LONG_HEX.search(t.replace(HEX_A, "").replace(HEX_B, "")) , LONG_HEX.search(t).group()
        for h in hashes:
            assert h not in t
        for needle in ["FAKECALL", "FAKEREC", "FAKE_", "604555", "Traceback", "http", "ringcentral", "Error",
                       *SECRET_WORDS]:
            assert needle not in t, needle


def test_worker_sync_mode_end_to_end(env):
    rc = FakeTelus(followup_fixture() + hourly_fixture())
    write_req(env)
    code, st = run_worker(env, rc)
    assert code == 0
    doc = read_json(env, "analytics.json")
    assert stat.S_IMODE(os.stat(os.path.join(env.bridge, "analytics.json")).st_mode) == 0o660
    assert sorted(os.listdir(env.bridge)) == ["analytics.json", "requests", "status.json"]   # no temp files
    assert os.listdir(os.path.join(env.bridge, "requests")) == []
    rows = rows_of(doc)
    assert counts(rows, "live_missed_followup", "7d", live.FOLLOWUP_BUCKETS)["called_back_4h"] == 2
    assert rows[("live_inbound_by_hour", "9", None)] == (2.0, 40)
    # status: job done, connection ok, the new sync object has fixed fields only
    assert st["job"]["mode"] == "sync" and st["job"]["state"] == "done" and st["job"]["code"] == "ok"
    assert st["connection"]["state"] == "ok"
    assert st["sync"] == {"state": "ok", "code": "ok", "finished_at": "2025-11-05T14:20:00Z",
                          "rows": len(doc["rows"])}
    assert set(st) == {"v", "updated_at", "connection", "job", "storage", "rejected_requests", "sync"}
    assert set(st["sync"]) == {"state", "code", "finished_at", "rows"}
    # GET call-log only (plus the login POST)
    assert {(m, p) for m, _, p in rc.log} == {("POST", endpoints.TOKEN_PATH), ("GET", endpoints.CALL_LOG_PATH)}
    assert_clean(env)


def test_sync_request_accepts_optional_days_and_max_items(env):
    rc = FakeTelus([rec("a", B, result="Missed", who=1)])
    write_req(env, days=7, max_items=5)
    write_req(env, job_id=HEX_B, days=None, max_items=None)
    code, st = run_worker(env, rc)
    assert code == 0 and st["rejected_requests"] == 0 and st["sync"]["state"] == "ok"
    write_req(env, days=31)                       # still out of range, rejected like any other mode
    _, st = run_worker(env, rc)
    assert st["rejected_requests"] == 1
    write_req(env, mode="bogus")
    _, st = run_worker(env, rc)
    assert st["rejected_requests"] == 2


def test_rerun_is_idempotent_and_cursor_is_untouched(env):
    conn = dbm.connect(os.path.join(env.root, "private.db"), create=True)
    conn.execute("INSERT INTO telus_sync_state(key, value) VALUES ('synced_through', '2025-09-01T00:00:00Z')")
    conn.commit()
    conn.close()
    rc = FakeTelus(followup_fixture())
    write_req(env)
    run_worker(env, rc)
    first = open(os.path.join(env.bridge, "analytics.json"), "rb").read()
    n1 = db_rows(env, "SELECT COUNT(*) AS n FROM telus_calls")[0]["n"]
    write_req(env, job_id=HEX_B)
    run_worker(env, rc)
    assert open(os.path.join(env.bridge, "analytics.json"), "rb").read() == first
    assert db_rows(env, "SELECT COUNT(*) AS n FROM telus_calls")[0]["n"] == n1 == len(followup_fixture())
    assert db_rows(env, "SELECT key, value FROM telus_sync_state") == [
        {"key": "synced_through", "value": "2025-09-01T00:00:00Z"}]
    assert_clean(env)


def test_sync_failure_codes_keep_the_old_file(env):
    old = '{"v":1,"old":true}'
    with open(os.path.join(env.bridge, "analytics.json"), "w") as f:
        f.write(old)
    write_req(env)
    _, st = run_worker(env, FakeTelus([rec("a", B)], scope=GOOD_SCOPE + " SMS"))
    assert st["sync"] == {"state": "failed", "code": "write_scope_present", "finished_at": "2025-11-05T14:20:00Z",
                          "rows": 0}
    assert st["job"]["state"] == "failed" and st["job"]["code"] == "write_scope_present"
    assert st["connection"]["write_capable_scopes"] is True
    assert open(os.path.join(env.bridge, "analytics.json")).read() == old
    assert not os.path.exists(os.path.join(env.root, "private.db"))        # refused before anything was pulled

    write_req(env, job_id=HEX_B)
    _, st = run_worker(env, FakeTelus([rec("a", B)], scope="ReadCallLog"))   # required scopes missing
    assert st["sync"]["code"] == "error" and open(os.path.join(env.bridge, "analytics.json")).read() == old

    write_req(env)
    _, st = run_worker(env, FakeTelus([], status=429))
    assert st["sync"]["state"] == "failed" and st["sync"]["code"] == "rate_limited"
    assert open(os.path.join(env.bridge, "analytics.json")).read() == old
    assert_clean(env)


def test_sync_not_configured_and_failed_run_keeps_previous_row_count(env):
    write_req(env)
    run_worker(env, FakeTelus([rec("a", B, result="Missed", who=1)]))
    rows = read_json(env, "status.json")["sync"]["rows"]
    assert rows > 0
    os.unlink(os.path.join(env.root, "telus", "credentials.env"))
    write_req(env, job_id=HEX_B)
    _, st = run_worker(env, FakeTelus([]))
    assert st["sync"]["code"] == "not_configured" and st["sync"]["state"] == "failed" and st["sync"]["rows"] == rows


def test_unexpected_exception_text_never_reaches_status_or_logs(env, monkeypatch, caplog):
    def boom(conn, now, min_cell=5):
        raise RuntimeError("secret text 6045550142 FAKECALL1")
    monkeypatch.setattr(live, "compute", boom)
    write_req(env)
    with caplog.at_level(logging.DEBUG):
        _, st = run_worker(env, FakeTelus([rec("a", B)]))
    assert st["sync"]["code"] == "error" and st["job"]["state"] == "failed"
    assert not os.path.exists(os.path.join(env.bridge, "analytics.json"))
    assert "6045550142" not in caplog.text and "FAKECALL" not in caplog.text and "RuntimeError" in caplog.text
    assert_clean(env)


def test_oversized_output_is_refused_without_writing(env, monkeypatch):
    monkeypatch.setattr(sync, "MAX_ROWS", 3)
    write_req(env)
    _, st = run_worker(env, FakeTelus(followup_fixture()))
    assert st["sync"]["code"] == "error" and not os.path.exists(os.path.join(env.bridge, "analytics.json"))


def patch_http(monkeypatch, rc):
    real = httpx.Client

    class Patched(real):
        def __init__(self, *a, **kw):
            kw["transport"] = rc.transport()
            super().__init__(*a, **kw)
    monkeypatch.setattr(httpx, "Client", Patched)

    class FrozenDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW if tz else NOW.replace(tzinfo=None)
    monkeypatch.setattr(worker, "datetime", FrozenDT)
    monkeypatch.setattr(worker.time, "sleep", lambda s: None)


def test_cli_telus_sync_end_to_end(env, monkeypatch, capsys):
    rc = FakeTelus(followup_fixture() + after_hours_fixture())
    patch_http(monkeypatch, rc)
    assert cli.main(["telus-sync", "--root", env.root, "--bridge", env.bridge, "--days", "40"]) == 0
    out = capsys.readouterr()
    assert out.out.startswith("telus-sync code=ok rows=") and "FAKE" not in out.out + out.err
    st = read_json(env, "status.json")
    assert st["sync"]["state"] == "ok" and st["sync"]["rows"] == len(read_json(env, "analytics.json")["rows"])
    assert st["job"]["state"] == "idle" and st["job"]["mode"] is None          # the request queue is untouched
    assert db_rows(env, "SELECT * FROM telus_sync_state") == []
    # the window sent to TELUS covers `--days` days back
    assert_clean(env)
    # bad arguments and a missing bridge
    assert cli.main(["telus-sync", "--root", env.root, "--bridge", env.bridge, "--days", "0"]) == 2
    assert cli.main(["telus-sync", "--root", env.root, "--bridge", env.bridge, "--days", "91"]) == 2
    assert cli.main(["telus-sync", "--root", env.root, "--bridge", str(env.tmp / "nope")]) == 2
    capsys.readouterr()


def test_cli_telus_sync_refuses_write_scope(env, monkeypatch, capsys):
    patch_http(monkeypatch, FakeTelus([rec("a", B)], scope=GOOD_SCOPE + " EditAccounts"))
    assert cli.main(["telus-sync", "--root", env.root, "--bridge", env.bridge]) == 1
    out = capsys.readouterr()
    assert "code=write_scope_present" in out.out
    assert not os.path.exists(os.path.join(env.bridge, "analytics.json"))
    assert read_json(env, "status.json")["sync"]["code"] == "write_scope_present"


def test_cli_telus_sync_busy_when_another_worker_holds_the_lock(env, monkeypatch, capsys):
    patch_http(monkeypatch, FakeTelus([]))
    w = make_worker(env, FakeTelus([]))
    lock = w._lock()
    try:
        assert cli.main(["telus-sync", "--root", env.root, "--bridge", env.bridge]) == 1
    finally:
        os.close(lock)
    assert "busy" in capsys.readouterr().out


def test_existing_status_without_sync_key_is_still_valid(env):
    """Old status files (no sync object) load fine, and a recordings/check status has no sync key."""
    write_req(env, mode="check")
    _, st = run_worker(env, FakeTelus([rec("a", B)]))
    assert "sync" not in st and st["job"]["mode"] == "check"


def test_the_leak_scan_itself_catches_what_it_should():
    for bad in ("+1 (604) 555-0142", "604-555-0142", "604.555.0142", "1-604-555-0142", "(604) 555"):
        assert PHONE_LIKE.search(bad), bad
    for fine in ("2025-11-05", "2025-11-05T14:20:00Z", "live_calls", '"n":40,"value":2.0'):
        assert not PHONE_LIKE.search(fine), fine
    assert LONG_HEX.search("a3" * 32) and not LONG_HEX.search("rang_again_1h")
    assert DIGIT_RUN.search("6045550142") and not DIGIT_RUN.search("2025-11-05")
