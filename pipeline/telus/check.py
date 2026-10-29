"""telus-check: log in and print a short, non-sensitive summary. Exit non-zero on a bad permission set."""
from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta, timezone

from . import endpoints
from .client import ReadOnlyClient, TelusError

_SAFE_SCOPE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


def write_capable_scopes(scopes: list[str]) -> list[str]:
    return [s for s in scopes if endpoints.is_write_capable(s)]


def _safe(scope: str) -> str:
    return scope if _SAFE_SCOPE.match(scope) else "<unprintable-scope>"


def count_last_7_days(client: ReadOnlyClient) -> tuple[int, bool]:
    """(count, exact). One GET; count only, nothing from the records is kept."""
    now = datetime.now(timezone.utc)
    fmt = "%Y-%m-%dT%H:%M:%S.000Z"
    body = client.get(endpoints.CALL_LOG_PATH, {
        "dateFrom": (now - timedelta(days=7)).strftime(fmt), "dateTo": now.strftime(fmt),
        "perPage": 1000, "page": 1, "view": "Simple"})
    paging = body.get("paging") or {}
    total = paging.get("totalElements")
    if isinstance(total, int):
        return total, True
    recs = body.get("records")
    n = len(recs) if isinstance(recs, list) else 0
    more = bool((body.get("navigation") or {}).get("nextPage"))
    return n, not more


def run(creds) -> int:
    code = 0
    try:
        with ReadOnlyClient(creds) as client:
            client.login()
            scopes = client.scopes
            print(f"environment: {creds.env}")
            print(f"server_host: {creds.host}")
            print("scopes: " + (" ".join(_safe(s) for s in scopes) if scopes else "(none)"))
            bad = write_capable_scopes(scopes)
            if bad:
                code = 1
                for s in bad:
                    print(f"WRITE_CAPABLE: {_safe(s)}")
                print("WARNING: this token can do more than read. Create a read-only app/JWT and run "
                      "telus-setup --force.")
            else:
                print("write_capable_scopes: none")
            for s in endpoints.REQUIRED_READ_SCOPES:
                ok = s in scopes
                print(f"{s}: {'present' if ok else 'MISSING'}")
                if not ok:
                    code = 1
            print(f"token_expires_in_seconds: {client.expires_in}")
            try:
                n, exact = count_last_7_days(client)
                print(f"calls_last_7_days: {n}" + ("" if exact else " (at least, more pages exist)"))
            except TelusError as e:
                code = 1
                print(f"calls_last_7_days: error ({e})")
    except TelusError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return code
