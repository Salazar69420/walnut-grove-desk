"""Runtime configuration, read from environment variables (see docs/CONTRACT.md)."""
from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent

PRODUCTION = "production"
DEV = "dev"
TEST = "test"


@dataclass(frozen=True)
class Settings:
    env: str = DEV
    secret_key: str = "dev-secret-key-not-for-production-use-0123456789"
    fernet_key: str = ""
    db_path: str = str(BACKEND_DIR / "walnut-dev.db")
    llm_env_path: str = "/var/lib/walnut-desk-llm/llm.env"
    import_dir: str = "/var/lib/walnut-desk/imports"
    public_url: str = "http://127.0.0.1:8000"
    frontend_dist: str = str(REPO_DIR / "frontend" / "dist")
    skip_migrate: bool = False

    @property
    def is_production(self) -> bool:
        return self.env == PRODUCTION

    @property
    def cookie_name(self) -> str:
        return "__Host-wgd" if self.is_production else "wgd"

    @property
    def cookie_secure(self) -> bool:
        return self.is_production

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_path}"


def load_settings() -> Settings:
    env = os.environ.get("WALNUT_ENV", DEV).strip().lower()
    if env not in (PRODUCTION, DEV, TEST):
        raise RuntimeError(f"WALNUT_ENV must be production, dev or test, got {env!r}")
    base = Settings()
    s = replace(
        base,
        env=env,
        secret_key=os.environ.get("WALNUT_SECRET_KEY", base.secret_key),
        fernet_key=os.environ.get("WALNUT_FERNET_KEY", base.fernet_key),
        db_path=os.environ.get("WALNUT_DB_PATH", base.db_path),
        llm_env_path=os.environ.get("WALNUT_LLM_ENV_PATH", base.llm_env_path),
        import_dir=os.environ.get("WALNUT_IMPORT_DIR", base.import_dir),
        public_url=os.environ.get("WALNUT_PUBLIC_URL", base.public_url),
        frontend_dist=os.environ.get("WALNUT_FRONTEND_DIST", base.frontend_dist),
        skip_migrate=os.environ.get("WALNUT_SKIP_MIGRATE", "") == "1",
    )
    if s.is_production:
        if "WALNUT_SECRET_KEY" not in os.environ or len(s.secret_key) < 32:
            raise RuntimeError("WALNUT_SECRET_KEY (32+ chars) is required in production")
        if not os.environ.get("WALNUT_DB_PATH"):
            raise RuntimeError("WALNUT_DB_PATH is required in production")
    return s


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings


def set_settings(s: Settings | None) -> None:
    """Replace (or clear) the cached settings. Used by tests."""
    global _settings
    _settings = s
