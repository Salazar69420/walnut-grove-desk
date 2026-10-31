"""Admin: TELUS recordings button (docs/CONTRACT_TELUS_RECORDINGS.md).

This module only touches the small bridge folder (settings.telus_dir): it drops request files and
reads a counts-only status file written by the separate worker. It never reads credentials or
recordings, never imports anything from pipeline/, and treats everything it reads as untrusted.
"""
from __future__ import annotations

import json
import logging
import os
import re
import stat
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, field_validator
from sqlalchemy.orm import Session

from app import config
from app.api.schemas import Strict
from app.db import get_db
from app.models import User
from app.security.auth import require
from app.services import events as ev
from app.services import timeutil

router = APIRouter(prefix="/api/admin/telus", tags=["admin"])
log = logging.getLogger("walnut.telus")

MAX_READ = 64 * 1024
STALE_AFTER = timedelta(hours=24)
JOB_ID_RE = re.compile(r"^[0-9a-f]{32}$")
REQUEST_FILE_RE = re.compile(r"^[0-9a-f]{32}\.json$")
MAX_PENDING_SCAN = 200

Code = Literal["ok", "partial", "recordings_disabled", "not_configured", "login_failed", "write_scope_present",
               "disk_cap_reached", "disk_low", "rate_limited", "no_recordings", "error"]

MESSAGES: dict[str, str] = {
    "ok": "All good.",
    "partial": "Some recordings could not be saved. The rest were saved. You can run it again.",
    "recordings_disabled": "Downloading recordings is turned off. Turn it on and save first.",
    "not_configured": "The TELUS login is not set up on the server yet.",
    "login_failed": "TELUS did not accept the login. Ask the person who looks after the server to check it.",
    "write_scope_present": "The TELUS login can change things, so downloading was stopped. It must be read only.",
    "disk_cap_reached": "The storage limit was reached, so downloading stopped. Raise the limit or wait for old recordings to expire.",
    "disk_low": "The server is running low on disk space, so downloading stopped.",
    "rate_limited": "TELUS asked us to slow down. Try again in a few minutes.",
    "no_recordings": "There were no new recordings to download.",
    "error": "Something went wrong. Try again. If it keeps happening, tell the person who looks after the server.",
}

DEFAULTS: dict[str, Any] = {"recordings_enabled": False, "max_gb": 10, "keep_days": 30, "max_per_run_cap": 200}


# ---------------------------------------------------------------- schemas
def _iso(v: str | None) -> str | None:
    if v is None:
        return None
    if len(v) > 40:
        raise ValueError("too long")
    datetime.fromisoformat(v.replace("Z", "+00:00"))
    return v


def _name(v: str | None) -> str | None:
    if v is None:
        return None
    return "".join(c for c in v if c.isprintable())[:60]


class _Untrusted(BaseModel):
    """Parses files written by another program. Unknown keys are ignored, wrong types are errors."""
    model_config = ConfigDict(extra="ignore", strict=True)


class StoredConfig(_Untrusted):
    v: Literal[1]
    recordings_enabled: StrictBool
    max_gb: StrictInt = Field(ge=1, le=50)
    keep_days: StrictInt = Field(ge=1, le=90)
    max_per_run_cap: StrictInt = Field(ge=1, le=1000)
    updated_by: StrictStr | None = None
    updated_at: StrictStr | None = None

    @field_validator("updated_by")
    @classmethod
    def _clean_by(cls, v):
        return _name(v)

    @field_validator("updated_at")
    @classmethod
    def _check_at(cls, v):
        return _iso(v)


def _count():
    return Field(ge=0, le=10**13)


class Connection(_Untrusted):
    state: Literal["unknown", "ok", "failed"]
    code: Code
    checked_at: StrictStr | None = None
    environment: Literal["production", "sandbox"] | None = None
    write_capable_scopes: StrictBool = False
    calls_last_7_days: StrictInt | None = Field(default=None, ge=0, le=10**9)

    @field_validator("checked_at")
    @classmethod
    def _check_at(cls, v):
        return _iso(v)


class Job(_Untrusted):
    id: StrictStr | None = Field(default=None, pattern=JOB_ID_RE.pattern)
    mode: Literal["check", "recordings"] | None = None
    state: Literal["idle", "queued", "running", "done", "failed"]
    requested_by: StrictStr | None = None
    requested_at: StrictStr | None = None
    started_at: StrictStr | None = None
    finished_at: StrictStr | None = None
    code: Code
    found: StrictInt = _count()
    downloaded: StrictInt = _count()
    already_had: StrictInt = _count()
    skipped: StrictInt = _count()
    failed: StrictInt = _count()
    remaining: StrictInt = _count()
    deleted_old: StrictInt = _count()

    @field_validator("requested_by")
    @classmethod
    def _clean_by(cls, v):
        return _name(v)

    @field_validator("requested_at", "started_at", "finished_at")
    @classmethod
    def _check_at(cls, v):
        return _iso(v)


class Storage(_Untrusted):
    recordings_count: StrictInt = _count()
    bytes: StrictInt = _count()
    free_bytes: StrictInt = _count()
    max_bytes: StrictInt = _count()


class StoredStatus(_Untrusted):
    v: Literal[1]
    updated_at: StrictStr
    connection: Connection
    job: Job
    storage: Storage
    rejected_requests: StrictInt = _count()

    @field_validator("updated_at")
    @classmethod
    def _check_at(cls, v):
        return _iso(v)


class StrictTypes(Strict):
    model_config = ConfigDict(extra="forbid", strict=True)


class SettingsBody(StrictTypes):
    recordings_enabled: bool
    max_gb: int = Field(ge=1, le=50)
    keep_days: int = Field(ge=1, le=90)
    max_per_run_cap: int = Field(ge=1, le=1000)


class RecordingsBody(StrictTypes):
    days: int = Field(ge=1, le=30)
    max_items: int = Field(ge=1, le=1000)


# ---------------------------------------------------------------- bridge reading
def _bridge() -> str:
    return config.get_settings().telus_dir


def _real_dir(path: str) -> bool:
    try:
        return stat.S_ISDIR(os.lstat(path).st_mode)  # lstat: a symlink is not a directory here
    except OSError:
        return False


def bridge_ok() -> bool:
    b = _bridge()
    return _real_dir(b) and os.access(b, os.R_OK | os.W_OK | os.X_OK)


def read_json_file(directory: str, name: str) -> dict[str, Any] | None:
    """Bounded, no-symlink read of a small JSON object. Returns None for anything unexpected. Never raises."""
    try:
        if not _real_dir(directory):
            return None
        path = os.path.join(directory, name)
        st = os.lstat(path)
        if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_READ:
            return None
        flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
        fd = os.open(path, flags)
        try:
            st2 = os.fstat(fd)
            if not stat.S_ISREG(st2.st_mode) or st2.st_size > MAX_READ:
                return None
            chunks: list[bytes] = []
            total = 0
            while total <= MAX_READ:
                part = os.read(fd, MAX_READ + 1 - total)
                if not part:
                    break
                chunks.append(part)
                total += len(part)
            if total > MAX_READ:
                return None
        finally:
            os.close(fd)
        data = json.loads(b"".join(chunks).decode("utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001  (untrusted input must never raise out of here)
        return None


def read_settings() -> dict[str, Any]:
    raw = read_json_file(_bridge(), "config.json")
    if raw is not None:
        try:
            return StoredConfig.model_validate(raw).model_dump(exclude={"v"})
        except Exception:  # noqa: BLE001
            pass
    return {**DEFAULTS, "updated_by": None, "updated_at": None}


def read_status() -> dict[str, Any] | None:
    raw = read_json_file(_bridge(), "status.json")
    if raw is None:
        return None
    try:
        st = StoredStatus.model_validate(raw).model_dump()
    except Exception:  # noqa: BLE001
        return None
    st.pop("v", None)
    try:
        age = timeutil.now_utc() - datetime.fromisoformat(st["updated_at"].replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:  # noqa: BLE001  (naive or odd timestamp)
        return None
    if st["job"]["state"] in ("queued", "running") and age > STALE_AFTER:
        st["job"]["state"] = "failed"
        st["job"]["code"] = "error"
    return st


def pending_requests() -> list[str]:
    """Names (job ids) of request files waiting for the worker. Files older than a day are ignored."""
    req = os.path.join(_bridge(), "requests")
    out: list[str] = []
    try:
        if not _real_dir(req):
            return out
        cutoff = timeutil.now_utc().timestamp() - STALE_AFTER.total_seconds()
        with os.scandir(req) as it:
            for i, e in enumerate(it):
                if i >= MAX_PENDING_SCAN:
                    break
                if not REQUEST_FILE_RE.match(e.name):
                    continue
                st = e.stat(follow_symlinks=False)
                if stat.S_ISREG(st.st_mode) and st.st_mtime >= cutoff:
                    out.append(e.name[:-5])
    except OSError:
        return out
    return out


def _active(status: dict[str, Any] | None, pending: list[str]) -> bool:
    return bool(pending) or (status is not None and status["job"]["state"] in ("queued", "running"))


def _overlay_queued(status: dict[str, Any] | None, pending: list[str]) -> dict[str, Any] | None:
    """Show 'queued' in the response when a request file is waiting. status.json itself is never written here."""
    if status is None or not pending or status["job"]["state"] in ("queued", "running"):
        return status
    status["job"] = {**status["job"], "state": "queued", "id": pending[0], "mode": None, "code": "ok",
                     "started_at": None, "finished_at": None}
    return status


# ---------------------------------------------------------------- bridge writing
def _atomic_write(directory: str, final_path: str, body: dict[str, Any]) -> None:
    tmp = os.path.join(directory, f".tmp-{uuid.uuid4().hex}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(tmp, flags, 0o660)
    try:
        try:
            os.fchmod(fd, 0o660)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                fd = -1
                f.write(json.dumps(body, separators=(",", ":")))
                f.flush()
                os.fsync(f.fileno())
        finally:
            if fd != -1:
                os.close(fd)
        os.replace(tmp, final_path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _need_bridge() -> None:
    if not bridge_ok():
        raise HTTPException(status_code=503, detail="The download helper is not set up on this server yet.")


def _now_iso() -> str:
    return timeutil.now_utc().astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _who(user: User) -> str:
    return (_name(user.display_name) or "")[:60] or "Admin"


def _queue(mode: str, days: int, max_items: int, user: User) -> str:
    _need_bridge()
    if _active(read_status(), pending_requests()):
        raise HTTPException(status_code=409, detail="A job is already waiting or running. Wait for it to finish.")
    req_dir = os.path.join(_bridge(), "requests")
    try:
        if not _real_dir(req_dir):
            os.mkdir(req_dir, 0o770)
            os.chmod(req_dir, 0o770)
        job_id = uuid.uuid4().hex
        body = {"v": 1, "id": job_id, "mode": mode, "days": days, "max_items": max_items,
                "requested_by": _who(user), "requested_at": _now_iso()}
        # Written next to (not inside) requests/ and renamed in, so the worker never sees a half-written file.
        _atomic_write(_bridge(), os.path.join(req_dir, job_id + ".json"), body)
    except OSError as e:
        log.warning("could not write a telus request: %s", e.__class__.__name__)
        raise HTTPException(status_code=503, detail="Could not hand the job to the download helper. Try again.")
    return job_id


# ---------------------------------------------------------------- routes
@router.get("")
def get_telus(user: User = Depends(require("admin.telus"))):
    ok = bridge_ok()
    pending = pending_requests() if ok else []
    status = read_status() if ok else None
    busy = _active(status, pending)
    return {"settings": read_settings() if ok else {**DEFAULTS, "updated_by": None, "updated_at": None},
            "status": _overlay_queued(status, pending), "bridge_ok": ok, "busy": busy, "messages": MESSAGES}


@router.put("/settings")
def put_settings(body: SettingsBody, actor: User = Depends(require("admin.telus")), db: Session = Depends(get_db)):
    _need_bridge()
    cfg = {"v": 1, **body.model_dump(), "updated_by": _who(actor), "updated_at": _now_iso()}
    try:
        _atomic_write(_bridge(), os.path.join(_bridge(), "config.json"), cfg)
    except OSError as e:
        log.warning("could not write telus config: %s", e.__class__.__name__)
        raise HTTPException(status_code=503, detail="Could not save the settings. Try again.")
    ev.record(db, ev.ADMIN, "telus_settings_changed",
              f"{actor.display_name} changed the TELUS settings (recordings {'on' if body.recordings_enabled else 'off'}, "
              f"{body.max_gb} GB limit, keep {body.keep_days} days, up to {body.max_per_run_cap} per click)",
              actor, "telus", "settings", {"keys": sorted(body.model_dump())})
    db.commit()
    return {"settings": read_settings()}


@router.post("/check")
def post_check(actor: User = Depends(require("admin.telus")), db: Session = Depends(get_db)):
    job_id = _queue("check", 7, 1, actor)
    ev.record(db, ev.ADMIN, "telus_check_requested", f"{actor.display_name} asked to check the TELUS connection",
              actor, "telus", job_id, {})
    db.commit()
    return {"job_id": job_id}


@router.post("/recordings")
def post_recordings(body: RecordingsBody, actor: User = Depends(require("admin.telus")), db: Session = Depends(get_db)):
    _need_bridge()
    if not read_settings()["recordings_enabled"]:
        raise HTTPException(status_code=400, detail="Downloading recordings is turned off. Turn it on and save first.")
    job_id = _queue("recordings", body.days, body.max_items, actor)
    ev.record(db, ev.ADMIN, "telus_recordings_requested",
              f"{actor.display_name} asked to download new TELUS recordings (last {body.days} days, up to {body.max_items})",
              actor, "telus", job_id, {"days": body.days, "max_items": body.max_items})
    db.commit()
    return {"job_id": job_id}
