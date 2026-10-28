"""Row validation and loading for imports (calls, appointments, clients, pets, lab_results, analytics)."""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import CallAnalytic, ImportedCall, PimsAppointment, PimsClient, PimsLabResult, PimsPatient
from app.services import events as ev
from app.services import timeutil

MAX_BYTES = 10 * 1024 * 1024
KINDS = ("calls", "appointments", "clients", "pets", "lab_results", "analytics")
ANALYTICS_SOURCES = ("derived_from_real_calls", "demo")


def _bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("1", "true", "yes", "y", "t"):
        return True
    if s in ("0", "false", "no", "n", "f", ""):
        return False
    raise ValueError("must be true or false")


def _dt(v: Any) -> datetime:
    s = str(v).strip()
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("must be an ISO date and time like 2026-10-27T09:31:00-07:00")
    if d.tzinfo is None:  # exports from the clinic system are in local time
        d = d.replace(tzinfo=timeutil.VANCOUVER)
    return d.astimezone(timezone.utc)


class Row(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class CallRow(Row):
    call_id: str = Field(min_length=1, max_length=80)
    timestamp: datetime
    caller_type: str = Field(default="", max_length=40)
    intent: str = Field(default="", max_length=60)
    outcome: str = Field(default="", max_length=60)
    urgency: str = Field(default="", max_length=20)
    red_flags: str = Field(default="", max_length=300)
    promised_followup: bool = False
    contains_private_content: bool
    summary: str | None = Field(default=None, max_length=4000)

    @field_validator("timestamp", mode="before")
    @classmethod
    def _ts(cls, v):
        return _dt(v)

    @field_validator("promised_followup", "contains_private_content", mode="before")
    @classmethod
    def _b(cls, v):
        return _bool(v)

    @field_validator("red_flags", mode="before")
    @classmethod
    def _flags(cls, v):
        if isinstance(v, list):
            return ";".join(str(x) for x in v)
        s = str(v or "").strip()
        if s.startswith("["):
            try:
                lst = json.loads(s)
            except ValueError:
                raise ValueError("red_flags must be a JSON list or ;-separated words")
            if not isinstance(lst, list):
                raise ValueError("red_flags must be a JSON list or ;-separated words")
            return ";".join(str(x) for x in lst)
        return s


class AppointmentRow(Row):
    id: str = Field(min_length=1, max_length=60)
    start: datetime
    client_id: str = Field(default="", max_length=60)
    patient_id: str = Field(default="", max_length=60)
    provider: str = Field(default="", max_length=120)
    visit_type: str = Field(default="", max_length=120)
    status: str = Field(default="", max_length=30)

    @field_validator("start", mode="before")
    @classmethod
    def _s(cls, v):
        return _dt(v)


class ClientRow(Row):
    id: str = Field(min_length=1, max_length=60)
    name: str = Field(default="", max_length=160)
    last_visit: datetime | None = None
    recall_overdue: bool = False

    @field_validator("last_visit", mode="before")
    @classmethod
    def _lv(cls, v):
        return _dt(v) if v not in (None, "") else None

    @field_validator("recall_overdue", mode="before")
    @classmethod
    def _b(cls, v):
        return _bool(v)


class PetRow(Row):
    id: str = Field(min_length=1, max_length=60)
    client_id: str = Field(default="", max_length=60)
    name: str = Field(default="", max_length=120)
    species: str = Field(default="", max_length=40)
    breed: str = Field(default="", max_length=80)
    last_exam: datetime | None = None

    @field_validator("last_exam", mode="before")
    @classmethod
    def _le(cls, v):
        return _dt(v) if v not in (None, "") else None


class LabRow(Row):
    id: str = Field(min_length=1, max_length=60)
    patient_id: str = Field(default="", max_length=60)
    test: str = Field(default="", max_length=120)
    resulted_at: datetime | None = None
    status: str = Field(default="", max_length=30)

    @field_validator("resulted_at", mode="before")
    @classmethod
    def _r(cls, v):
        return _dt(v) if v not in (None, "") else None


class AnalyticsRow(Row):
    source: str
    metric: str = Field(min_length=1, max_length=60)
    dim1: str = Field(default="", max_length=60)
    dim2: str = Field(default="", max_length=60)
    value: float
    n: int = Field(default=0, ge=0)

    @field_validator("source")
    @classmethod
    def _src(cls, v: str) -> str:
        if v not in ANALYTICS_SOURCES:
            raise ValueError("source must be derived_from_real_calls or demo")
        return v

    @field_validator("dim1", "dim2", mode="before")
    @classmethod
    def _dim(cls, v):
        return "" if v is None else str(v)


MODELS: dict[str, type[Row]] = {
    "calls": CallRow, "appointments": AppointmentRow, "clients": ClientRow, "pets": PetRow,
    "lab_results": LabRow, "analytics": AnalyticsRow,
}
REQUIRED_COLUMNS = {
    "calls": ["call_id", "timestamp", "contains_private_content"],
    "appointments": ["id", "start"], "clients": ["id"], "pets": ["id"], "lab_results": ["id"],
    "analytics": ["source", "metric", "value"],
}


class ImportError_(ValueError):
    pass


def parse_file(filename: str, data: bytes, kind: str) -> list[dict[str, Any]]:
    """Return raw row dicts from csv, jsonl or (analytics only) json."""
    if len(data) > MAX_BYTES:
        raise ImportError_("The file is larger than 10 MB")
    name = (filename or "").lower()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ImportError_("The file must be UTF-8 text")
    if name.endswith(".csv"):
        reader = csv.DictReader(io.StringIO(text))
        cols = [c.strip() for c in (reader.fieldnames or [])]
        missing = [c for c in REQUIRED_COLUMNS[kind] if c not in cols]
        if missing:
            raise ImportError_("Missing column(s): " + ", ".join(missing))
        return [{(k or "").strip(): v for k, v in r.items() if k is not None} for r in reader]
    if name.endswith((".jsonl", ".ndjson")):
        rows = []
        for i, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                rows.append({"__bad__": f"line {i} is not valid JSON"})
                continue
            rows.append(obj if isinstance(obj, dict) else {"__bad__": f"line {i} is not a JSON object"})
        return rows
    if name.endswith(".json") and kind == "analytics":
        try:
            obj = json.loads(text)
        except ValueError:
            raise ImportError_("The file is not valid JSON")
        rows = obj.get("rows") if isinstance(obj, dict) else obj
        if not isinstance(rows, list):
            raise ImportError_('Expected {"meta":{...},"rows":[...]}')
        return [r if isinstance(r, dict) else {"__bad__": "row is not an object"} for r in rows]
    raise ImportError_("The file must be .csv or .jsonl" + (" (or .json for analytics)" if kind == "analytics" else ""))


def validate(kind: str, raw: list[dict[str, Any]]) -> tuple[list[Row], list[dict[str, Any]]]:
    model = MODELS[kind]
    good: list[Row] = []
    bad: list[dict[str, Any]] = []
    for i, r in enumerate(raw, 1):
        if "__bad__" in r:
            bad.append({"row": i, "error": r["__bad__"]})
            continue
        try:
            good.append(model(**r))
        except ValidationError as e:
            first = e.errors()[0]
            field = ".".join(str(x) for x in first["loc"]) or "row"
            bad.append({"row": i, "error": f"{field}: {first['msg']}"})
    return good, bad


def _naive(d: datetime | None):
    return d


def load(db: Session, kind: str, rows: list[Row], actor: Any = None) -> int:
    """Insert or update the rows. Returns the number loaded. Caller commits."""
    now = timeutil.now_utc()
    n = len(rows)
    if kind == "calls":
        existing = {c.call_id: c for c in db.scalars(select(ImportedCall).where(ImportedCall.call_id.in_([r.call_id for r in rows]))).all()} if rows else {}
        for r in rows:
            vals = dict(timestamp=r.timestamp, caller_type=r.caller_type, intent=r.intent, outcome=r.outcome, urgency=r.urgency,
                        red_flags=r.red_flags, promised_followup=r.promised_followup,
                        contains_private_content=r.contains_private_content,
                        summary=None if r.contains_private_content else (r.summary or ""), source="import", imported_at=now)
            row = existing.get(r.call_id)
            if row is None:
                db.add(ImportedCall(call_id=r.call_id, **vals))
            else:
                for k, v in vals.items():
                    setattr(row, k, v)
        ev.record(db, ev.CALL_LOGGER, "calls_imported", f"Imported {n} calls", actor, "analytics", "calls", {"rows": n})
    elif kind == "analytics":
        for src in {r.source for r in rows}:
            db.execute(delete(CallAnalytic).where(CallAnalytic.source == src))
        for r in rows:
            db.add(CallAnalytic(source=r.source, metric=r.metric, dim1=r.dim1, dim2=r.dim2, value=r.value, n=r.n, loaded_at=now))
        ev.record(db, ev.CALL_LOGGER, "analytics_imported", f"Imported {n} analytics rows", actor, "analytics", "analytics", {"rows": n})
    else:
        spec: dict[str, tuple[type, Callable[[Row], dict[str, Any]], str]] = {
            "appointments": (PimsAppointment, lambda r: dict(start=r.start, client_external_id=r.client_id, patient_external_id=r.patient_id,
                                                          provider=r.provider, visit_type=r.visit_type, status=r.status), "appointments"),
            "clients": (PimsClient, lambda r: dict(name=r.name, last_visit=r.last_visit, recall_overdue=r.recall_overdue), "clients"),
            "pets": (PimsPatient, lambda r: dict(client_external_id=r.client_id, name=r.name, species=r.species, breed=r.breed,
                                                 last_exam=r.last_exam), "pets"),
            "lab_results": (PimsLabResult, lambda r: dict(patient_external_id=r.patient_id, test=r.test, resulted_at=r.resulted_at,
                                                          status=r.status), "lab results"),
        }
        model, fn, label = spec[kind]
        existing = {x.external_id: x for x in db.scalars(select(model).where(model.external_id.in_([r.id for r in rows]))).all()} if rows else {}
        for r in rows:
            vals = fn(r)
            row = existing.get(r.id)
            if row is None:
                db.add(model(external_id=r.id, synced_at=now, **vals))
            else:
                for k, v in vals.items():
                    setattr(row, k, v)
                row.synced_at = now
        ev.record(db, ev.IMPROMED_SYNC, "pims_imported", f"Imported {n} {label} (read only)", actor, "analytics", kind, {"rows": n})
    return n
