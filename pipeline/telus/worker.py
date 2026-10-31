"""TELUS worker: processes request files dropped by the web app in the bridge folder.

Bridge layout (see docs/CONTRACT_TELUS_RECORDINGS.md):
    requests/<job_id>.json   written by the app, read and deleted here
    config.json              written by the app (settings), read here
    status.json              written here atomically, read by the app. Counts and fixed codes only.

Everything read from the bridge is untrusted and validated strictly. Nothing derived from TELUS (ids, numbers,
names, exception text) is ever written to status.json or to logs; only counts and the fixed codes below.
"""
from __future__ import annotations

import errno
import fcntl
import json
import logging
import os
import re
import stat
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .. import db as dbm
from ..load import load_salt
from . import calllog, check, recordings, sync
from . import config as tcfg
from .client import (ReadOnlyClient, ReadOnlyViolation, TelusAuthError, TelusDownloadError, TelusError,
                     TelusHTTPError)

log = logging.getLogger("pipeline.telus.worker")

DEFAULT_BRIDGE = "/var/lib/walnut-desk-telus"
GB = 1024 ** 3                       # module constant so tests can shrink the disk cap
LOW_DISK_BYTES = 5 * 1024 ** 3       # stop when free space on the volume would fall under this
MAX_FILE_BYTES = recordings.MAX_FILE_BYTES
REQUEST_MAX_BYTES = 4096
READ_MAX_BYTES = 64 * 1024
MAX_ENTRIES = 500                    # directory entries looked at per run
MAX_JOBS_PER_RUN = 20
GRACE_SECONDS = 2.0                  # a half-written request younger than this gets one more read
PROGRESS_EVERY = 10
FILE_SLEEP = 1.0                     # polite pause between recordings
MAX_CONSECUTIVE_FAILURES = 10
PLAY_DIR = "play"                    # inside the bridge folder: short-lived audio the app streams to owners
PLAY_TTL = 3600                      # seconds a played recording stays in the bridge before it is deleted
PLAY_CODES = ("ready", "recordings_disabled", "not_configured", "login_failed", "write_scope_present",
              "disk_cap_reached", "disk_low", "rate_limited", "not_found", "error")
_PLAY_FILE = re.compile(r"^\.?[0-9]{1,24}\.[A-Za-z0-9._-]{1,30}$")

CODES = ("ok", "partial", "recordings_disabled", "not_configured", "login_failed", "write_scope_present",
         "disk_cap_reached", "disk_low", "rate_limited", "no_recordings", "error")
DONE_CODES = ("ok", "partial", "no_recordings")
_NAME_RE = r"^[0-9a-f]{32}\.json$"
_ID_PATTERN = r"^[0-9a-f]{32}$"

_NAME = re.compile(_NAME_RE)


# ------------------------------------------------------------------ models (everything crossing the bridge)
def _iso(text: str) -> str:
    try:
        dt = datetime.fromisoformat(text.strip())
    except ValueError:
        raise ValueError("not an ISO 8601 time") from None
    if dt.tzinfo is None:
        raise ValueError("time needs a UTC offset")
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    v: int
    id: str = Field(pattern=_ID_PATTERN)
    mode: Literal["check", "recordings", "sync", "play"]
    days: Optional[int] = Field(default=None, ge=1, le=30)
    max_items: Optional[int] = Field(default=None, ge=1, le=1000)
    recording_id: Optional[str] = Field(default=None, pattern=r"^[0-9]{1,24}$")  # play only
    call_date: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")  # play only
    requested_by: str = Field(min_length=1, max_length=60)
    requested_at: str = Field(min_length=10, max_length=40)

    @field_validator("v")
    @classmethod
    def _version(cls, v: int) -> int:
        if v != 1:
            raise ValueError("unsupported version")
        return v

    @field_validator("requested_by")
    @classmethod
    def _printable(cls, v: str) -> str:
        if not v.isprintable() or not v.strip():
            raise ValueError("requested_by must be printable text")
        return v

    @field_validator("requested_at")
    @classmethod
    def _when(cls, v: str) -> str:
        return _iso(v)

    @model_validator(mode="after")
    def _need_numbers(self):
        if self.mode == "recordings" and (self.days is None or self.max_items is None):
            raise ValueError("recordings needs days and max_items")
        if self.mode == "play":
            if self.recording_id is None or self.call_date is None or self.days is not None or self.max_items is not None:
                raise ValueError("play needs recording_id and call_date only")
        elif self.recording_id is not None or self.call_date is not None:
            raise ValueError("recording_id and call_date are only for play")
        return self


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    v: int
    recordings_enabled: bool
    max_gb: int = Field(ge=1, le=50)
    keep_days: int = Field(ge=1, le=90)
    max_per_run_cap: int = Field(ge=1, le=1000)
    updated_by: Optional[str] = Field(default=None, max_length=60)
    updated_at: Optional[str] = Field(default=None, max_length=40)

    @field_validator("v")
    @classmethod
    def _version(cls, v: int) -> int:
        if v != 1:
            raise ValueError("unsupported version")
        return v


DEFAULT_SETTINGS = Settings(v=1, recordings_enabled=False, max_gb=10, keep_days=30, max_per_run_cap=200)


class _Job(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: Optional[str] = Field(default=None, pattern=_ID_PATTERN)
    mode: Optional[Literal["check", "recordings", "sync"]] = None
    state: Literal["idle", "queued", "running", "done", "failed"]
    requested_by: Optional[str] = Field(default=None, max_length=60)
    requested_at: Optional[str] = Field(default=None, max_length=40)
    started_at: Optional[str] = Field(default=None, max_length=40)
    finished_at: Optional[str] = Field(default=None, max_length=40)
    code: Literal[CODES]  # type: ignore[valid-type]
    found: int = Field(ge=0)
    downloaded: int = Field(ge=0)
    already_had: int = Field(ge=0)
    skipped: int = Field(ge=0)
    failed: int = Field(ge=0)
    remaining: int = Field(ge=0)
    deleted_old: int = Field(ge=0)


class _Sync(BaseModel):
    """status.json `sync` (live Brief numbers). Absent until the first sync has run; the app tolerates that."""
    model_config = ConfigDict(extra="forbid", strict=True)
    state: Literal["ok", "failed"]
    code: Literal[CODES]  # type: ignore[valid-type]
    finished_at: Optional[str] = Field(default=None, max_length=40)
    rows: int = Field(ge=0, le=sync.MAX_ROWS)


class _Conn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    state: Literal["unknown", "ok", "failed"]
    code: Literal[CODES]  # type: ignore[valid-type]
    checked_at: Optional[str] = Field(default=None, max_length=40)
    environment: Optional[Literal["production", "sandbox"]] = None
    write_capable_scopes: bool
    calls_last_7_days: Optional[int] = Field(default=None, ge=0)


# ------------------------------------------------------------------ safe file reading
def read_bounded(path: str, limit: int) -> bytes | None:
    """Contents of a regular file of at most `limit` bytes, never following a symlink. None otherwise."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > limit:
            return None
        data = os.read(fd, limit + 1)
        return data if len(data) <= limit else None
    except OSError:
        return None
    finally:
        os.close(fd)


def load_settings(bridge: str) -> Settings:
    data = read_bounded(os.path.join(bridge, "config.json"), REQUEST_MAX_BYTES)
    if data is None:
        return DEFAULT_SETTINGS
    try:
        return Settings.model_validate_json(data)
    except (ValidationError, ValueError):
        return DEFAULT_SETTINGS


def _parse_request(data: bytes | None, stem: str) -> Request | None:
    if data is None:
        return None
    try:
        req = Request.model_validate_json(data)
    except (ValidationError, ValueError):
        return None
    return req if req.id == stem else None


@dataclass
class Job:
    path: str
    mtime_ns: int
    req: Request


def collect_requests(bridge: str, sleep: Callable[[float], None]) -> tuple[list[Job], int]:
    """Valid jobs oldest first, and the number of bad entries (each one is deleted). Bad means: name not
    32 hex plus .json, not a regular file (symlinks included), over 4096 bytes, or failing the schema."""
    rdir = os.path.join(bridge, "requests")
    try:
        if not stat.S_ISDIR(os.lstat(rdir).st_mode):
            return [], 0
        with os.scandir(rdir) as it:
            entries = [e for _, e in zip(range(MAX_ENTRIES), it)]
    except OSError:
        return [], 0
    jobs: list[Job] = []
    rejected = 0
    for e in sorted(entries, key=lambda x: x.name):
        try:
            st = os.lstat(e.path)
        except OSError:
            continue
        ok_shape = (_NAME.fullmatch(e.name) is not None and stat.S_ISREG(st.st_mode)
                    and st.st_size <= REQUEST_MAX_BYTES)
        req = None
        if ok_shape:
            stem = e.name[:-5]
            req = _parse_request(read_bounded(e.path, REQUEST_MAX_BYTES), stem)
            if req is None and time.time() - st.st_mtime < GRACE_SECONDS:
                sleep(GRACE_SECONDS)  # the app may still be writing it; look once more
                req = _parse_request(read_bounded(e.path, REQUEST_MAX_BYTES), stem)
        if req is None:
            rejected += 1
            _discard(e.path, st)
            continue
        jobs.append(Job(e.path, st.st_mtime_ns, req))
    jobs.sort(key=lambda j: (j.mtime_ns, j.req.id))
    return jobs, rejected


def _discard(path: str, st: os.stat_result) -> None:
    try:
        if stat.S_ISDIR(st.st_mode):
            os.rmdir(path)
        else:
            os.unlink(path)  # removes a symlink itself, never its target
    except OSError:
        pass


# ------------------------------------------------------------------ status.json
def _zero_job() -> dict:
    return {"id": None, "mode": None, "state": "idle", "requested_by": None, "requested_at": None,
            "started_at": None, "finished_at": None, "code": "ok", "found": 0, "downloaded": 0,
            "already_had": 0, "skipped": 0, "failed": 0, "remaining": 0, "deleted_old": 0}


def _zero_conn() -> dict:
    return {"state": "unknown", "code": "ok", "checked_at": None, "environment": None,
            "write_capable_scopes": False, "calls_last_7_days": None}


def new_status() -> dict:
    return {"v": 1, "updated_at": None, "connection": _zero_conn(), "job": _zero_job(),
            "storage": {"recordings_count": 0, "bytes": 0, "free_bytes": 0, "max_bytes": 0},
            "rejected_requests": 0}


def load_previous(bridge: str) -> dict:
    """Carry over the last connection result, last job and the rejected counter. A job that was still
    queued or running (a worker that died) is shown as failed. Storage is recomputed on every write."""
    st = new_status()
    data = read_bounded(os.path.join(bridge, "status.json"), READ_MAX_BYTES)
    if data is None:
        return st
    try:
        old = json.loads(data)
        conn = _Conn.model_validate(old["connection"])
        st["connection"] = conn.model_dump()
        try:
            job = _Job.model_validate(old["job"]).model_dump()
            if job["state"] in ("queued", "running"):
                job.update(state="failed", code="error")
            st["job"] = job
        except (KeyError, TypeError, ValidationError):
            pass
        try:
            st["sync"] = _Sync.model_validate(old["sync"]).model_dump()
        except (KeyError, TypeError, ValidationError):
            pass
        n = old.get("rejected_requests")
        if isinstance(n, int) and not isinstance(n, bool) and 0 <= n < 10 ** 9:
            st["rejected_requests"] = n
    except (ValueError, KeyError, TypeError, ValidationError):
        pass
    return st


def write_status(bridge: str, status: dict) -> None:
    """Atomic: temp file in the bridge, fsync, mode 660, rename over status.json, fsync the folder."""
    final = os.path.join(bridge, "status.json")
    tmp = os.path.join(bridge, f".status.{os.getpid()}.{os.urandom(4).hex()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o660)
    try:
        os.fchmod(fd, 0o660)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(status, f, separators=(",", ":"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, final)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    recordings._fsync_dir(bridge)


def free_bytes(path: str) -> int:
    try:
        import shutil
        return shutil.disk_usage(path).free
    except OSError:
        return 0


def _default_factory(creds, sleep):
    return ReadOnlyClient(creds, sleep=sleep)


def _code_for(e: BaseException) -> str:
    if isinstance(e, TelusAuthError):
        return "login_failed"
    if isinstance(e, TelusHTTPError) and e.status == 429:
        return "rate_limited"
    if isinstance(e, TelusHTTPError) and e.status == 401:
        return "login_failed"
    return "error"


# ------------------------------------------------------------------ the worker
class Worker:
    def __init__(self, root: str, bridge: str, db_path: str, salt_path: str, *,
                 sleep: Callable[[float], None] | None = None,
                 now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 client_factory: Callable = _default_factory, file_sleep: float | None = None):
        self.root, self.bridge, self.db_path, self.salt_path = root, bridge, db_path, salt_path
        self.sleep = sleep or (lambda s: time.sleep(s))
        self.now, self.client_factory = now, client_factory
        self.file_sleep = FILE_SLEEP if file_sleep is None else file_sleep
        self.status = load_previous(bridge)
        self.settings = DEFAULT_SETTINGS
        self.status_ok = True

    # -------------------------------------------------------------- status plumbing
    def _t(self) -> str:
        return recordings.iso_z(self.now())

    def flush(self) -> None:
        self.status["updated_at"] = self._t()
        self._refresh_storage()
        try:
            write_status(self.bridge, self.status)
        except OSError as e:
            self.status_ok = False
            log.error("could not write status (%s)", type(e).__name__)

    def _refresh_storage(self) -> None:
        count, total = recordings.scan_storage(self.root) if os.path.isdir(self.root) else (0, 0)
        self.status["storage"] = {"recordings_count": count, "bytes": total,
                                  "free_bytes": free_bytes(self.root) if os.path.isdir(self.root) else 0,
                                  "max_bytes": self.settings.max_gb * GB}

    def _conn(self, state: str, code: str, *, scopes_bad: bool = False, calls="keep", env=None) -> None:
        c = self.status["connection"]
        c.update(state=state, code=code, checked_at=self._t(), write_capable_scopes=scopes_bad)
        if env is not None:
            c["environment"] = env
        if calls != "keep":
            c["calls_last_7_days"] = calls

    # -------------------------------------------------------------- run
    def run(self) -> int:
        if not os.path.isdir(self.bridge) or os.path.islink(self.bridge):
            print("error: bridge folder not found", file=sys.stderr)
            return 2
        lock = self._lock()
        if lock is None:
            print("telus-worker busy, exiting")
            return 0
        try:
            self.settings = load_settings(self.bridge)
            jobs, rejected = collect_requests(self.bridge, self.sleep)
            self.status["rejected_requests"] = min(self.status["rejected_requests"] + rejected, 10 ** 9 - 1)
            jobs.sort(key=lambda j: j.req.mode != "play")  # a person waiting to hear a call goes first (stable sort)
            jobs = jobs[:MAX_JOBS_PER_RUN]
            self._play_cleanup()
            if not jobs:
                if rejected:
                    self.flush()
                print(f"telus-worker jobs=0 rejected={rejected}")
                return 0 if self.status_ok else 1
            for job in jobs:
                self.settings = load_settings(self.bridge)
                if job.req.mode == "play":
                    self._play_job(job.req)  # never touches status.json: it carries no recording ids
                    try:
                        os.unlink(job.path)
                    except OSError:
                        pass
                    continue
                self._queue(job.req)
                self.flush()
                self._run_job(job.req)
                try:
                    os.unlink(job.path)
                except OSError:
                    pass
                self.flush()
            print(f"telus-worker jobs={len(jobs)} rejected={rejected}")
            return 0 if self.status_ok else 1
        finally:
            os.close(lock)

    def _lock(self):
        try:
            d = os.path.join(self.root, "telus")
            os.makedirs(d, mode=0o700, exist_ok=True)
            fd = os.open(os.path.join(d, ".worker.lock"), os.O_RDWR | os.O_CREAT, 0o600)
        except OSError:
            return None
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return None
        return fd

    def _queue(self, req: Request) -> None:
        j = _zero_job()
        j.update(id=req.id, mode=req.mode, state="queued", requested_by=req.requested_by,
                 requested_at=req.requested_at)
        self.status["job"] = j

    def _run_job(self, req: Request) -> None:
        job = self.status["job"]
        job.update(state="running", started_at=self._t())
        self.flush()
        try:
            if req.mode == "check":
                code = self._check(req)
            elif req.mode == "sync":
                code = self._sync(req.days)
            else:
                code = self._recordings(req)
        except Exception as e:  # never let text from TELUS or the system reach status or logs
            log.error("telus worker job failed (%s)", type(e).__name__)
            code = "error"
        job["code"] = code
        job["finished_at"] = self._t()
        done = code in DONE_CODES or (job["downloaded"] > 0 and code in ("disk_cap_reached", "disk_low",
                                                                         "rate_limited"))
        if req.mode == "check":
            done = self.status["connection"]["state"] == "ok"
        elif req.mode == "sync":
            done = code == "ok"
        job["state"] = "done" if done else "failed"

    # -------------------------------------------------------------- shared: log in and check scopes
    def _session(self, stack):
        """(client, None) when logged in with a read-only token, else (None, code)."""
        try:
            creds = tcfg.load_credentials(self.root)
        except tcfg.TelusConfigError:
            self._conn("failed", "not_configured")
            return None, "not_configured"
        env = creds.env
        client = stack.enter_context(self.client_factory(creds, self.sleep))
        try:
            client.login()
        except TelusError as e:
            code = _code_for(e)
            self._conn("failed", "login_failed" if code == "login_failed" else "error", env=env)
            return None, self.status["connection"]["code"]
        if check.write_capable_scopes(client.scopes):
            self._conn("failed", "write_scope_present", scopes_bad=True, env=env)
            return None, "write_scope_present"
        if any(s not in client.scopes for s in check_required()):
            self._conn("failed", "error", env=env)
            return None, "error"
        self._conn("ok", "ok", env=env)
        return client, None

    # -------------------------------------------------------------- mode: play (one recording for an owner to hear)
    def _play_dir(self) -> str:
        d = os.path.join(self.bridge, PLAY_DIR)
        try:
            os.mkdir(d, 0o770)
        except FileExistsError:
            pass
        os.chmod(d, 0o2770)
        return d

    def _play_cleanup(self) -> None:
        try:
            d = os.path.join(self.bridge, PLAY_DIR)
            with os.scandir(d) as it:
                entries = [e for _, e in zip(range(MAX_ENTRIES), it)]
        except OSError:
            return
        cutoff = time.time() - PLAY_TTL
        for e in entries:
            try:
                st = os.lstat(e.path)
                if stat.S_ISREG(st.st_mode) and _PLAY_FILE.fullmatch(e.name) and st.st_mtime < cutoff:
                    recordings.secure_delete(e.path)
            except OSError:
                continue

    def _play_result(self, rid: str, code: str, ext: str = "", nbytes: int = 0) -> None:
        d = self._play_dir()
        body = {"v": 1, "state": "ready" if code == "ready" else "failed", "code": code if code in PLAY_CODES else "error",
                "ext": ext, "bytes": nbytes, "at": self._t()}
        tmp = os.path.join(d, f".{rid}.{os.getpid()}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o660)
        with os.fdopen(fd, "w") as fh:
            json.dump(body, fh, separators=(",", ":"))
        os.chmod(tmp, 0o660)
        os.replace(tmp, os.path.join(d, f"{rid}.json"))

    def _find_stored(self, rid: str) -> str | None:
        """The recording in the private store, if an earlier download left it there."""
        base = recordings.rec_root(self.root)
        try:
            for y in os.scandir(base):
                if not y.is_dir(follow_symlinks=False):
                    continue
                for m in os.scandir(y.path):
                    if not m.is_dir(follow_symlinks=False):
                        continue
                    for f in os.scandir(m.path):
                        if f.is_file(follow_symlinks=False) and f.name.startswith(rid + ".") and recordings._STEM_RE.fullmatch(f.name):
                            return f.path
        except OSError:
            return None
        return None

    def _play_job(self, req: Request) -> None:
        rid = req.recording_id
        ext, nbytes = "", 0
        try:
            code, ext, nbytes = self._play(req)
        except Exception as e:  # nothing from TELUS or the system may reach the result file or the logs
            log.error("telus play failed (%s)", type(e).__name__)
            code = "error"
        try:
            self._play_result(rid, code, ext, nbytes)
        except OSError as e:
            log.error("telus play result not written (%s)", type(e).__name__)

    def _play(self, req: Request) -> tuple[str, str, int]:
        from contextlib import ExitStack
        rid, s = req.recording_id, self.settings
        if not s.recordings_enabled:
            return "recordings_disabled", "", 0
        src = self._find_stored(rid)
        if src is None:
            with ExitStack() as stack:
                client, code = self._session(stack)
                if client is None:
                    return (code if code in PLAY_CODES else "error"), "", 0
                used = recordings.scan_storage(self.root)[1]
                cap_allow = s.max_gb * GB - used
                disk_allow = free_bytes(self.root) - LOW_DISK_BYTES
                if cap_allow <= 0:
                    return "disk_cap_reached", "", 0
                if disk_allow <= 0:
                    return "disk_low", "", 0
                try:
                    res = recordings.fetch(client, self.root, rid, req.call_date, min(MAX_FILE_BYTES, cap_allow, disk_allow))
                except TelusDownloadError as e:
                    return ("disk_cap_reached" if e.reason == "too_large" else "error"), "", 0
                except ReadOnlyViolation:
                    return "error", "", 0
                except TelusHTTPError as e:
                    return {404: "not_found", 410: "not_found", 429: "rate_limited", 401: "login_failed"}.get(e.status, "error"), "", 0
                except TelusError:
                    return "error", "", 0
                except OSError as e:
                    return ("disk_low" if e.errno in (errno.ENOSPC, errno.EDQUOT) else "error"), "", 0
                with _closing(dbm.connect(self.db_path, create=True)) as conn:
                    recordings.upsert(conn, rid, "play", "downloaded", nbytes=res.bytes, sha256=res.sha256, ext=res.ext, downloaded_at=self._t())
                src = self._find_stored(rid)
                if src is None:
                    return "error", "", 0
        ext = src.rsplit(".", 1)[-1]
        d = self._play_dir()
        for old in os.listdir(d):  # one audio file per recording
            if old.startswith(rid + ".") and old != rid + ".json":
                try:
                    os.unlink(os.path.join(d, old))
                except OSError:
                    pass
        tmp = os.path.join(d, f".{rid}.{os.getpid()}.part")
        with open(src, "rb") as fin, os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o660), "wb") as fout:
            n = 0
            while True:
                chunk = fin.read(1 << 20)
                if not chunk:
                    break
                n += len(chunk)
                if n > MAX_FILE_BYTES:
                    raise OSError("too large")
                fout.write(chunk)
        os.chmod(tmp, 0o660)
        os.replace(tmp, os.path.join(d, f"{rid}.{ext}"))
        return "ready", ext, n

    # -------------------------------------------------------------- mode: check
    def _check(self, req: Request) -> str:
        from contextlib import ExitStack
        with ExitStack() as stack:
            client, code = self._session(stack)
            if client is None:
                return code
            try:
                n, _exact = check.count_last_7_days(client)
            except TelusError:
                self._conn("failed", "error", calls=None)
                return "error"
            self._conn("ok", "ok", calls=n)
            return "ok"

    # -------------------------------------------------------------- mode: sync (live Brief numbers)
    def _set_sync(self, code: str, rows: int | None = None) -> None:
        prev = self.status.get("sync")
        if rows is None:
            rows = prev["rows"] if prev else 0   # a failed run leaves the old analytics.json in place
        self.status["sync"] = {"state": "ok" if code == "ok" else "failed", "code": code,
                               "finished_at": self._t(), "rows": rows}

    def _sync(self, days: int | None) -> str:
        """Same code for the request mode and the telus-sync command. Fixed codes only."""
        from contextlib import ExitStack
        try:
            with ExitStack() as stack:
                client, code = self._session(stack)
                if client is None:
                    self._set_sync(code)
                    return code
                conn = stack.enter_context(_closing(dbm.connect(self.db_path, create=True)))
                code, rows = sync.execute(client, conn, load_salt(self.salt_path), self.bridge, self.now(),
                                          days)
        except Exception as e:  # never let text from TELUS or the system reach status or logs
            log.error("telus sync failed (%s)", type(e).__name__)
            code, rows = "error", None
        self._set_sync(code, rows if code == "ok" else None)
        return code

    def run_sync_command(self, days: int | None) -> int:
        """telus-sync: one sync outside the request queue. Does not touch the `job` section."""
        if not os.path.isdir(self.bridge) or os.path.islink(self.bridge):
            print("error: bridge folder not found", file=sys.stderr)
            return 2
        lock = self._lock()
        if lock is None:
            print("telus-sync busy, exiting")
            return 1
        try:
            code = self._sync(days)
            self.flush()
            print(f"telus-sync code={code} rows={self.status['sync']['rows']}")
            return 0 if code == "ok" and self.status_ok else 1
        finally:
            os.close(lock)

    # -------------------------------------------------------------- mode: recordings
    def _recordings(self, req: Request) -> str:
        from contextlib import ExitStack
        job, s = self.status["job"], self.settings
        if not s.recordings_enabled:
            return "recordings_disabled"
        with ExitStack() as stack:
            client, code = self._session(stack)
            if client is None:
                return code
            conn = stack.enter_context(_closing(dbm.connect(self.db_path, create=True)))
            salt = load_salt(self.salt_path)
            now = self.now()
            try:
                calllog.pull(client, conn, salt, days=req.days, now=now, track_state=False)
            except TelusError as e:
                return _code_for(e)
            since = recordings.cutoff_date(now, req.days)
            counts = recordings.window_counts(conn, since)
            todo = recordings.pending(conn, since)
            limit = min(req.max_items, s.max_per_run_cap)
            job.update(found=counts["found"], already_had=counts["had"], skipped=counts["skipped"],
                       remaining=len(todo))
            self.flush()
            stop = self._download_all(client, conn, todo[:limit], len(todo))
            try:
                job["deleted_old"] = recordings.cleanup(self.root, conn, s.keep_days, self.now())
            except Exception as e:
                log.error("cleanup failed (%s)", type(e).__name__)
            if stop:
                return stop
            if counts["found"] == 0:
                return "no_recordings"
            if job["failed"] and not job["downloaded"]:
                return "error"
            return "partial" if job["failed"] else "ok"

    def _download_all(self, client, conn, batch, total_todo: int) -> str | None:
        """Returns a stop code, or None when the batch finished. Updates the job counters."""
        job, s = self.status["job"], self.settings
        max_bytes = s.max_gb * GB
        used = recordings.scan_storage(self.root)[1]
        consecutive = attempted = 0
        for idx, (rid, telus_id, call_date) in enumerate(batch):
            cap_allow = max_bytes - used
            disk_allow = free_bytes(self.root) - LOW_DISK_BYTES
            if cap_allow <= 0:
                return self._stop("disk_cap_reached", total_todo, attempted)
            if disk_allow <= 0:
                return self._stop("disk_low", total_todo, attempted)
            allow = min(MAX_FILE_BYTES, cap_allow, disk_allow)
            attempted += 1
            try:
                res = recordings.fetch(client, self.root, rid, call_date, allow)
            except TelusDownloadError as e:
                if e.reason == "too_large" and allow < MAX_FILE_BYTES:
                    return self._stop("disk_cap_reached" if cap_allow <= disk_allow else "disk_low",
                                      total_todo, attempted - 1)
                if e.reason == "too_large":
                    self._mark(conn, rid, telus_id, "skipped", "too_large")
                else:
                    consecutive = self._fail(conn, rid, telus_id, e.reason, consecutive)
            except ReadOnlyViolation:
                consecutive = self._fail(conn, rid, telus_id, "redirect_blocked", consecutive)
            except TelusHTTPError as e:
                if e.status == 429:
                    return self._stop("rate_limited", total_todo, attempted - 1)
                if e.status == 401:
                    return self._stop("login_failed", total_todo, attempted - 1)
                if e.status in (404, 410):
                    self._mark(conn, rid, telus_id, "skipped", "gone")
                else:
                    consecutive = self._fail(conn, rid, telus_id, f"http_{e.status}" if e.status else "network",
                                             consecutive)
            except TelusError:
                consecutive = self._fail(conn, rid, telus_id, "other", consecutive)
            except OSError as e:
                if e.errno in (errno.ENOSPC, errno.EDQUOT):
                    return self._stop("disk_low", total_todo, attempted - 1)
                consecutive = self._fail(conn, rid, telus_id, "io", consecutive)
            else:
                recordings.upsert(conn, rid, telus_id, "downloaded", nbytes=res.bytes, sha256=res.sha256,
                                  ext=res.ext, downloaded_at=self._t())
                used += res.bytes
                job["downloaded"] += 1
                consecutive = 0
                self._pace(res, last=idx == len(batch) - 1)
            job["remaining"] = total_todo - attempted
            if attempted % PROGRESS_EVERY == 0:
                self.flush()
            if consecutive >= MAX_CONSECUTIVE_FAILURES:
                return self._stop("partial" if job["downloaded"] else "error", total_todo, attempted)
        job["remaining"] = total_todo - attempted
        return None

    def _stop(self, code: str, total_todo: int, attempted: int) -> str:
        self.status["job"]["remaining"] = total_todo - attempted
        return code

    def _mark(self, conn, rid, telus_id, status, error_code) -> None:
        recordings.upsert(conn, rid, telus_id, status, error_code=error_code)
        self.status["job"]["skipped" if status == "skipped" else "failed"] += 1

    def _fail(self, conn, rid, telus_id, error_code, consecutive) -> int:
        self._mark(conn, rid, telus_id, "failed", error_code)
        return consecutive + 1

    def _pace(self, res, *, last: bool) -> None:
        if last:
            return
        if res.rate_remaining == 0:
            self.sleep(min(res.rate_window or 10.0, 90.0))
        elif self.file_sleep > 0:
            self.sleep(self.file_sleep)


def check_required():
    from . import endpoints
    return endpoints.REQUIRED_READ_SCOPES


class _closing:
    def __init__(self, c):
        self.c = c

    def __enter__(self):
        return self.c

    def __exit__(self, *exc):
        self.c.close()


def run(root: str, bridge: str, db_path: str | None = None, salt_path: str | None = None, **kw) -> int:
    return Worker(root, bridge, db_path or os.path.join(root, "private.db"),
                  salt_path or os.path.join(root, "salt.bin"), **kw).run()
