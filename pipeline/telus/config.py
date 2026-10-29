"""Credential loading for the TELUS connector. Values are never logged or put in error text."""
from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from urllib.parse import urlparse

from . import endpoints

CRED_REL = os.path.join("telus", "credentials.env")
KEYS = ("TELUS_ENV", "TELUS_SERVER_URL", "TELUS_CLIENT_ID", "TELUS_CLIENT_SECRET", "TELUS_JWT")
SECRET_KEYS = ("TELUS_CLIENT_ID", "TELUS_CLIENT_SECRET", "TELUS_JWT")
ENVS = ("sandbox", "production")


class TelusConfigError(Exception):
    """Configuration problem. The message never contains a credential value."""


def credentials_path(root: str) -> str:
    return os.path.join(root, CRED_REL)


@dataclass(frozen=True)
class Credentials:
    env: str
    server_url: str
    client_id: str
    client_secret: str
    jwt: str

    def __repr__(self) -> str:  # never show values
        return f"Credentials(env={self.env!r}, server_url={self.server_url!r}, <secrets hidden>)"

    __str__ = __repr__

    @property
    def host(self) -> str:
        return urlparse(self.server_url).hostname or ""

    def secret_values(self) -> list[str]:
        return [self.client_id, self.client_secret, self.jwt]


def validate_server_url(url: str) -> str:
    u = urlparse(url)
    if u.scheme != "https" or not u.hostname or u.username or u.password or u.query or u.fragment:
        raise TelusConfigError("TELUS_SERVER_URL must be a plain https URL with a host")
    if u.path not in ("", "/"):
        raise TelusConfigError("TELUS_SERVER_URL must not contain a path")
    return f"https://{u.netloc}"


def default_server_url(env: str) -> str:
    return endpoints.SERVER_URLS[env]


def check_value(key: str, value: str) -> str:
    """Reject empty values and anything that would not survive a one-line KEY=VALUE file."""
    if not value or value != value.strip():
        raise TelusConfigError(f"{key} is empty or has leading/trailing spaces")
    if re.search(r"[\x00-\x1f\x7f]", value):
        raise TelusConfigError(f"{key} contains control characters")
    return value


def parse_env_text(text: str) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
            v = v[1:-1]
        env[k.strip()] = v
    return env


def check_file_safety(path: str) -> None:
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        raise TelusConfigError("credentials file not found (run telus-setup)") from None
    if not stat.S_ISREG(st.st_mode):
        raise TelusConfigError("credentials path is not a regular file")
    if st.st_uid != os.getuid():
        raise TelusConfigError("credentials file is not owned by the current user")
    if stat.S_IMODE(st.st_mode) != 0o600:
        raise TelusConfigError(
            f"credentials file mode is {stat.S_IMODE(st.st_mode):o}, must be 600 (chmod 600 on it)")


def load_credentials(root: str) -> Credentials:
    path = credentials_path(root)
    check_file_safety(path)
    with open(path, "r", encoding="utf-8") as f:
        env = parse_env_text(f.read())
    missing = [k for k in KEYS if k in ("TELUS_ENV", "TELUS_CLIENT_ID", "TELUS_CLIENT_SECRET", "TELUS_JWT")
               and not env.get(k)]
    if missing:
        raise TelusConfigError("credentials file is missing: " + ", ".join(missing))
    tenv = env["TELUS_ENV"]
    if tenv not in ENVS:
        raise TelusConfigError("TELUS_ENV must be sandbox or production")
    url = validate_server_url(env.get("TELUS_SERVER_URL") or default_server_url(tenv))
    return Credentials(env=tenv, server_url=url, client_id=env["TELUS_CLIENT_ID"],
                       client_secret=env["TELUS_CLIENT_SECRET"], jwt=env["TELUS_JWT"])


def write_credentials(root: str, values: dict[str, str], force: bool = False) -> str:
    """Atomic write: umask 077, tmp file in the same directory, fsync, rename. Returns the path."""
    path = credentials_path(root)
    d = os.path.dirname(path)
    if os.path.lexists(path) and not force:
        raise TelusConfigError("credentials file already exists (use --force to replace it)")
    old = os.umask(0o077)
    try:
        os.makedirs(d, mode=0o700, exist_ok=True)
        tmp = os.path.join(d, f".credentials.{os.getpid()}.{os.urandom(4).hex()}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                for k in KEYS:
                    f.write(f"{k}={values[k]}\n")
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    finally:
        os.umask(old)
    return path
