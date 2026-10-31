"""Caller numbers for recorded calls, so contacts found in call recordings can show their phone number (owner request).

    telus-numbers --since 2025-07-01 --export /var/lib/walnut-desk-telus/numbers.json

Read-only: the same GET-only client and write-scope refusal as telus-pull-calllog. For each call that has a recording, the OTHER
party's number (the caller on an inbound call, the person called on an outbound call) is written next to the recording id. Calls
between extensions and numbers that are not ordinary North American numbers are left out. Nothing is printed except counts.
The file is for the app importer (pipeline/app_numbers.py), which deletes it as soon as it has read it.
"""
from __future__ import annotations

import json
import os
import re
import sys

from . import endpoints
from .calllog import PER_PAGE, _ID_RE, _iso_z, parse_since, DIRECTIONS
from .check import write_capable_scopes
from .client import ReadOnlyClient, TelusError

NANP = re.compile(r"^[2-9][0-9]{2}[2-9][0-9]{6}$")


def other_party(rec: dict) -> tuple[str, str] | None:
    """(recording id, +1 number) for one call-log record, or None."""
    if not isinstance(rec, dict):
        return None
    rcd = rec.get("recording")
    rid = rcd.get("id") if isinstance(rcd, dict) else None
    if rid is None or not _ID_RE.match(str(rid)):
        return None
    direction = DIRECTIONS.get(str(rec.get("direction") or "").strip().lower(), "other")
    if direction not in ("inbound", "outbound"):
        return None
    party = rec.get("from" if direction == "inbound" else "to")
    if not isinstance(party, dict) or party.get("extensionId"):
        return None  # an internal extension, not a client
    raw = party.get("phoneNumber")
    digits = re.sub(r"\D", "", raw) if isinstance(raw, str) else ""
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if not NANP.match(digits):
        return None
    return str(rid), "+1" + digits


def write_export(path: str, numbers: dict[str, str]) -> None:
    tmp = f"{path}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o660)
    with os.fdopen(fd, "w") as fh:
        json.dump({"v": 1, "numbers": numbers}, fh, separators=(",", ":"))
    os.chmod(tmp, 0o660)
    os.replace(tmp, path)


def run(creds, since_text: str, export_path: str) -> int:
    from datetime import datetime, timezone
    try:
        since = parse_since(since_text)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    numbers: dict[str, str] = {}
    seen = 0
    try:
        with ReadOnlyClient(creds) as client:
            client.login()
            bad = write_capable_scopes(client.scopes)
            if bad:
                print(f"error: token has {len(bad)} write-capable scope(s); refusing to pull (use a read-only TELUS app, then telus-setup --force)",
                      file=sys.stderr)
                return 1
            params = {"dateFrom": _iso_z(since), "dateTo": _iso_z(datetime.now(timezone.utc)), "perPage": PER_PAGE, "view": "Simple"}
            for _page, body in client.iter_pages(endpoints.CALL_LOG_PATH, params):
                recs = body.get("records")
                for rec in recs if isinstance(recs, list) else []:
                    seen += 1
                    hit = other_party(rec)
                    if hit:
                        numbers[hit[0]] = hit[1]
    except TelusError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    write_export(export_path, numbers)
    print(f"telus-numbers records={seen} recorded_calls_with_a_client_number={len(numbers)} distinct_numbers={len(set(numbers.values()))} (nothing else shown)")
    return 0
