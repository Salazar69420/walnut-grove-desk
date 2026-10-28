"""verify-no-leak: scan files for phone numbers, number hashes and transcript shingles. Counts only."""
from __future__ import annotations

import hashlib
import hmac
import os
import random
import re
import sqlite3
import sys
from pathlib import Path

from . import db as dbm

MAX_BYTES = 50 * 1024 * 1024
SHINGLE = 8
WORD = re.compile(r"[^\W_]+", re.U)
PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d{1,2}[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}(?!\d)")
LONG_DIGITS_RE = re.compile(r"(?<!\d)\d{10,}(?!\d)")
HASH_RE = re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{64}(?![0-9a-fA-F])")
CALLREF_RE = re.compile(r"\b\d{8}-\d{6}-[A-Za-z0-9]{3}-[0-9a-f]{6,12}\b")
SQLITE_MAGIC = b"SQLite format 3\x00"


def shingle_keys(text: str):
    words = WORD.findall(text.lower())
    for i in range(len(words) - SHINGLE + 1):
        yield hashlib.sha256(" ".join(words[i:i + SHINGLE]).encode("utf-8")).digest()[:16]


class Fingerprints:
    def __init__(self, hashes: set[str], shingles: set[bytes], salt: bytes | None):
        self.hashes, self.shingles, self.salt = hashes, shingles, salt


def build_fingerprints(paths, sample: int = 300, seed: int = 0) -> Fingerprints:
    conn = dbm.connect(paths.db)
    hashes = {r[0].lower() for r in conn.execute("SELECT number_hash FROM calls WHERE number_hash IS NOT NULL")}
    refs = [r[0] for r in conn.execute(
        "SELECT rowid FROM calls WHERE private_purged=0 AND plain_text IS NOT NULL AND TRIM(plain_text)<>''")]
    rng = random.Random(seed)
    pick = rng.sample(refs, min(sample, len(refs)))
    shingles: set[bytes] = set()
    for rid in pick:
        t = conn.execute("SELECT plain_text FROM calls WHERE rowid=?", (rid,)).fetchone()[0]
        shingles.update(shingle_keys(t))
    conn.close()
    salt = None
    if os.path.exists(paths.salt):
        with open(paths.salt, "rb") as f:
            salt = f.read()
    return Fingerprints(hashes, shingles, salt)


def scan_text(text: str, fp: Fingerprints, ignore: re.Pattern | None = None) -> dict[str, int]:
    phone = known = 0
    seen: set[str] = set()
    scan_src = HASH_RE.sub(" ", CALLREF_RE.sub(" ", text))  # call_refs/hashes can look number-like
    for rx in (PHONE_RE, LONG_DIGITS_RE):
        for m in rx.finditer(scan_src):
            s = m.group(0)
            if ignore and ignore.search(s):
                continue
            digits = re.sub(r"\D", "", s)
            key = f"{m.start()}:{digits}"
            if key in seen:
                continue
            seen.add(key)
            phone += 1
            if fp.salt and len(digits) >= 10:
                cand = digits[-10:] if len(digits) > 10 else digits
                if hmac.new(fp.salt, cand.encode(), hashlib.sha256).hexdigest() in fp.hashes:
                    known += 1
    hsh = sum(1 for m in HASH_RE.finditer(text) if m.group(0).lower() in fp.hashes)
    sh = sum(1 for k in shingle_keys(text) if k in fp.shingles) if fp.shingles else 0
    return {"phone_patterns": phone, "known_numbers": known, "number_hashes": hsh, "shingles": sh}


def _sqlite_values(path: str):
    conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        for t in tables:
            try:
                cur = conn.execute(f'SELECT * FROM "{t}"')
            except sqlite3.Error:
                continue
            for row in cur:
                for v in row:
                    if isinstance(v, str):
                        yield v
    finally:
        conn.close()


def scan_file(path: str, fp: Fingerprints, ignore=None) -> tuple[dict[str, int], str]:
    """Return (counts, status) where status is scanned|skipped_large|skipped_binary|error."""
    zero = {"phone_patterns": 0, "known_numbers": 0, "number_hashes": 0, "shingles": 0}
    try:
        size = os.path.getsize(path)
        if size > MAX_BYTES:
            return zero, "skipped_large"
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return zero, "error"
    total = dict(zero)
    is_db = data.startswith(SQLITE_MAGIC)
    if not is_db and b"\x00" in data[:8192]:
        return zero, "skipped_binary"
    text = data.decode("utf-8", errors="ignore")
    for k, v in scan_text(text, fp, ignore).items():
        total[k] += v
    if is_db:
        try:
            for val in _sqlite_values(path):
                for k, v in scan_text(val, fp, ignore).items():
                    total[k] += v
        except sqlite3.Error:
            return total, "error"
    return total, "scanned"


def run(paths, targets: list[str], ignore_regex: str | None, list_files: bool, seed: int) -> int:
    fp = build_fingerprints(paths, seed=seed)
    ignore = re.compile(ignore_regex) if ignore_regex else None
    print(f"fingerprints: number_hashes={len(fp.hashes)} shingles={len(fp.shingles)}")
    matched_files = 0
    matched_list: list[str] = []
    for target in targets:
        files: list[str] = []
        if os.path.isdir(target):
            for dp, _dn, fn in os.walk(target):
                files += [os.path.join(dp, x) for x in fn if not os.path.islink(os.path.join(dp, x))]
        elif os.path.exists(target):
            files = [target]
        else:
            print(f"{target}: missing")
            continue
        agg = {"phone_patterns": 0, "known_numbers": 0, "number_hashes": 0, "shingles": 0}
        hit_files = skipped = scanned = 0
        for fpath in files:
            counts, status = scan_file(fpath, fp, ignore)
            if status == "scanned":
                scanned += 1
            elif status == "error":
                skipped += 1
            else:
                skipped += 1
            if any(counts.values()):
                hit_files += 1
                matched_list.append(fpath)
            for k, v in counts.items():
                agg[k] += v
        matched_files += hit_files
        print(f"{target}: files={len(files)} scanned={scanned} skipped={skipped} matched_files={hit_files} "
              + " ".join(f"{k}={v}" for k, v in agg.items()))
    print(f"matched_files_total: {matched_files}")
    if list_files:
        for p in matched_list:
            print(f"MATCH {p}")
    return 1 if matched_files else 0
