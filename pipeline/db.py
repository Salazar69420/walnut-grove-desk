"""SQLite helpers. The private DB is created with restrictive permissions."""
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
    call_id TEXT PRIMARY KEY,
    call_ref TEXT UNIQUE NOT NULL,
    ts TEXT,
    date TEXT,
    hour INTEGER,
    weekday INTEGER,
    ext TEXT,
    number_hash TEXT,
    has_number INTEGER NOT NULL DEFAULT 0,
    seconds REAL,
    language TEXT,
    transcript TEXT,
    plain_text TEXT,
    private_purged INTEGER NOT NULL DEFAULT 0
);
CREATE VIRTUAL TABLE IF NOT EXISTS calls_fts USING fts5(call_ref UNINDEXED, plain_text);
CREATE TABLE IF NOT EXISTS labels (
    call_ref TEXT NOT NULL,
    labeler TEXT NOT NULL,
    json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (call_ref, labeler)
);
CREATE TABLE IF NOT EXISTS gold (
    call_ref TEXT PRIMARY KEY,
    json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS load_meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(db_path: str, create: bool = False) -> sqlite3.Connection:
    if create:
        parent = os.path.dirname(os.path.abspath(db_path))
        os.makedirs(parent, mode=0o700, exist_ok=True)
        conn = sqlite3.connect(db_path)
    else:
        if not os.path.exists(db_path):
            print(f"error: database not found (run load first)", file=sys.stderr)
            raise SystemExit(2)
        conn = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=rw", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA secure_delete=ON")
    conn.execute("PRAGMA foreign_keys=ON")
    if create:
        conn.executescript(SCHEMA)
        try:
            os.chmod(db_path, 0o600)
        except OSError:
            pass
    else:
        conn.executescript(SCHEMA)  # idempotent; adds labels/gold tables on older DBs
    return conn


def chosen_labels(conn: sqlite3.Connection, labeler: str | None = None) -> dict[str, tuple[str, str]]:
    """call_ref -> (source, json). Gold wins, else the given labeler, else the latest label."""
    out: dict[str, tuple[str, str]] = {}
    if labeler:
        for r in conn.execute("SELECT call_ref, json FROM labels WHERE labeler=?", (labeler,)):
            out[r["call_ref"]] = (labeler, r["json"])
    else:
        for r in conn.execute("SELECT call_ref, labeler, json FROM labels ORDER BY created_at, rowid"):
            out[r["call_ref"]] = (r["labeler"], r["json"])
    for r in conn.execute("SELECT call_ref, json FROM gold"):
        out[r["call_ref"]] = ("gold", r["json"])
    return out
