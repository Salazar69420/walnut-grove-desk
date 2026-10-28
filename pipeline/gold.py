"""Gold sample, candidates, show, label commands."""
from __future__ import annotations

import json
import os
import random
import re

_NUMWORDS = re.compile(r"\b(?:zero|oh|o|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|double|triple)(?:[\s,.-]+(?:zero|oh|o|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|double|triple))+\b", re.I)
import sys
from collections import defaultdict

from pydantic import ValidationError

from . import db as dbm
from .schema import CallLabel

HOUR_BUCKETS = [(0, 7), (8, 11), (12, 15), (16, 18), (19, 23)]


def _bucket(h: int) -> int:
    for i, (lo, hi) in enumerate(HOUR_BUCKETS):
        if lo <= h <= hi:
            return i
    return len(HOUR_BUCKETS) - 1


def sample(paths, n: int, seed: int, min_chars: int) -> int:
    conn = dbm.connect(paths.db)
    strata = defaultdict(list)
    for r in conn.execute("SELECT call_ref, hour, weekday, ts FROM calls WHERE private_purged=0 "
                          "AND LENGTH(COALESCE(plain_text,'')) >= ? ORDER BY call_ref", (min_chars,)):
        if r["hour"] is None or r["weekday"] is None:
            continue
        strata[(_bucket(r["hour"]), r["weekday"])].append((r["ts"], r["call_ref"]))
    conn.close()
    rng = random.Random(seed)
    chosen: list[tuple[str, str]] = []
    keys = sorted(strata)
    rng.shuffle(keys)
    for k in keys:  # one per stratum first (coverage), until n reached
        if len(chosen) >= n:
            break
        pool = strata[k]
        chosen.append(pool.pop(rng.randrange(len(pool))))
    rest = [x for k in keys for x in strata[k]]
    rng.shuffle(rest)  # remaining slots: uniform over remaining calls (proportional to stratum size)
    chosen += rest[: max(0, n - len(chosen))]
    chosen.sort()
    os.makedirs(paths.gold_dir, mode=0o700, exist_ok=True)
    out = os.path.join(paths.gold_dir, "sample.txt")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(ref for _, ref in chosen) + ("\n" if chosen else ""))
    print(f"sample: {len(chosen)} call_refs written (strata available: {len(keys)})")
    return 0


def candidates(paths, query: str, limit: int) -> int:
    conn = dbm.connect(paths.db)
    try:
        rows = conn.execute(
            "SELECT c.call_ref, c.seconds FROM calls_fts f JOIN calls c ON c.rowid = f.rowid "
            "WHERE calls_fts MATCH ? AND c.private_purged=0 ORDER BY rank LIMIT ?", (query, limit)).fetchall()
    except Exception as e:  # sqlite3.OperationalError on bad FTS syntax
        print(f"error: bad fts query ({type(e).__name__})", file=sys.stderr)
        return 2
    for r in rows:
        print(f"{r['call_ref']}\t{r['seconds']}")
    conn.close()
    return 0


def show(paths, call_ref: str, chars: int, offset: int) -> int:
    conn = dbm.connect(paths.db)
    r = conn.execute("SELECT * FROM calls WHERE call_ref=?", (call_ref,)).fetchone()
    conn.close()
    if not r:
        print("error: unknown call_ref", file=sys.stderr)
        return 2
    text = r["plain_text"] if (r["plain_text"] or "").strip() else (r["transcript"] or "")
    total = len(text or "")
    if r["private_purged"]:
        print("[purged]")
        return 0
    chunk = re.sub(r"\d", "#", text[offset: offset + chars])
    # spoken digits (phone numbers read aloud) are masked too
    chunk = _NUMWORDS.sub("#", chunk)
    print(f"call_ref={r['call_ref']} date={r['date']} hour={r['hour']} weekday={r['weekday']} "
          f"seconds={r['seconds']} language={r['language']} chars={offset}-{min(total, offset + chars)}/{total}")
    print(chunk)
    return 0


def label(paths, call_ref: str, raw: str) -> int:
    try:
        lab = CallLabel.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValidationError) as e:
        print(f"error: invalid label ({type(e).__name__}): "
              + "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['type']}" for x in getattr(e, 'errors', lambda: [])()),
              file=sys.stderr)
        return 2
    conn = dbm.connect(paths.db)
    if not conn.execute("SELECT 1 FROM calls WHERE call_ref=?", (call_ref,)).fetchone():
        print("error: unknown call_ref", file=sys.stderr)
        return 2
    with conn:
        conn.execute("INSERT OR REPLACE INTO gold(call_ref, json, created_at) VALUES (?,?,?)",
                     (call_ref, json.dumps(lab.model_dump(mode="json"), sort_keys=True), dbm.now_iso()))
    n = conn.execute("SELECT COUNT(*) FROM gold").fetchone()[0]
    conn.close()
    print(f"gold labels: {n}")
    return 0
