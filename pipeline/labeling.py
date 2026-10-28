"""label-rules command: run rules-v1 over every non-purged call."""
from __future__ import annotations

import json
from collections import Counter

from . import db as dbm
from .rules import LABELER_ID, label_call, load_rules


def summarize(counters: dict[str, Counter]) -> None:
    for field, c in counters.items():
        print(f"{field}: " + " ".join(f"{k}={v}" for k, v in sorted(c.items(), key=lambda kv: str(kv[0]))))


def run(paths, rules_path: str | None) -> int:
    rs = load_rules(rules_path)
    conn = dbm.connect(paths.db)
    counters = {k: Counter() for k in ("real_direction", "caller_type", "intent", "outcome", "urgency",
                                       "promised_followup", "contains_private_content", "language")}
    flags = Counter()
    n = skipped = 0
    now = dbm.now_iso()
    batch = []
    for r in conn.execute("SELECT call_ref, plain_text, transcript, seconds, language, has_number, ext, "
                          "private_purged FROM calls"):
        if r["private_purged"]:
            skipped += 1
            continue
        text = r["plain_text"] if (r["plain_text"] or "").strip() else r["transcript"]
        lab = label_call(rs, text, r["seconds"], r["language"], bool(r["has_number"]), r["ext"])
        d = lab.model_dump(mode="json")
        for k, c in counters.items():
            c[d[k]] += 1
        for f in d["red_flags"]:
            flags[f] += 1
        batch.append((r["call_ref"], LABELER_ID, json.dumps(d, sort_keys=True), now))
        n += 1
    with conn:
        conn.executemany("INSERT OR REPLACE INTO labels(call_ref, labeler, json, created_at) VALUES (?,?,?,?)",
                         batch)
    conn.close()
    print(f"labeled: {n} (skipped purged: {skipped})")
    counters["red_flags"] = flags
    summarize(counters)
    return 0
