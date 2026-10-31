"""telus-sync: windowed call-log pull, live metrics, analytics.json for the web app.

Contract: docs/CONTRACT_LIVE_BRIEF.md. The pull never moves the incremental cursor used by
telus-pull-calllog (track_state=False). Only counts and fixed codes leave this module: no numbers, hashes,
call ids or exception text reach analytics.json, status.json or the logs.
"""
from __future__ import annotations

import json
import math
import os
import re
from datetime import datetime

from . import calllog, live
from .client import TelusAuthError, TelusError, TelusHTTPError
from .recordings import _fsync_dir, iso_z

ANALYTICS_NAME = "analytics.json"
DEFAULT_DAYS = 35
MAX_DAYS = 90
MAX_ROWS = 2000
MAX_BYTES = 256 * 1024
MAX_STR = 40
_SAFE = re.compile(r"^[A-Za-z0-9_.:-]{1,%d}$" % MAX_STR)
_ROW_KEYS = {"metric", "dim1", "dim2", "value", "n"}


def code_for(e: BaseException) -> str:
    if isinstance(e, TelusAuthError):
        return "login_failed"
    if isinstance(e, TelusHTTPError) and e.status == 429:
        return "rate_limited"
    if isinstance(e, TelusHTTPError) and e.status == 401:
        return "login_failed"
    return "error"


def validate(doc: dict) -> None:
    """Raise ValueError (fixed text, no data) unless doc obeys the analytics.json limits."""
    rows = doc.get("rows")
    if not isinstance(rows, list) or len(rows) > MAX_ROWS:
        raise ValueError("rows")
    for r in rows:
        if not isinstance(r, dict) or set(r) != _ROW_KEYS:
            raise ValueError("row shape")
        if not isinstance(r["metric"], str) or not _SAFE.match(r["metric"]):
            raise ValueError("metric")
        for k in ("dim1", "dim2"):
            if r[k] is not None and (not isinstance(r[k], str) or not _SAFE.match(r[k])):
                raise ValueError("dim")
        v = r["value"]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            raise ValueError("value")
        if isinstance(r["n"], bool) or not isinstance(r["n"], int) or r["n"] < 0:
            raise ValueError("n")


def build_document(conn, now: datetime) -> dict:
    res = live.compute(conn, now)
    doc = {"v": 1, "generated_at": iso_z(now), "timezone": live.TZ_NAME, "window": res["window"],
           "rows": res["rows"]}
    validate(doc)
    if len(encode(doc)) > MAX_BYTES:
        raise ValueError("size")
    return doc


def encode(doc: dict) -> bytes:
    return json.dumps(doc, separators=(",", ":")).encode("utf-8")


def write_analytics(bridge: str, doc: dict) -> None:
    """Atomic: temp file in the bridge, fsync, mode 660, rename over analytics.json, fsync the folder."""
    final = os.path.join(bridge, ANALYTICS_NAME)
    tmp = os.path.join(bridge, f".analytics.{os.getpid()}.{os.urandom(4).hex()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o660)
    try:
        os.fchmod(fd, 0o660)
        with os.fdopen(fd, "wb") as f:
            f.write(encode(doc))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, final)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    _fsync_dir(bridge)


def execute(client, conn, salt: bytes, bridge: str, now: datetime, days: int | None = None) -> tuple[str, int]:
    """Pull the last `days` days (cursor untouched), compute, validate and publish. (code, rows)."""
    days = DEFAULT_DAYS if days is None else max(1, min(int(days), MAX_DAYS))
    try:
        calllog.pull(client, conn, salt, days=days, now=now, track_state=False)
    except TelusError as e:
        return code_for(e), 0
    try:
        doc = build_document(conn, now)
    except ValueError:
        return "error", 0
    write_analytics(bridge, doc)
    return "ok", len(doc["rows"])
