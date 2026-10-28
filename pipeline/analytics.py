"""Aggregate-only analytics for the web app. No text, no numbers, no call ids."""
from __future__ import annotations

import json
import os
from collections import Counter
from datetime import date, timedelta

from . import db as dbm
from .rules import LABELER_ID

WD = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
MIN_CELL = 5
SOURCE = "derived_from_real_calls"


def is_after_hours(weekday: int, hour: int) -> bool:
    if weekday <= 4:
        return hour < 8 or hour >= 18
    if weekday == 5:
        return hour >= 14
    return True


def build(conn, labeler: str, min_cell: int = MIN_CELL) -> dict:
    labels = dbm.chosen_labels(conn, labeler)
    calls = conn.execute("SELECT call_ref, date, hour, weekday, ext FROM calls").fetchall()
    total_calls = len(calls)
    dates = sorted({c["date"] for c in calls if c["date"]})
    dr = [dates[0], dates[-1]] if dates else [None, None]
    days_per_wd = Counter()
    if dates:
        cur, end = date.fromisoformat(dates[0]), date.fromisoformat(dates[-1])
        while cur <= end:
            days_per_wd[cur.weekday()] += 1
            cur += timedelta(days=1)

    rows_in = []  # (call, label)
    private = unlabeled = 0
    for c in calls:
        got = labels.get(c["call_ref"])
        if not got:
            unlabeled += 1
            continue
        lab = json.loads(got[1])
        if lab.get("contains_private_content"):
            private += 1
            continue
        rows_in.append((c, lab))
    N = len(rows_in)

    out: list[dict] = []

    def add(metric, dim1, dim2, value, n, suppress_on=None):
        cell = n if suppress_on is None else suppress_on
        sup = cell is None or cell < min_cell
        row = {"source": SOURCE, "metric": metric, "dim1": dim1, "dim2": dim2,
               "value": None if sup else value, "n": None if sup else n, "suppressed": sup}
        out.append(row)

    hw = Counter((c["weekday"], c["hour"]) for c, _ in rows_in)
    for wd in range(7):
        for h in range(24):
            n = hw[(wd, h)]
            add("calls_total_by_weekday_hour", WD[wd], f"{h:02d}", n, n)
            avg = n / days_per_wd[wd] if days_per_wd[wd] else None
            add("calls_avg_per_day_by_weekday_hour", WD[wd], f"{h:02d}",
                None if avg is None else round(avg, 3), n)

    for ext, n in sorted(Counter(c["ext"] for c, _ in rows_in).items()):
        add("calls_by_extension", ext, None, n, n)
    for key, metric in (("caller_type", "caller_type"), ("intent", "intent")):
        cnt = Counter(l[key] for _, l in rows_in)
        for k, n in sorted(cnt.items()):
            add(f"calls_by_{metric}", k, None, n, n)
            add(f"share_by_{metric}", k, None, round(n / N, 4) if N else None, n)

    def rate(metric, num, den=N):
        sup_cell = min(num, den)
        add(metric, None, None, round(num / den, 4) if den else None, den, suppress_on=sup_cell)

    for oc in ("hold_then_dropped", "no_answer", "voicemail_left"):
        rate(f"outcome_rate_{oc}", sum(1 for _, l in rows_in if l["outcome"] == oc))
    rate("callback_promised_rate", sum(1 for _, l in rows_in if l["promised_followup"]))

    ah = sum(1 for c, _ in rows_in if is_after_hours(c["weekday"], c["hour"]))
    we = sum(1 for c, _ in rows_in if c["weekday"] >= 5)
    add("after_hours_calls", None, None, ah, ah)
    rate("after_hours_share", ah)
    add("weekend_calls", None, None, we, we)
    rate("weekend_share", we)

    by_hour = Counter(c["hour"] for c, _ in rows_in)
    top = sorted(by_hour.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
    for i, (h, n) in enumerate(top, 1):
        add("peak_hours_top5", f"{h:02d}", f"rank_{i}", n, n)

    weekdays_in_range = sum(days_per_wd[i] for i in range(5))
    inb = Counter(c["hour"] for c, l in rows_in
                  if c["weekday"] <= 4 and l["real_direction"] == "inbound" and l["caller_type"] == "client")
    for h in range(8, 18):
        n = inb[h]
        add("avg_weekday_inbound_client_calls_per_hour", f"{h:02d}", None,
            round(n / weekdays_in_range, 3) if weekdays_in_range else None, n)

    add("private_or_personal_total", None, None, private, private)

    meta = {
        "generated_at": dbm.now_iso(),
        "date_range": dr,
        "total_calls": total_calls,
        "labeler": labeler + "+gold",
        "calls_in_mix": N,
        "unlabeled_excluded": unlabeled,
        "min_cell_size": min_cell,
    }
    return {"metadata": meta, "rows": out}


def run(paths, labeler: str = LABELER_ID, out_path: str | None = None) -> int:
    conn = dbm.connect(paths.db)
    res = build(conn, labeler)
    conn.close()
    out_path = out_path or os.path.join(paths.out_dir, "analytics.json")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), mode=0o700, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(res, f, indent=2)
    sup = sum(1 for r in res["rows"] if r["suppressed"])
    print(f"analytics rows={len(res['rows'])} suppressed={sup} calls_in_mix={res['metadata']['calls_in_mix']}")
    return 0
