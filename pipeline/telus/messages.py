"""Counts-only look at the TELUS message store (SMS and voicemail) for the login's own extension.

    $P telus-messages-probe --days 120

Read-only: GET /extension/~/message-store only, through ReadOnlyClient. Prints numbers only (never a phone number, name or text).
"""
from __future__ import annotations

import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

from . import endpoints
from .check import write_capable_scopes
from .client import ReadOnlyClient, TelusError


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def probe(client: ReadOnlyClient, days: int) -> dict:
    now = datetime.now(timezone.utc)
    out: dict = {}
    for mtype in ("SMS", "VoiceMail", "Fax"):
        n = 0
        direction: Counter = Counter()
        counterparties: set[str] = set()
        first = last = None
        try:
            for _page, body in client.iter_pages(endpoints.MESSAGE_STORE_PATH, {
                    "messageType": mtype, "dateFrom": _iso(now - timedelta(days=days)), "dateTo": _iso(now), "perPage": 100}, max_pages=200):
                for r in body.get("records") or []:
                    n += 1
                    direction[str(r.get("direction") or "?")] += 1
                    other = (r.get("from") if r.get("direction") == "Inbound" else (r.get("to") or [{}])[0] if isinstance(r.get("to"), list) else r.get("to")) or {}
                    num = other.get("phoneNumber") or other.get("extensionNumber") if isinstance(other, dict) else None
                    if num:
                        counterparties.add(str(num))
                    t = str(r.get("creationTime") or "")[:10]
                    if t:
                        first = t if first is None or t < first else first
                        last = t if last is None or t > last else last
        except TelusError as e:
            out[mtype] = {"error": f"HTTP {e.status}" if e.status else "request failed"}
            continue
        out[mtype] = {"messages": n, "by_direction": dict(direction), "distinct_counterparties": len(counterparties), "first": first, "last": last}
    return out


def run(creds, days: int) -> int:
    try:
        with ReadOnlyClient(creds) as client:
            client.login()
            if write_capable_scopes(client.scopes):
                print("error: token has write-capable scopes; refusing (run telus-check)", file=sys.stderr)
                return 1
            res = probe(client, days)
    except TelusError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    for k, v in res.items():
        print(f"{k}: {v}")
    return 0
