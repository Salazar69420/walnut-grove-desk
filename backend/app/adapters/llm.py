"""OpenAI-compatible chat client (DeepSeek, OpenRouter). 15 second timeout, bodies are never logged.

A model never writes text to clients. It is used only for the second emergency check,
for wording the brief (checked by numbercheck), and, later, for transcript labeling.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.security import crypto
from app.services import settings_store

log = logging.getLogger("walnut.llm")

TIMEOUT = 15.0
CONFIG_KEY = "ai_config"
PROVIDERS = ("off", "deepseek", "openrouter")
BASE_URLS = {"deepseek": "https://api.deepseek.com", "openrouter": "https://openrouter.ai/api/v1"}
DEFAULT_MODELS = {"deepseek": "deepseek-chat", "openrouter": "deepseek/deepseek-chat"}
WARNING = (
    "DeepSeek processes data on servers outside Canada. Only turn on transcript labeling after "
    "Dr. Garg has decided this is acceptable under BC privacy law."
)

# Tests set this to an httpx.MockTransport so no network is used.
TRANSPORT: httpx.BaseTransport | None = None


class LlmError(Exception):
    """Any failure talking to the model. The message never includes request or response bodies."""


def default_config() -> dict[str, Any]:
    return {
        "provider": "off", "base_url": BASE_URLS["deepseek"], "model": "deepseek-chat", "key_enc": "", "key_last4": None,
        "word_brief": False, "emergency_check": False, "allow_transcript_labeling": False,
    }


def get_config(db: Session) -> dict[str, Any]:
    cfg = default_config()
    stored = settings_store.get_raw(db, CONFIG_KEY, None)
    if isinstance(stored, dict):
        cfg.update(stored)
    return cfg


class LlmClient:
    def __init__(self, base_url: str, model: str, api_key: str, timeout: float = TIMEOUT):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._key = api_key
        self.timeout = timeout

    @classmethod
    def from_settings(cls, db: Session) -> "LlmClient | None":
        cfg = get_config(db)
        if cfg["provider"] == "off" or not cfg["key_enc"]:
            return None
        key = crypto.decrypt(cfg["key_enc"])
        if not key:
            return None
        return cls(cfg["base_url"], cfg["model"], key)

    def chat(self, system: str, user: str, timeout: float | None = None, max_tokens: int = 300) -> str:
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0,
            "max_tokens": max_tokens,
        }
        try:
            with httpx.Client(timeout=timeout or self.timeout, transport=TRANSPORT) as c:
                r = c.post(f"{self.base_url}/chat/completions", json=payload,
                           headers={"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"})
            r.raise_for_status()
            return str(r.json()["choices"][0]["message"]["content"])
        except httpx.HTTPStatusError as e:
            raise LlmError(f"the provider answered with status {e.response.status_code}") from None
        except httpx.TimeoutException:
            raise LlmError("the provider did not answer in time") from None
        except (httpx.HTTPError, KeyError, IndexError, ValueError, TypeError):
            raise LlmError("the provider could not be reached or sent an unexpected answer") from None

    def complete_json(self, system: str, user: str, timeout: float | None = None) -> dict[str, Any]:
        text = self.chat(system, user, timeout)
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise LlmError("the model did not return JSON")
        try:
            data = json.loads(m.group(0))
        except ValueError:
            raise LlmError("the model did not return valid JSON") from None
        if not isinstance(data, dict):
            raise LlmError("the model did not return a JSON object")
        return data


_L2_SYSTEM = (
    "You check one short message from a pet owner sent to a vet clinic after hours. Decide whether it describes "
    "a possible pet emergency that needs a vet right now. Answer only with JSON like {\"label\":\"emergency\"}, "
    "{\"label\":\"not\"} or {\"label\":\"unsure\"}. Use unsure when you cannot tell. Never answer anything else."
)


def make_layer2(db: Session):
    """Layer 2 emergency check, or None unless a provider is set and the toggle is on."""
    cfg = get_config(db)
    if not cfg.get("emergency_check"):
        return None
    client = LlmClient.from_settings(db)
    if client is None:
        return None

    def layer2(text: str) -> str:
        data = client.complete_json(_L2_SYSTEM, text[:500])
        label = str(data.get("label", "")).strip().lower()
        return label if label in ("emergency", "not", "unsure") else "unsure"

    return layer2
