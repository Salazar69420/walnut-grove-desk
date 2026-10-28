"""Programmatic `alembic upgrade head`. Called at app startup and by the seed CLI."""
from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

from app import config

BACKEND_DIR = Path(__file__).resolve().parent.parent


def alembic_config(db_path: str | None = None) -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    path = db_path or config.get_settings().db_path
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
    cfg.attributes["skip_logging"] = True
    return cfg


def upgrade_head(db_path: str | None = None) -> None:
    command.upgrade(alembic_config(db_path), "head")
