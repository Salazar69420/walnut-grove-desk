"""Emergency keyword list: settings key `emergency_keywords` merged with config/emergency_keywords.yaml.

YAML format:
    status: draft            # placeholder | draft | approved
    groups:
      breathing: [trouble breathing, "re:\\bgasping\\b"]      # or {phrases: [...], heard_in: [...]}
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.orm import Session

from app import config
from app.aftercare.emergency import DEFAULT_KEYWORDS
from app.services import settings_store

KEY = "emergency_keywords"
STATUSES = ("placeholder", "draft", "approved")


def yaml_path() -> Path:
    return config.REPO_DIR / "config" / "emergency_keywords.yaml"


def _read_yaml(path: Path | None = None) -> dict[str, Any] | None:
    p = path or yaml_path()
    try:
        if not p.is_file():
            return None
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(data, dict):
        return None
    words: list[str] = []
    groups = data.get("groups") or {}
    if isinstance(groups, dict):
        for grp in groups.values():
            phrases = grp.get("phrases") if isinstance(grp, dict) else grp
            if isinstance(phrases, list):
                words.extend(str(x) for x in phrases if isinstance(x, (str, int)) and str(x).strip())
    status = data.get("status") if data.get("status") in STATUSES else "draft"
    return {"keywords": words, "status": status}


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for k in items:
        k = str(k).strip()
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out


def load_keywords_info(db: Session, path: Path | None = None) -> dict[str, Any]:
    stored = settings_store.get(db, KEY, None)
    if isinstance(stored, dict) and isinstance(stored.get("keywords"), list):
        words = [str(x) for x in stored["keywords"]]
        status = stored.get("status") if stored.get("status") in STATUSES else "placeholder"
    else:
        words, status = list(DEFAULT_KEYWORDS), "placeholder"
    y = _read_yaml(path)
    if y is not None:
        words = words + y["keywords"]
        # a YAML file the owner has approved wins; otherwise the weaker of the two states shows
        status = y["status"] if y["status"] == "approved" and status != "approved" else (
            "draft" if status == "placeholder" else status)
    return {"keywords": _dedupe(words), "status": status, "yaml": y is not None}


def load_keywords(db: Session, path: Path | None = None) -> list[str]:
    return load_keywords_info(db, path)["keywords"]
