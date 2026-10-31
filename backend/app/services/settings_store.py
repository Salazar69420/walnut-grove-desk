"""Key/value settings with typed defaults. Secrets get their own endpoints later."""
from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy.orm import Session

from app.models import Setting
from app.services import timeutil

DEFAULTS: dict[str, Any] = {
    "clinic_name": "Walnut Grove Animal Hospital",
    "banner_on": True,
    "tog_req": True,
    "tog_msg": True,
    "tog_missed": False,
}

# Typed keys: (python type, max length for strings)
TYPED: dict[str, tuple[type, int]] = {
    "clinic_name": (str, 120),
    "banner_on": (bool, 0),
    "tog_req": (bool, 0),
    "tog_msg": (bool, 0),
    "tog_missed": (bool, 0),
}

KEY_RE = re.compile(r"^[a-z][a-z0-9_.]{0,63}$")
# Secret-bearing settings are written only by their own endpoints.
RESERVED_PREFIXES = ("ai_", "ai.", "retell_", "retell.", "secret", "fernet")
MAX_JSON_CHARS = 4000


class SettingError(ValueError):
    pass


def validate(key: str, value: Any) -> Any:
    if not KEY_RE.match(key):
        raise SettingError(f"bad setting name: {key!r}")
    if key.startswith(RESERVED_PREFIXES):
        raise SettingError(f"{key} cannot be set here")
    if key in TYPED:
        typ, maxlen = TYPED[key]
        if typ is bool:
            if not isinstance(value, bool):
                raise SettingError(f"{key} must be true or false")
        elif typ is str:
            if not isinstance(value, str) or not value.strip():
                raise SettingError(f"{key} must be non-empty text")
            if len(value) > maxlen:
                raise SettingError(f"{key} is too long")
            value = value.strip()
    else:
        try:
            enc = json.dumps(value)
        except (TypeError, ValueError):
            raise SettingError(f"{key} is not valid JSON")
        if len(enc) > MAX_JSON_CHARS:
            raise SettingError(f"{key} is too long")
    return value


def get_all(db: Session) -> dict[str, Any]:
    out = dict(DEFAULTS)
    for row in db.query(Setting).all():
        if row.key.startswith(RESERVED_PREFIXES):
            continue
        try:
            out[row.key] = json.loads(row.value_json)
        except ValueError:
            continue
    return out


def get(db: Session, key: str, default: Any = None) -> Any:
    row = db.get(Setting, key)
    if row is None:
        return DEFAULTS.get(key, default)
    try:
        return json.loads(row.value_json)
    except ValueError:
        return DEFAULTS.get(key, default)


def put_raw(db: Session, key: str, value: Any, by: str = "") -> None:
    """Write without the admin-endpoint checks (internal callers: AI, Retell, keywords, seed)."""
    row = db.get(Setting, key)
    enc = json.dumps(value)
    if row is None:
        db.add(Setting(key=key, value_json=enc, updated_at=timeutil.now_utc(), updated_by=by))
    else:
        row.value_json = enc
        row.updated_at = timeutil.now_utc()
        row.updated_by = by


def get_raw(db: Session, key: str, default: Any = None) -> Any:
    row = db.get(Setting, key)
    if row is None:
        return default
    try:
        return json.loads(row.value_json)
    except ValueError:
        return default


def put(db: Session, key: str, value: Any, by: str = "") -> None:
    value = validate(key, value)
    row = db.get(Setting, key)
    enc = json.dumps(value)
    if row is None:
        db.add(Setting(key=key, value_json=enc, updated_at=timeutil.now_utc(), updated_by=by))
    else:
        row.value_json = enc
        row.updated_at = timeutil.now_utc()
        row.updated_by = by


def public(db: Session) -> dict[str, Any]:
    """The bits every signed-in user sees in /api/auth/me."""
    s = get_all(db)
    mode = get_raw(db, "sms_mode", "practice")
    nums = get_raw(db, "sms_test_numbers", [])
    return {"clinic_name": s["clinic_name"], "banner_on": bool(s["banner_on"]), "sms_mode": mode if mode in ("practice", "test_numbers_only", "live") else "practice",
            "sms_test_numbers": [n for n in nums if isinstance(n, str)][:20]}
