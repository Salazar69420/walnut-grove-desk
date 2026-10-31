"""Content studio: Contento, the clinic's AI video and image maker, runs beside Walnut Grove Desk on the same server and is
shown inside the Content studio page as part of the app.

* Sign-in: the Desk signs a two-minute, single-use link for an owner (HMAC with WALNUT_CONTENTO_SECRET, shared only by the two
  server processes); Contento turns it into its operator session. No second password.
* Summary (recent videos, clients, which keys are set) and handing Contento the OpenRouter key the Desk already holds are
  server-to-server calls on 127.0.0.1 with the same secret as a bearer token. Key values are never shown.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx

LOCAL = "http://127.0.0.1:3100"


def public_url() -> str:
    explicit = os.environ.get("WALNUT_CONTENTO_URL", "").strip().rstrip("/")
    if explicit:
        return explicit
    host = urlparse(os.environ.get("WALNUT_PUBLIC_URL", "")).netloc
    return f"https://contento.{host}" if host else ""


def _secret() -> str | None:
    s = os.environ.get("WALNUT_CONTENTO_SECRET", "").strip()
    return s if len(s) >= 32 else None


def linked() -> bool:
    return bool(_secret())


def sso_url(name: str, next_path: str = "/admin/dashboard") -> str | None:
    s = _secret()
    if not s or not public_url():
        return None
    payload = base64.urlsafe_b64encode(json.dumps({"exp": int(time.time()) + 120, "nonce": secrets.token_hex(12), "name": name[:80],
                                                   "scope": "contento-admin"}).encode()).decode().rstrip("=")
    sig = base64.urlsafe_b64encode(hmac.new(s.encode(), payload.encode(), hashlib.sha256).digest()).decode().rstrip("=")
    return f"{public_url()}/api/desk/sso?" + urlencode({"t": f"{payload}.{sig}", "next": next_path})


def is_up() -> bool:
    try:
        return httpx.get(f"{LOCAL}/login", timeout=3.0).status_code < 500
    except httpx.HTTPError:
        return False


def summary() -> dict[str, Any] | None:
    s = _secret()
    if not s:
        return None
    try:
        r = httpx.get(f"{LOCAL}/api/desk/summary", headers={"Authorization": f"Bearer {s}"}, timeout=10.0)
        return r.json() if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        return None


def share_openrouter_key(key: str) -> bool:
    s = _secret()
    if not s or not key:
        return False
    try:
        r = httpx.post(f"{LOCAL}/api/desk/keys", headers={"Authorization": f"Bearer {s}"}, json={"openrouter": key}, timeout=10.0)
        return r.status_code == 200
    except httpx.HTTPError:
        return False
