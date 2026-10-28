"""purge-private: irreversibly remove transcript text of private-flagged calls."""
from __future__ import annotations

import json
import os
import secrets
import sys

from . import db as dbm


def select_private(conn, conservative: bool = False) -> tuple[list[str], int]:
    """Return (call_refs to purge, count of calls with no label at all)."""
    refs = set()
    labeled = set()
    if conservative:
        for r in conn.execute("SELECT call_ref, json FROM labels"):
            labeled.add(r["call_ref"])
            if json.loads(r["json"]).get("contains_private_content"):
                refs.add(r["call_ref"])
    else:
        for ref, (_src, js) in dbm.chosen_labels(conn).items():
            labeled.add(ref)
            if json.loads(js).get("contains_private_content"):
                refs.add(ref)
    # gold overrides even in conservative mode
    for r in conn.execute("SELECT call_ref, json FROM gold"):
        labeled.add(r["call_ref"])
        if json.loads(r["json"]).get("contains_private_content"):
            refs.add(r["call_ref"])
        else:
            refs.discard(r["call_ref"])
    existing = {r[0] for r in conn.execute("SELECT call_ref FROM calls WHERE private_purged=0")}
    unlabeled = len(existing - labeled)
    return sorted(refs & existing), unlabeled


def shred_file(path: str) -> bool:
    if not os.path.isfile(path):
        return False
    size = os.path.getsize(path)
    with open(path, "r+b", buffering=0) as f:
        left = size
        while left > 0:
            n = min(left, 1 << 20)
            f.write(secrets.token_bytes(n))
            left -= n
        f.flush()
        os.fsync(f.fileno())
    os.unlink(path)
    return True


def run(paths, conservative: bool = False, shred_csv: bool = False) -> int:
    conn = dbm.connect(paths.db)
    refs, unlabeled = select_private(conn, conservative)
    conn.execute("PRAGMA secure_delete=ON")
    with conn:
        for i in range(0, len(refs), 500):
            chunk = refs[i:i + 500]
            ph = ",".join("?" * len(chunk))
            # 1) same-length overwrite
            conn.execute(
                f"UPDATE calls SET "
                f"transcript = CASE WHEN transcript IS NULL THEN NULL ELSE "
                f"substr(replace(hex(zeroblob(length(transcript))),'0','x'),1,length(transcript)) END, "
                f"plain_text = CASE WHEN plain_text IS NULL THEN NULL ELSE "
                f"substr(replace(hex(zeroblob(length(plain_text))),'0','x'),1,length(plain_text)) END "
                f"WHERE call_ref IN ({ph})", chunk)
            conn.execute(f"UPDATE calls_fts SET plain_text='' WHERE rowid IN "
                         f"(SELECT rowid FROM calls WHERE call_ref IN ({ph}))", chunk)
        conn.commit()
        for i in range(0, len(refs), 500):
            chunk = refs[i:i + 500]
            ph = ",".join("?" * len(chunk))
            # 2) NULL out, mark purged, drop from FTS
            conn.execute(f"DELETE FROM calls_fts WHERE rowid IN (SELECT rowid FROM calls WHERE call_ref IN ({ph}))",
                         chunk)
            conn.execute(f"UPDATE calls SET transcript=NULL, plain_text=NULL, private_purged=1 "
                         f"WHERE call_ref IN ({ph})", chunk)
    # rebuild/merge FTS segments so deleted terms do not linger, then compact the file
    conn.execute("INSERT INTO calls_fts(calls_fts) VALUES('rebuild')")
    conn.execute("INSERT INTO calls_fts(calls_fts) VALUES('optimize')")
    conn.commit()
    conn.execute("VACUUM")
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    print(f"purged: {len(refs)}")
    print(f"unlabeled_kept: {unlabeled}")
    if shred_csv:
        print(f"csv_shredded: {1 if shred_file(paths.csv) else 0}")
    return 0
