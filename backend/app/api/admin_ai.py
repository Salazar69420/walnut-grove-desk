"""Admin: AI provider and Retell settings. Keys are write-only (stored encrypted, never returned)."""
from __future__ import annotations

import hashlib
import logging
import os
import re
import secrets
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy.orm import Session

from app import config
from app.adapters import llm
from app.api.schemas import Strict
from app.db import get_db
from app.models import User
from app.security import crypto
from app.security.auth import require
from app.services import events as ev
from app.services import settings_store

router = APIRouter(prefix="/api/admin/settings", tags=["admin"])
log = logging.getLogger("walnut.admin")

RETELL_KEY = "retell_config"


# ---------------------------------------------------------------- AI provider
def ai_public(cfg: dict[str, Any]) -> dict[str, Any]:
    return {
        "provider": cfg["provider"], "base_url": cfg["base_url"], "model": cfg["model"],
        "key_set": bool(cfg["key_enc"]), "key_last4": cfg["key_last4"] if cfg["key_enc"] else None,
        "word_brief": bool(cfg["word_brief"]), "emergency_check": bool(cfg["emergency_check"]),
        "allow_transcript_labeling": bool(cfg["allow_transcript_labeling"]), "warning": llm.WARNING,
    }


@router.get("/ai")
def get_ai(user: User = Depends(require("admin.settings")), db: Session = Depends(get_db)):
    return ai_public(llm.get_config(db))


class AiBody(Strict):
    provider: Literal["off", "deepseek", "openrouter"] | None = None
    base_url: str | None = Field(default=None, max_length=300)
    model: str | None = Field(default=None, max_length=120)
    api_key: str | None = Field(default=None, max_length=400)
    word_brief: bool | None = None
    emergency_check: bool | None = None
    allow_transcript_labeling: bool | None = None


def _clean_line(v: str) -> str:
    return re.sub(r"[\r\n\x00]", "", v)


def write_llm_env(cfg: dict[str, Any]) -> bool:
    """Write the pipeline's env file with mode 0640. Returns False if the path cannot be written."""
    path = Path(config.get_settings().llm_env_path)
    key = (crypto.decrypt(cfg["key_enc"]) or "") if cfg["key_enc"] and cfg["provider"] != "off" else ""
    body = (
        f"PROVIDER={_clean_line(cfg['provider'])}\n"
        f"BASE_URL={_clean_line(cfg['base_url'])}\n"
        f"MODEL={_clean_line(cfg['model'])}\n"
        f"API_KEY={_clean_line(key)}\n"
        f"ALLOW_TRANSCRIPT_LABELING={'1' if cfg['allow_transcript_labeling'] else '0'}\n"
    )
    tmp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
        with os.fdopen(fd, "w") as f:
            f.write(body)
        os.chmod(tmp, 0o640)
        os.replace(tmp, path)
        os.chmod(path, 0o640)
        return True
    except OSError as e:
        log.warning("could not write the llm env file: %s", e.__class__.__name__)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def _valid_url(url: str) -> bool:
    if config.get_settings().is_production:
        return url.startswith("https://")
    return url.startswith(("https://", "http://127.0.0.1", "http://localhost"))


@router.put("/ai")
def put_ai(body: AiBody, actor: User = Depends(require("admin.settings")), db: Session = Depends(get_db)):
    cfg = llm.get_config(db)
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    if not changes:
        raise HTTPException(status_code=400, detail="Nothing to change")
    if "provider" in changes:
        p = changes["provider"]
        if p != cfg["provider"] and p != "off":
            cfg["base_url"] = llm.BASE_URLS[p]
            cfg["model"] = llm.DEFAULT_MODELS[p]
        cfg["provider"] = p
    if "base_url" in changes:
        url = changes["base_url"].strip().rstrip("/")
        if not _valid_url(url):
            raise HTTPException(status_code=400, detail="The address must start with https://")
        cfg["base_url"] = url
    if "model" in changes:
        if not changes["model"].strip():
            raise HTTPException(status_code=400, detail="The model name cannot be empty")
        cfg["model"] = changes["model"].strip()
    key_changed = False
    if "api_key" in changes:
        k = changes["api_key"].strip()
        if re.search(r"\s", k):
            raise HTTPException(status_code=400, detail="The key cannot contain spaces or line breaks")
        if k:
            cfg["key_enc"] = crypto.encrypt(k)
            cfg["key_last4"] = k[-4:]
        else:
            cfg["key_enc"], cfg["key_last4"] = "", None
        key_changed = True
    for flag in ("word_brief", "emergency_check", "allow_transcript_labeling"):
        if flag in changes:
            cfg[flag] = changes[flag]
    if cfg["provider"] != "off" and not cfg["key_enc"] and (cfg["word_brief"] or cfg["emergency_check"]):
        pass  # allowed: the features stay idle until a key is saved
    settings_store.put_raw(db, llm.CONFIG_KEY, cfg, by=actor.display_name)
    names = sorted(k for k in changes if k != "api_key") + (["api_key"] if key_changed else [])
    ev.record(db, ev.ADMIN, "ai_settings_changed", f"{actor.display_name} changed AI settings: {', '.join(names)}", actor,
              "setting", llm.CONFIG_KEY, {"keys": names})
    db.commit()
    out = ai_public(cfg)
    out["llm_env_written"] = write_llm_env(cfg)
    return out


@router.post("/ai/test")
def test_ai(actor: User = Depends(require("admin.settings")), db: Session = Depends(get_db)):
    client = llm.LlmClient.from_settings(db)
    if client is None:
        return {"ok": False, "detail": "Choose a provider and save a key first."}
    try:
        client.chat("You reply with one word.", "Reply with OK", max_tokens=5)
    except llm.LlmError as e:
        return {"ok": False, "detail": f"The test failed: {e}."}
    return {"ok": True, "detail": "The model answered."}


# ---------------------------------------------------------------- Retell
def retell_config(db: Session) -> dict[str, Any]:
    cfg = {"enabled": False, "dry_run": True, "secret_hash": "", "api_key_enc": "", "api_key_last4": None}
    stored = settings_store.get_raw(db, RETELL_KEY, None)
    if isinstance(stored, dict):
        cfg.update(stored)
    return cfg


def retell_public(cfg: dict[str, Any]) -> dict[str, Any]:
    return {"enabled": bool(cfg["enabled"]), "dry_run": bool(cfg["dry_run"]), "secret_set": bool(cfg["secret_hash"]),
            "api_key_set": bool(cfg["api_key_enc"]), "api_key_last4": cfg["api_key_last4"] if cfg["api_key_enc"] else None}


@router.get("/retell")
def get_retell(user: User = Depends(require("admin.settings")), db: Session = Depends(get_db)):
    return retell_public(retell_config(db))


class RetellBody(Strict):
    enabled: bool | None = None
    dry_run: bool | None = None
    regenerate_secret: bool | None = None
    retell_api_key: str | None = Field(default=None, max_length=400)


@router.put("/retell")
def put_retell(body: RetellBody, actor: User = Depends(require("admin.settings")), db: Session = Depends(get_db)):
    cfg = retell_config(db)
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    if not changes:
        raise HTTPException(status_code=400, detail="Nothing to change")
    new_secret = None
    if changes.get("regenerate_secret"):
        new_secret = secrets.token_urlsafe(48)
        cfg["secret_hash"] = hashlib.sha256(new_secret.encode()).hexdigest()
    if "enabled" in changes:
        if changes["enabled"] and not cfg["secret_hash"]:
            raise HTTPException(status_code=400, detail="Generate a secret before turning Retell on")
        cfg["enabled"] = changes["enabled"]
    if "dry_run" in changes:
        cfg["dry_run"] = changes["dry_run"]
    if "retell_api_key" in changes:
        k = changes["retell_api_key"].strip()
        if re.search(r"\s", k):
            raise HTTPException(status_code=400, detail="The key cannot contain spaces or line breaks")
        cfg["api_key_enc"] = crypto.encrypt(k) if k else ""
        cfg["api_key_last4"] = k[-4:] if k else None
    settings_store.put_raw(db, RETELL_KEY, cfg, by=actor.display_name)
    names = sorted(k for k in changes if k not in ("retell_api_key",)) + (["retell_api_key"] if "retell_api_key" in changes else [])
    ev.record(db, ev.ADMIN, "retell_settings_changed", f"{actor.display_name} changed Retell settings: {', '.join(names)}", actor,
              "setting", RETELL_KEY, {"keys": names})
    db.commit()
    out = retell_public(cfg)
    if new_secret:
        out["secret"] = new_secret
    return out
