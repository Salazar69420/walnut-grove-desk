"""Fernet helpers for stored secrets (the AI provider key, the Retell API key)."""
from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app import config


def _fernet() -> Fernet:
    s = config.get_settings()
    key = s.fernet_key
    if key:
        return Fernet(key.encode())
    if s.is_production:
        raise RuntimeError("WALNUT_FERNET_KEY is required to store secrets in production")
    # dev and test only: derive a stable key from the secret key
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(s.secret_key.encode()).digest()))


def encrypt(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt(token: str) -> str | None:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        return None
