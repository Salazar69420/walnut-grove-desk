"""Admin > AI assistant: switch, OpenRouter key (write-only), model picker, base prompt, prompt preview, and a try-it box."""
from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.aftercare import emergency as em
from app.aftercare import keywords as kw
from app.api.schemas import Strict
from app.assistant import ai, replies
from app.db import get_db
from app.models import Contact, User
from app.security import crypto
from app.security.auth import require
from app.services import events as ev
from app.services import sms_mode

router = APIRouter(prefix="/api/admin/assistant", tags=["assistant"])
MODEL_RX = re.compile(r"^[a-z0-9._-]+/[a-z0-9._:-]+$")


class AssistantIn(Strict):
    enabled: bool | None = None
    staff_enabled: bool | None = None
    model: str | None = Field(default=None, max_length=120)
    api_key: str | None = Field(default=None, min_length=10, max_length=300)
    clear_key: bool = False
    deny_data_collection: bool | None = None
    system_prompt: str | None = Field(default=None, max_length=6000)


def _pilot(db: Session) -> list[str]:
    nums = sms_mode.get(db)["test_numbers"]
    rows = db.scalars(select(Contact).where(Contact.phone.in_(nums), Contact.ai_enabled.is_(True), Contact.erased_at.is_(None))).all() if nums else []
    return [c.display_name for c in rows]


def _view(db: Session) -> dict[str, Any]:
    cfg = ai.get_config(db)
    _system, texts = ai.system_prompt(db)
    return {**ai.public(cfg), "active_for": _pilot(db), "knowledge_count": sum(1 for k in texts if k.startswith("K")),
            "fee_count": sum(1 for k in texts if k.startswith("F"))}


@router.get("")
def get_assistant(user: User = Depends(require("admin.settings")), db: Session = Depends(get_db)):
    return _view(db)


@router.put("")
def put_assistant(body: AssistantIn, user: User = Depends(require("admin.settings")), db: Session = Depends(get_db)):
    cfg = ai.get_config(db)
    changed = []
    if body.model is not None:
        if not MODEL_RX.fullmatch(body.model):
            raise HTTPException(status_code=400, detail="That does not look like an OpenRouter model id (for example deepseek/deepseek-v4.1-flash-1017).")
        cfg["model"] = body.model
        changed.append("model")
    if body.api_key is not None:
        key = body.api_key.strip()
        if not key.isprintable() or " " in key:
            raise HTTPException(status_code=400, detail="That key has spaces or hidden characters. Paste it again.")
        cfg["key_enc"], cfg["key_last4"] = crypto.encrypt(key), key[-4:]
        changed.append("key")
    if body.clear_key:
        cfg["key_enc"], cfg["key_last4"] = "", None
        changed.append("key removed")
    if body.deny_data_collection is not None:
        cfg["deny_data_collection"] = body.deny_data_collection
        changed.append("privacy")
    if body.system_prompt is not None:
        cfg["system_prompt"] = body.system_prompt.strip() or ai.DEFAULT_PROMPT
        changed.append("prompt")
    if body.enabled is not None:
        if body.enabled and not cfg["key_enc"]:
            raise HTTPException(status_code=409, detail="Add an OpenRouter API key first.")
        cfg["enabled"] = body.enabled
        changed.append("on" if body.enabled else "off")
    if body.staff_enabled is not None:
        if body.staff_enabled and not cfg["key_enc"]:
            raise HTTPException(status_code=409, detail="Add an OpenRouter API key first.")
        cfg["staff_enabled"] = body.staff_enabled
        changed.append("staff AI " + ("on" if body.staff_enabled else "off"))
    ai.save(db, cfg, user.display_name)
    ev.record(db, ev.ADMIN, "assistant_settings_changed", f"{user.display_name} changed the AI assistant: {', '.join(changed) or 'nothing'}", user,
              "setting", ai.KEY, {"changed": changed})
    db.commit()
    return _view(db)


@router.get("/models")
def models(user: User = Depends(require("admin.settings"))):
    return {"items": ai.list_models(), "default": ai.DEFAULT_MODEL}


@router.get("/prompt")
def prompt(user: User = Depends(require("admin.settings")), db: Session = Depends(get_db)):
    system, texts = ai.system_prompt(db)
    return {"system_prompt": system, "knowledge_count": sum(1 for k in texts if k.startswith("K")), "fee_count": sum(1 for k in texts if k.startswith("F"))}


class TryIn(Strict):
    text: str = Field(min_length=2, max_length=800)


@router.post("/try")
def try_it(body: TryIn, user: User = Depends(require("admin.settings")), db: Session = Depends(get_db)):
    """What the assistant would answer, without sending anything and without raising tasks."""
    if em.detect(body.text, kw.load_keywords(db))["emergency"]:
        return {"kind": "emergency", "reply": "Fixed emergency text (the AI is never used for emergencies).", "model": None}
    if replies.MEDICAL.search(body.text):
        return {"kind": "medical", "reply": "Fixed medical holding text (the AI is never used for medical questions).", "model": None}
    cfg = ai.get_config(db)
    if not cfg["key_enc"]:
        raise HTTPException(status_code=409, detail="Add an OpenRouter API key first.")
    system, texts = ai.system_prompt(db)
    try:
        data = ai.call(cfg, system, [{"role": "user", "content": "(Client first name: Kartik. Pets: not on file.)\n" + body.text}])
        out = ai.check(data, texts, body.text)
    except ai.AiFailed as e:
        return {"kind": "fallback", "reply": None, "model": cfg["model"], "code": str(e)}
    return {"kind": "answer" if out["answered"] else "holding", "reply": out["reply"], "model": cfg["model"], "sources": replies._titles(db, out["ids"]),
            "needs_human": out["needs_human"], "reason": out["reason"], "usage": out["usage"]}
