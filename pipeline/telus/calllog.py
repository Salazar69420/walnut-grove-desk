"""Pull call-log metadata into private.db (telus_calls, telus_sync_state). Counts-only output.

Stored per call: id, local start time, duration, direction, a fixed-vocabulary result, extension id,
has_recording, and HMAC-SHA256 hashes of the two phone numbers (digits only, same salt and normalisation as
`load`). Raw numbers, names and any free text from the response are never stored.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta, timezone

from .. import db as dbm
from ..config import TZ_NAME
from ..load import hmac_hex, load_salt, normalize_number
from . import endpoints
from .check import write_capable_scopes
from .client import ReadOnlyClient, TelusError

try:
    from zoneinfo import ZoneInfo
    _TZ = ZoneInfo(TZ_NAME)
except Exception:  # pragma: no cover
    _TZ = None

PER_PAGE = 1000
DEFAULT_DAYS = 30
OVERLAP = timedelta(days=1)  # re-read the last day so calls that were still in progress get final values
STATE_THROUGH = "synced_through"
STATE_INFLIGHT = "inflight"

RESULT_MAP = {
    "accepted": "answered", "call connected": "answered", "answered": "answered", "connected": "answered",
    "received": "answered", "reply": "answered",
    "missed": "missed", "no answer": "missed", "busy": "missed", "rejected": "missed", "declined": "missed",
    "voicemail": "voicemail",
    "hang up": "hangup", "abandoned": "hangup",
    "call failed": "failed", "failed": "failed", "unknown": "other",
}
DIRECTIONS = {"inbound": "inbound", "outbound": "outbound"}
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_EXT_RE = re.compile(r"^[0-9]{1,20}$")


def parse_since(text: str) -> datetime:
    """ISO 8601 to an aware datetime. A naive value is read as America/Vancouver local time."""
    try:
        dt = datetime.fromisoformat(text.strip())
    except ValueError:
        raise ValueError("--since must be an ISO 8601 date or datetime") from None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_TZ or timezone.utc)
    return dt.astimezone(timezone.utc)


def _iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def map_result(raw) -> str:
    return RESULT_MAP.get(str(raw or "").strip().lower(), "other")


def _hash_num(salt: bytes, party) -> str | None:
    if not isinstance(party, dict):
        return None
    digits = normalize_number(party.get("phoneNumber") if isinstance(party.get("phoneNumber"), str) else None)
    return hmac_hex(salt, digits) if digits else None


def _ext_id(rec: dict, direction: str) -> str | None:
    cands = []
    ext = rec.get("extension")
    if isinstance(ext, dict):
        cands.append(ext.get("id"))
    first, second = (("to", "from") if direction == "inbound" else ("from", "to"))
    for side in (first, second):
        p = rec.get(side)
        if isinstance(p, dict):
            cands.append(p.get("extensionId"))
    for c in cands:
        if c is not None and _EXT_RE.match(str(c)):
            return str(c)
    return None


def transform(rec: dict, salt: bytes) -> dict | None:
    """One API record to one row, or None if it has no usable id or start time."""
    if not isinstance(rec, dict):
        return None
    tid = rec.get("id")
    if tid is None or not _ID_RE.match(str(tid)):
        return None
    try:
        start = datetime.fromisoformat(str(rec.get("startTime")))
    except ValueError:
        return None
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    local = start.astimezone(_TZ) if _TZ else start
    try:
        duration = max(0, int(rec.get("duration") or 0))
    except (TypeError, ValueError):
        duration = 0
    direction = DIRECTIONS.get(str(rec.get("direction") or "").strip().lower(), "other")
    rcd = rec.get("recording")
    return {
        "telus_id": str(tid),
        "start_local": local.isoformat(),
        "date": local.date().isoformat(),
        "hour": local.hour,
        "weekday": local.weekday(),
        "duration": duration,
        "direction": direction,
        "result": map_result(rec.get("result")),
        "ext_id": _ext_id(rec, direction),
        "has_recording": 1 if isinstance(rcd, dict) and rcd.get("id") is not None else 0,
        "from_hash": _hash_num(salt, rec.get("from")),
        "to_hash": _hash_num(salt, rec.get("to")),
    }


UPSERT = """
INSERT INTO telus_calls(telus_id, start_local, date, hour, weekday, duration, direction, result, ext_id,
                        has_recording, from_hash, to_hash, synced_at)
VALUES (:telus_id, :start_local, :date, :hour, :weekday, :duration, :direction, :result, :ext_id,
        :has_recording, :from_hash, :to_hash, :synced_at)
ON CONFLICT(telus_id) DO UPDATE SET
    start_local=excluded.start_local, date=excluded.date, hour=excluded.hour, weekday=excluded.weekday,
    duration=excluded.duration, direction=excluded.direction, result=excluded.result, ext_id=excluded.ext_id,
    has_recording=excluded.has_recording, from_hash=excluded.from_hash, to_hash=excluded.to_hash,
    synced_at=excluded.synced_at
"""


def _state_get(conn, key):
    r = conn.execute("SELECT value FROM telus_sync_state WHERE key=?", (key,)).fetchone()
    return r["value"] if r else None


def _state_set(conn, key, value):
    if value is None:
        conn.execute("DELETE FROM telus_sync_state WHERE key=?", (key,))
    else:
        conn.execute("INSERT INTO telus_sync_state(key, value) VALUES (?,?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def pull(client: ReadOnlyClient, conn, salt: bytes, *, since: datetime | None = None,
         days: int | None = None, now: datetime | None = None) -> dict:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    inflight = None
    raw_inflight = _state_get(conn, STATE_INFLIGHT)
    if raw_inflight and since is None and days is None:
        try:
            d = json.loads(raw_inflight)
            inflight = (d["date_from"], d["date_to"], int(d["next_page"]))
        except (ValueError, KeyError, TypeError):
            inflight = None
    resumed = inflight is not None
    if inflight:
        date_from, date_to, page0 = inflight
    else:
        if since is not None:
            start = since
        elif days is not None:
            start = now - timedelta(days=days)
        else:
            through = _state_get(conn, STATE_THROUGH)
            start = (datetime.fromisoformat(through) - OVERLAP) if through else now - timedelta(days=DEFAULT_DAYS)
        date_from, date_to, page0 = _iso_z(start), _iso_z(now), 1

    seen = skipped = pages = 0
    max_start: str | None = None
    params = {"dateFrom": date_from, "dateTo": date_to, "perPage": PER_PAGE, "view": "Simple"}
    for page, body in client.iter_pages(endpoints.CALL_LOG_PATH, params, start_page=page0):
        pages += 1
        recs = body.get("records")
        for rec in recs if isinstance(recs, list) else []:
            row = transform(rec, salt)
            if row is None:
                skipped += 1
                continue
            row["synced_at"] = dbm.now_iso()
            conn.execute(UPSERT, row)
            seen += 1
            if max_start is None or row["start_local"] > max_start:
                max_start = row["start_local"]
        nav = body.get("navigation") or {}
        paging = body.get("paging") or {}
        more = bool(nav.get("nextPage")) or (isinstance(paging.get("totalPages"), int) and page < paging["totalPages"])
        if more:  # checkpoint: a later run resumes at the next page of the same window
            _state_set(conn, STATE_INFLIGHT, json.dumps({"date_from": date_from, "date_to": date_to,
                                                          "next_page": page + 1}))
        conn.commit()
    _state_set(conn, STATE_INFLIGHT, None)
    _state_set(conn, STATE_THROUGH, date_to)
    conn.commit()
    return {"pages": pages, "records": seen, "skipped": skipped, "resumed": int(resumed)}


def run(paths, creds, since_text: str | None, days: int | None) -> int:
    since = None
    if since_text:
        try:
            since = parse_since(since_text)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
    if days is not None and days < 1:
        print("error: --days must be at least 1", file=sys.stderr)
        return 2
    conn = dbm.connect(paths.db, create=True)
    salt = load_salt(paths.salt)
    try:
        before = conn.execute("SELECT COUNT(*) FROM telus_calls").fetchone()[0]
        try:
            with ReadOnlyClient(creds) as client:
                client.login()
                bad = write_capable_scopes(client.scopes)
                if bad:
                    print(f"error: token has {len(bad)} write-capable scope(s); refusing to pull "
                          f"(run telus-check)", file=sys.stderr)
                    return 1
                res = pull(client, conn, salt, since=since, days=days)
        except TelusError as e:
            conn.commit()
            print(f"error: {e}", file=sys.stderr)
            return 1
        after = conn.execute("SELECT COUNT(*) FROM telus_calls").fetchone()[0]
    finally:
        conn.close()
    print(f"telus-pull-calllog pages={res['pages']} records={res['records']} skipped={res['skipped']} "
          f"new={after - before} total={after} resumed={res['resumed']}")
    return 0
