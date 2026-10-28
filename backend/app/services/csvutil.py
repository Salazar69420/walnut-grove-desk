"""CSV writing that is safe to open in a spreadsheet (formula-injection safe)."""
from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Sequence
from typing import Any

_DANGEROUS = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return value
    s = str(value)
    if s.startswith(_DANGEROUS):
        return "'" + s
    return s


def to_csv(header: Sequence[str], rows: Iterable[Sequence[Any]]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow([safe_cell(h) for h in header])
    for r in rows:
        w.writerow([safe_cell(c) for c in r])
    return buf.getvalue()
