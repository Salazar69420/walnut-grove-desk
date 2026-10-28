"""Counts-only data audit. No text, no numbers, no ids."""
from __future__ import annotations

import json
import os
from datetime import date, timedelta

from . import db as dbm

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _grouped(conn, col, order="1"):
    return {str(r[0]) if r[0] is not None else "null": r[1]
            for r in conn.execute(f"SELECT {col}, COUNT(*) FROM calls GROUP BY 1 ORDER BY {order}")}


def compute(conn) -> dict:
    q = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    meta = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM load_meta")}
    total = q("SELECT COUNT(*) FROM calls")
    d = {
        "total_rows": total,
        "rows_in_csv": int(meta.get("rows_read", total)),
        "duplicate_ids": int(meta.get("duplicates_skipped", 0)),
        "unparsed_file_names": int(meta.get("unparsed_file_names", 0)),
        "rows_skipped_bad_timestamp": int(meta.get("bad_timestamp_skipped", 0)),
        "empty_texts": q("SELECT COUNT(*) FROM calls WHERE private_purged=0 AND "
                         "(plain_text IS NULL OR TRIM(plain_text)='')"),
        "purged_rows": q("SELECT COUNT(*) FROM calls WHERE private_purged=1"),
        "missing_number": q("SELECT COUNT(*) FROM calls WHERE has_number=0"),
    }
    buckets = {
        "lt_5s": "seconds < 5",
        "5_to_30s": "seconds >= 5 AND seconds < 30",
        "30s_to_2min": "seconds >= 30 AND seconds < 120",
        "2_to_10min": "seconds >= 120 AND seconds <= 600",
        "gt_10min": "seconds > 600",
        "unknown": "seconds IS NULL",
    }
    d["durations"] = {k: q(f"SELECT COUNT(*) FROM calls WHERE {w}") for k, w in buckets.items()}
    d["durations"]["max_seconds"] = q("SELECT MAX(seconds) FROM calls")
    d["per_extension"] = _grouped(conn, "ext")
    d["per_language"] = _grouped(conn, "COALESCE(language,'unknown')")
    d["per_weekday"] = {WEEKDAYS[int(k)] if k.isdigit() else k: v
                        for k, v in _grouped(conn, "weekday").items()}
    d["per_hour"] = {f"{int(k):02d}": v for k, v in _grouped(conn, "hour").items() if k != "null"}
    lo, hi = q("SELECT MIN(date) FROM calls"), q("SELECT MAX(date) FROM calls")
    d["date_range"] = [lo, hi]
    zero = 0
    if lo and hi:
        have = {r[0] for r in conn.execute("SELECT DISTINCT date FROM calls")}
        cur, end = date.fromisoformat(lo), date.fromisoformat(hi)
        while cur <= end:
            if cur.isoformat() not in have:
                zero += 1
            cur += timedelta(days=1)
    d["days_with_zero_calls"] = zero
    d["speaker_labels"] = {
        "with_speaker_1": q("SELECT COUNT(*) FROM calls WHERE transcript LIKE '%Speaker 1:%'"),
        "with_speaker_2": q("SELECT COUNT(*) FROM calls WHERE transcript LIKE '%Speaker 2:%'"),
        "with_both": q("SELECT COUNT(*) FROM calls WHERE transcript LIKE '%Speaker 1:%' "
                       "AND transcript LIKE '%Speaker 2:%'"),
        "with_any_label": q("SELECT COUNT(*) FROM calls WHERE transcript LIKE '%Speaker _:%'"),
        "transcripts_available": q("SELECT COUNT(*) FROM calls WHERE transcript IS NOT NULL "
                                   "AND TRIM(transcript)<>''"),
    }
    return d


def _table(title: str, mapping: dict) -> list[str]:
    lines = [f"### {title}", "", "| key | count |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in mapping.items()]
    return lines + [""]


def to_markdown(d: dict) -> str:
    L = ["# Data audit (counts only)", ""]
    for k in ("total_rows", "rows_in_csv", "duplicate_ids", "unparsed_file_names",
              "rows_skipped_bad_timestamp", "empty_texts", "purged_rows", "missing_number",
              "days_with_zero_calls"):
        L.append(f"- {k}: {d[k]}")
    L.append(f"- date_range: {d['date_range'][0]} to {d['date_range'][1]}")
    L.append("")
    L += _table("Durations", d["durations"])
    L += _table("Per extension", d["per_extension"])
    L += _table("Per language", d["per_language"])
    L += _table("Per weekday", d["per_weekday"])
    L += _table("Per hour (local)", d["per_hour"])
    L += _table("Speaker labels in transcript", d["speaker_labels"])
    return "\n".join(L) + "\n"


def run(paths, out: str | None) -> int:
    conn = dbm.connect(paths.db)
    d = compute(conn)
    conn.close()
    md_path = out or os.path.join(paths.out_dir, "DATA_AUDIT.md")
    os.makedirs(os.path.dirname(os.path.abspath(md_path)), mode=0o700, exist_ok=True)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(to_markdown(d))
    json_path = os.path.splitext(md_path)[0] + ".json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2)
    print(f"audit rows={d['total_rows']} empty={d['empty_texts']} dups={d['duplicate_ids']} "
          f"missing_number={d['missing_number']}")
    print(f"wrote {os.path.basename(md_path)} and {os.path.basename(json_path)}")
    return 0
