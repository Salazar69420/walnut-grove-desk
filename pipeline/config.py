"""Path resolution. All paths derive from --root unless overridden."""
from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_ROOT = "/srv/walnut-private"
DEFAULT_LLM_ENV = "/var/lib/walnut-desk-llm/llm.env"
PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_RULES = os.path.join(PACKAGE_DIR, "rules.yaml")
TZ_NAME = "America/Vancouver"


@dataclass
class Paths:
    root: str
    db: str
    salt: str
    csv: str
    out_dir: str
    gold_dir: str


def resolve_paths(args) -> Paths:
    root = getattr(args, "root", None) or DEFAULT_ROOT
    g = lambda k: getattr(args, k, None)  # noqa: E731
    return Paths(
        root=root,
        db=g("db") or os.path.join(root, "private.db"),
        salt=g("salt") or os.path.join(root, "salt.bin"),
        csv=g("csv") or os.path.join(root, "inbox", "all_transcripts.csv"),
        out_dir=g("out_dir") or os.path.join(root, "out"),
        gold_dir=g("gold_dir") or os.path.join(root, "gold"),
    )
