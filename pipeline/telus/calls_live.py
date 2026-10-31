"""telus-calls: the latest calls from the TELUS call log for the app's Phone page, every few minutes (owner request).

Read-only (GET-only client, refuses a login with write permissions). Pulls the last HOURS hours and writes
<bridge>/calls/live.json: one row per call with time, direction, result, length, the client's number (the caller on an
inbound call, the person called on an outbound call; ordinary North American numbers only) and the recording id.
The app folds it into the call log (app/services/calls_live.py). Prints counts only.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone

from . import endpoints
from .calllog import DIRECTIONS, PER_PAGE, _ID_RE, _iso_z, map_result
from .check import write_capable_scopes
from .client import ReadOnlyClient, TelusError
from .numbers import NANP

HOURS = 48
MAX_ROWS = 3000


def row(rec: dict) -> dict | None:
    if not isinstance(rec, dict) or rec.get("id") is None or not _ID_RE.match(str(rec["id"])):
        return None
    try:
        start = datetime.fromisoformat(str(rec.get("startTime")).replace("Z", "+00:00"))
    except ValueError:
        return None
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    direction = DIRECTIONS.get(str(rec.get("direction") or "").strip().lower(), "other")
    number = None
    party = rec.get("from" if direction == "inbound" else "to") if direction in ("inbound", "outbound") else None
    if isinstance(party, dict) and not party.get("extensionId") and isinstance(party.get("phoneNumber"), str):
        digits = "".join(ch for ch in party["phoneNumber"] if ch.isdigit())
        if len(digits) == 11 and digits.startswith("1"):
            digits = digits[1:]
        number = "+1" + digits if NANP.match(digits) else None
    if direction in ("inbound", "outbound") and number is None and isinstance(party, dict) and party.get("extensionId"):
        direction = "internal"
    rcd = rec.get("recording")
    rid = rcd.get("id") if isinstance(rcd, dict) else None
    try:
        duration = max(0, int(rec.get("duration") or 0))
    except (TypeError, ValueError):
        duration = 0
    return {"id": str(rec["id"]), "at": start.astimezone(timezone.utc).isoformat(), "d": direction, "r": map_result(rec.get("result")),
            "s": duration, "n": number, "rec": str(rid) if rid is not None and _ID_RE.match(str(rid)) else None}


def write(bridge: str, rows: list[dict]) -> None:
    d = os.path.join(bridge, "calls")
    os.makedirs(d, mode=0o770, exist_ok=True)
    path = os.path.join(d, "live.json")
    tmp = f"{path}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o660)
    with os.fdopen(fd, "w") as fh:
        json.dump({"v": 1, "at": datetime.now(timezone.utc).isoformat(), "calls": rows}, fh, separators=(",", ":"))
    os.chmod(tmp, 0o660)
    try:
        os.chown(tmp, -1, os.stat(bridge).st_gid)  # the bridge group, so the app can read it (no setgid bits: RestrictSUIDSGID)
    except OSError:
        pass
    os.replace(tmp, path)


def run(creds, bridge: str, hours: int = HOURS) -> int:
    now = datetime.now(timezone.utc)
    rows: list[dict] = []
    try:
        with ReadOnlyClient(creds) as client:
            client.login()
            bad = write_capable_scopes(client.scopes)
            if bad:
                print(f"error: token has {len(bad)} write-capable scope(s); refusing to pull (use a read-only TELUS app, then telus-setup --force)",
                      file=sys.stderr)
                return 1
            params = {"dateFrom": _iso_z(now - timedelta(hours=hours)), "dateTo": _iso_z(now), "perPage": PER_PAGE, "view": "Simple"}
            for _page, body in client.iter_pages(endpoints.CALL_LOG_PATH, params):
                for rec in body.get("records") if isinstance(body.get("records"), list) else []:
                    r = row(rec)
                    if r and r["d"] in ("inbound", "outbound"):
                        rows.append(r)
    except TelusError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    write(bridge, rows[:MAX_ROWS])
    print(f"telus-calls calls={len(rows)} with_number={sum(1 for r in rows if r['n'])} with_recording={sum(1 for r in rows if r['rec'])}")
    return 0
