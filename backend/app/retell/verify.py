"""Retell webhook signature check, isolated on purpose.

This is my best understanding of Retell's scheme and MUST be checked against the current Retell
documentation (their SDK exposes it as `Retell.verify(body, api_key, signature)`) before going live.

Understood scheme:
  * header `x-retell-signature`
  * newer form: `v=<timestamp_ms>,d=<hex>` where d = HMAC-SHA256(key=api_key, msg=body + timestamp)
    and the timestamp must be within 5 minutes
  * older form: the hex HMAC-SHA256(key=api_key, msg=body)

The shared secret header (X-Walnut-Secret) is the primary protection; this is a second check that only
runs when a Retell API key is stored.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import time

MAX_AGE_SECONDS = 300
_V_RX = re.compile(r"^v=(\d{10,16}),d=([0-9a-fA-F]{64})$")


def _hmac_hex(key: str, msg: bytes) -> str:
    return hmac.new(key.encode(), msg, hashlib.sha256).hexdigest()


def verify_signature(body: bytes, api_key: str, signature: str, now: float | None = None) -> bool:
    if not api_key or not signature:
        return False
    signature = signature.strip()
    m = _V_RX.match(signature)
    if m:
        ts, digest = m.group(1), m.group(2).lower()
        now_ms = (now if now is not None else time.time()) * 1000
        if abs(now_ms - int(ts)) > MAX_AGE_SECONDS * 1000:
            return False
        return hmac.compare_digest(_hmac_hex(api_key, body + ts.encode()), digest)
    if re.fullmatch(r"[0-9a-fA-F]{64}", signature):
        return hmac.compare_digest(_hmac_hex(api_key, body), signature.lower())
    return False


def sign(body: bytes, api_key: str, now: float | None = None) -> str:
    """Make a signature in the newer form (used by tests and the docs examples)."""
    ts = str(int((now if now is not None else time.time()) * 1000))
    return f"v={ts},d={_hmac_hex(api_key, body + ts.encode())}"
