"""Counts-only comparison of a transcripts CSV against the TELUS call log in the private store.

    P="sudo -u walnut-pipeline /opt/walnut-desk/pipeline/.venv/bin/python -m pipeline"
    $P telus-match --csv /tmp/calls.csv

A CSV row matches a TELUS call when the start times are within WINDOW seconds (one TELUS call is used at most once; ties go to the
closer duration). Prints numbers only: no ids, numbers, names or text, so the output is safe to paste.
"""
from __future__ import annotations

import bisect
import csv
from collections import Counter
from datetime import datetime, timedelta

from . import calllog

WINDOW = 180          # seconds between the recording's start and the call log's start
DUR_SLACK = 20        # seconds; only used to report how many matches also agree on length


def _epoch_local(date_s: str, time_s: str) -> float:
    d = datetime.fromisoformat(f"{date_s}T{time_s[:8]}")
    tz = calllog._TZ
    return d.replace(tzinfo=tz).timestamp() if tz else d.timestamp()


def run(db_path: str, csv_path: str) -> int:
    from .. import db as dbm

    csv.field_size_limit(10 ** 9)
    with open(csv_path, encoding="utf-8-sig", newline="") as fh:
        rows = [(r["date"], r["time"], float(r.get("audio_seconds") or 0)) for r in csv.DictReader(fh)]
    conn = dbm.connect(db_path)
    calls = conn.execute("SELECT telus_id, start_local, date, duration, direction, result, has_recording FROM telus_calls").fetchall()
    conn.close()
    print(f"csv_rows={len(rows)} telus_calls_in_store={len(calls)}")
    if not calls:
        print("The TELUS call log has not been pulled yet, so nothing can be compared. Run telus-pull-calllog first.")
        return 0

    tel = sorted(((datetime.fromisoformat(c["start_local"]).timestamp(), c) for c in calls), key=lambda x: x[0])
    keys = [t for t, _ in tel]
    lo, hi = tel[0][1]["date"], tel[-1][1]["date"]
    print(f"telus_window={lo}..{hi}")
    csv_dates = sorted(r[0] for r in rows)
    print(f"csv_window={csv_dates[0]}..{csv_dates[-1]}")

    used: set[str] = set()
    matched = same_len = outside = 0
    diffs: list[float] = []
    direction = Counter()
    result = Counter()
    unmatched_by_month: Counter = Counter()
    matched_ids: set[str] = set()
    for d, t, secs in sorted(rows, key=lambda r: (r[0], r[1])):
        if not (lo <= d <= hi):
            outside += 1
            continue
        ts = _epoch_local(d, t)
        i = bisect.bisect_left(keys, ts - WINDOW)
        best = None
        while i < len(tel) and keys[i] <= ts + WINDOW:
            c = tel[i][1]
            if c["telus_id"] not in used:
                score = (abs(c["duration"] - secs) > DUR_SLACK, abs(keys[i] - ts))
                if best is None or score < best[0]:
                    best = (score, c, keys[i] - ts)
            i += 1
        if best is None:
            unmatched_by_month[d[:7]] += 1
            continue
        _, c, diff = best
        used.add(c["telus_id"])
        matched_ids.add(c["telus_id"])
        matched += 1
        diffs.append(diff)
        same_len += abs(c["duration"] - secs) <= DUR_SLACK
        direction[c["direction"]] += 1
        result[c["result"]] += 1
    inside = len(rows) - outside
    print(f"csv_rows_inside_telus_window={inside} outside_window={outside}")
    print(f"matched={matched} unmatched_inside_window={inside - matched} (matched and also within {DUR_SLACK}s of the same length: {same_len})")
    if diffs:
        diffs.sort()
        print(f"start_time_difference_seconds median={diffs[len(diffs)//2]:+.0f} min={diffs[0]:+.0f} max={diffs[-1]:+.0f}")
    print("unmatched_by_month=" + ", ".join(f"{k}:{v}" for k, v in sorted(unmatched_by_month.items())))
    print("matched_telus_direction=" + ", ".join(f"{k}:{v}" for k, v in direction.most_common()))
    print("matched_telus_result=" + ", ".join(f"{k}:{v}" for k, v in result.most_common()))
    csv_lo, csv_hi = csv_dates[0], csv_dates[-1]
    in_range = [c for _, c in tel if csv_lo <= c["date"] <= csv_hi]
    rec_only = [c for c in in_range if c["has_recording"] and c["telus_id"] not in matched_ids]
    print(f"telus_calls_in_csv_date_range={len(in_range)} with_recording={sum(1 for c in in_range if c['has_recording'])} "
          f"recorded_but_not_in_csv={len(rec_only)}")
    return 0
