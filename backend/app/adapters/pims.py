"""Read-only access to the practice management system (ImproMed). No write path exists."""
from __future__ import annotations

import csv
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from app.services import importers as imp


class PimsAdapter(Protocol):
    def appointments(self, day: date | None = None) -> list[imp.AppointmentRow]: ...
    def clients(self) -> list[imp.ClientRow]: ...
    def patients(self) -> list[imp.PetRow]: ...
    def lab_results(self, since: datetime | None = None) -> list[imp.LabRow]: ...


class CsvExportAdapter:
    """Reads appointments.csv, clients.csv, patients.csv and lab_results.csv from a folder."""

    def __init__(self, directory: str | Path):
        self.dir = Path(directory)

    def _rows(self, filename: str, model: type[imp.Row], kind: str):
        path = self.dir / filename
        if not path.is_file():
            return [], []
        raw = imp.parse_file(filename, path.read_bytes(), kind)
        return imp.validate(kind, raw)

    def appointments(self, day: date | None = None) -> list[imp.AppointmentRow]:
        good, _ = self._rows("appointments.csv", imp.AppointmentRow, "appointments")
        if day is not None:
            from app.services import timeutil
            good = [r for r in good if timeutil.local_date(r.start) == day]
        return good

    def clients(self) -> list[imp.ClientRow]:
        return self._rows("clients.csv", imp.ClientRow, "clients")[0]

    def patients(self) -> list[imp.PetRow]:
        return self._rows("patients.csv", imp.PetRow, "pets")[0]

    def lab_results(self, since: datetime | None = None) -> list[imp.LabRow]:
        good, _ = self._rows("lab_results.csv", imp.LabRow, "lab_results")
        if since is not None:
            good = [r for r in good if r.resulted_at is None or r.resulted_at >= since]
        return good
