"""Read analytics.json from the bridge folder and load it into call_analytics (source 'telus_live').

docs/CONTRACT_LIVE_BRIEF.md. The file is written by a separate worker and is untrusted: it is read with a
size limit and without following symlinks, checked against a strict schema, and only then does it replace the
old rows, all in one transaction. Only counts live in it. Nothing in this module logs or stores file content,
and `ingest` never raises.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import stat
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, StrictStr, field_validator
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app import config
from app.models import CallAnalytic
from app.services import events as ev
from app.services import settings_store, timeutil

log = logging.getLogger("walnut.live_ingest")

SOURCE = "telus_live"
META_KEY = "live_ingest"
FILE_NAME = "analytics.json"
MAX_BYTES = 256 * 1024
MAX_ROWS = 2000
MAX_VALUE = 10**7

STR_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,40}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

PERIODS = ("7d", "prev7d")
DIRECTIONS = ("all", "inbound", "outbound")
RESULTS = ("answered", "missed", "voicemail", "hangup", "other")
FOLLOWUPS = ("rang_again_1h", "called_back_4h", "called_back_24h", "no_contact_24h", "too_recent")
AFTER_HOURS = ("inbound", "voicemail")
HOURS = tuple(str(h) for h in range(8, 18))


def _is_date(s: str) -> bool:
    if not DATE_RE.match(s):
        return False
    try:
        date.fromisoformat(s)
    except ValueError:
        return False
    return True


# metric -> (allowed dim1 check, allowed dim2 check). None means the dimension must be null.
def _in(options):
    return lambda v: v in options


_DIMS: dict[str, tuple[Any, Any]] = {
    "live_calls": (_in(PERIODS), _in(DIRECTIONS)),
    "live_inbound_result": (_in(PERIODS), _in(RESULTS)),
    "live_missed_followup": (_in(PERIODS), _in(FOLLOWUPS)),
    "live_after_hours": (_in(PERIODS), _in(AFTER_HOURS)),
    "live_inbound_by_hour": (_in(HOURS), None),
    "live_missed_by_hour": (_in(HOURS), None),
    "live_busiest_hour": (_in(HOURS), None),
    "live_daily_inbound": (_is_date, None),
    "live_past_followup_share": (_in(("past_recordings",)), None),
}
METRICS = tuple(_DIMS)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Window(_Strict):
    from_: StrictStr = Field(alias="from")
    to: StrictStr

    @field_validator("from_", "to")
    @classmethod
    def _dates(cls, v: str) -> str:
        if not _is_date(v):
            raise ValueError("bad date")
        return v


class Row(_Strict):
    metric: StrictStr
    dim1: StrictStr | None = None
    dim2: StrictStr | None = None
    value: Union[StrictInt, StrictFloat]
    n: StrictInt

    @field_validator("metric", "dim1", "dim2")
    @classmethod
    def _charset(cls, v: str | None) -> str | None:
        if v is not None and not STR_RE.match(v):
            raise ValueError("bad characters or length")
        return v

    @field_validator("value")
    @classmethod
    def _value(cls, v: int | float) -> int | float:
        if isinstance(v, float) and not math.isfinite(v):
            raise ValueError("not finite")
        if v < 0 or v > MAX_VALUE:
            raise ValueError("out of range")
        return v

    @field_validator("n")
    @classmethod
    def _n(cls, v: int) -> int:
        if v < 0 or v > MAX_VALUE:
            raise ValueError("out of range")
        return v

    def check_dims(self) -> None:
        spec = _DIMS.get(self.metric)
        if spec is None:
            raise ValueError("unknown metric")
        for dim, ok in ((self.dim1, spec[0]), (self.dim2, spec[1])):
            if ok is None:
                if dim is not None:
                    raise ValueError("unexpected dimension")
            elif dim is None or not ok(dim):
                raise ValueError("bad dimension")


class Payload(_Strict):
    v: Literal[1]
    generated_at: StrictStr
    timezone: Literal["America/Vancouver"]
    window: Window
    rows: list[Row]

    @field_validator("generated_at")
    @classmethod
    def _gen(cls, v: str) -> str:
        if len(v) > 40:
            raise ValueError("too long")
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError("needs a time zone")
        if dt.astimezone(timezone.utc) > timeutil.now_utc().astimezone(timezone.utc) + timedelta(days=1):
            raise ValueError("in the future")
        return v

    @field_validator("rows")
    @classmethod
    def _rows(cls, rows: list[Row]) -> list[Row]:
        if len(rows) > MAX_ROWS:
            raise ValueError("too many rows")
        seen: set[tuple[str, str | None, str | None]] = set()
        for r in rows:
            r.check_dims()
            key = (r.metric, r.dim1, r.dim2)
            if key in seen:
                raise ValueError("duplicate row")
            seen.add(key)
        return rows


@dataclass
class IngestResult:
    status: Literal["ok", "unchanged", "no_file", "unsafe", "too_big", "invalid", "error"]
    rows: int = 0


# ---------------------------------------------------------------- safe read
def read_bytes(directory: str, name: str = FILE_NAME) -> tuple[bytes | None, str]:
    """Bounded, no-symlink read. Returns (data, status). Never raises."""
    try:
        d = os.lstat(directory)
        if not stat.S_ISDIR(d.st_mode):  # lstat: a symlinked folder is refused
            return None, "unsafe"
        path = os.path.join(directory, name)
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            return None, "no_file"
        if not stat.S_ISREG(st.st_mode):
            return None, "unsafe"
        if st.st_size > MAX_BYTES:
            return None, "too_big"
        flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
        fd = os.open(path, flags)
        try:
            st2 = os.fstat(fd)
            if not stat.S_ISREG(st2.st_mode):
                return None, "unsafe"
            if st2.st_size > MAX_BYTES:
                return None, "too_big"
            chunks: list[bytes] = []
            total = 0
            while total <= MAX_BYTES:
                part = os.read(fd, MAX_BYTES + 1 - total)
                if not part:
                    break
                chunks.append(part)
                total += len(part)
        finally:
            os.close(fd)
        if total > MAX_BYTES:
            return None, "too_big"
        return b"".join(chunks), "ok"
    except OSError as e:
        log.warning("live numbers file not readable (%s)", e.__class__.__name__)
        return None, "unsafe"
    except Exception as e:  # noqa: BLE001  (untrusted input must never raise out of here)
        log.warning("live numbers file read failed (%s)", e.__class__.__name__)
        return None, "error"


def _no_constants(_name: str):
    raise ValueError("not a number")


def parse(data: bytes) -> Payload | None:
    """Strict parse. None for anything that does not fit the schema. Never raises, never logs content."""
    try:
        obj = json.loads(data.decode("utf-8"), parse_constant=_no_constants)
        if not isinstance(obj, dict):
            return None
        return Payload.model_validate(obj)
    except Exception as e:  # noqa: BLE001
        log.warning("live numbers file rejected (%s)", e.__class__.__name__)
        return None


# ---------------------------------------------------------------- ingest
def meta(db: Session) -> dict[str, Any]:
    m = settings_store.get_raw(db, META_KEY, None)
    return m if isinstance(m, dict) else {}


def ingest(db: Session, directory: str | None = None) -> IngestResult:
    """Load analytics.json if it changed. All-or-nothing; old rows stay if anything is wrong. Never raises."""
    try:
        directory = directory or config.get_settings().telus_dir
        data, status = read_bytes(directory)
        if data is None:
            return IngestResult(status)  # type: ignore[arg-type]
        payload = parse(data)
        if payload is None:
            return IngestResult("invalid")
        sha = hashlib.sha256(data).hexdigest()
        # The first statement opens the write transaction, so a second process waits here and then sees our meta.
        current = meta(db)
        have = db.scalar(select(CallAnalytic.id).where(CallAnalytic.source == SOURCE).limit(1))
        if current.get("sha") == sha and have is not None:
            db.rollback()
            return IngestResult("unchanged", int(current.get("rows", 0)))
        now = timeutil.now_utc()
        db.execute(delete(CallAnalytic).where(CallAnalytic.source == SOURCE))
        db.add_all([
            CallAnalytic(source=SOURCE, metric=r.metric, dim1=r.dim1 or "", dim2=r.dim2 or "", value=float(r.value),
                         n=r.n, loaded_at=now)
            for r in payload.rows
        ])
        settings_store.put_raw(db, META_KEY, {
            "sha": sha, "generated_at": payload.generated_at, "ingested_at": timeutil.iso(now), "rows": len(payload.rows),
            "window_from": payload.window.from_, "window_to": payload.window.to,
        }, by="system")
        ev.record(db, ev.CALL_LOGGER, "live_calls_updated", f"Live call numbers updated: {len(payload.rows)} rows", None,
                  "analytics", "live", {"rows": len(payload.rows)})
        db.commit()
        return IngestResult("ok", len(payload.rows))
    except Exception as e:  # noqa: BLE001
        log.warning("live numbers ingest failed (%s)", e.__class__.__name__)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return IngestResult("error")
