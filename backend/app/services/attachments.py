"""Attachment storage: magic-byte checks, random stored names, owner-only files."""
from __future__ import annotations

import os
import re
import secrets
from pathlib import Path

from app import config

MAX_BYTES = 5 * 1024 * 1024
CHUNK = 64 * 1024


def sniff_mime(head: bytes) -> str | None:
    """The type is decided by the first bytes, never by the file name or the sent content type."""
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head.startswith(b"%PDF-"):
        return "application/pdf"
    return None


_BAD = re.compile(r"[\x00-\x1f\x7f\"<>|:*?\\/]")


def safe_display_name(name: str | None) -> str:
    """For display only: no folders, no control characters, bounded length. Never used as a path."""
    base = (name or "").replace("\\", "/").split("/")[-1]
    base = _BAD.sub("_", base).strip(" .")
    base = re.sub(r"\s+", " ", base)
    if not base:
        return "file"
    if len(base) > 120:
        stem, dot, ext = base.rpartition(".")
        ext = ext[:10] if dot else ""
        base = (stem[: 110 - len(ext)] + "." + ext) if dot else base[:120]
    return base


def root() -> Path:
    p = Path(config.get_settings().attach_dir)
    p.mkdir(mode=0o700, parents=True, exist_ok=True)
    return p


def new_stored_name() -> str:
    return secrets.token_hex(24)


def write_new(data: bytes) -> str:
    """Write bytes to a new random file (mode 600). Returns the stored name."""
    name = new_stored_name()
    fd = os.open(root() / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
    except BaseException:
        remove(name)
        raise
    return name


def path_for(stored_name: str) -> Path | None:
    if not re.fullmatch(r"[0-9a-f]{48}", stored_name or ""):
        return None
    return root() / stored_name


def remove(stored_name: str) -> None:
    """Overwrite with zeros, then delete. Best effort, never raises."""
    p = path_for(stored_name)
    if p is None:
        return
    try:
        size = p.stat().st_size
        with open(p, "r+b") as f:
            f.write(b"\x00" * size)
            f.flush()
            os.fsync(f.fileno())
    except OSError:
        pass
    try:
        p.unlink()
    except OSError:
        pass


def content_disposition(display_name: str) -> str:
    from urllib.parse import quote

    ascii_name = re.sub(r"[^A-Za-z0-9._ -]", "_", display_name) or "file"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(display_name, safe='')}"
