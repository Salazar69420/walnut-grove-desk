"""CSV -> private.db loader. Prints counts only."""
from __future__ import annotations

import csv
import hmac
import hashlib
import os
import re
import secrets
import sys
from datetime import date, datetime, timedelta

from . import db as dbm
from .config import TZ_NAME

try:
    from zoneinfo import ZoneInfo

    _TZ = ZoneInfo(TZ_NAME)
except Exception:  # pragma: no cover - tzdata missing
    _TZ = None

SPEAKER_RE = re.compile(r"^\s*Speaker\s*\d+\s*:\s*", re.I | re.M)


def set_csv_limit() -> None:
    limit = sys.maxsize
    while True:
        try:
            csv.field_size_limit(limit)
            return
        except OverflowError:
            limit = int(limit / 10)


def load_salt(path: str, create: bool = True) -> bytes:
    if os.path.exists(path):
        with open(path, "rb") as f:
            data = f.read()
        if len(data) < 16:
            raise SystemExit("error: salt file too short")
        return data
    if not create:
        raise SystemExit("error: salt file missing")
    os.makedirs(os.path.dirname(os.path.abspath(path)), mode=0o700, exist_ok=True)
    data = secrets.token_bytes(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return data


def hmac_hex(salt: bytes, value: str) -> str:
    return hmac.new(salt, value.encode("utf-8"), hashlib.sha256).hexdigest()


def normalize_number(raw: str | None) -> str | None:
    """Digits only; a leading country code 1 on 11 digit numbers is dropped. None if no digits."""
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return None
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


def parse_file_name(name: str) -> dict:
    base = os.path.basename((name or "").strip().replace("\\", "/"))
    stem = re.sub(r"\.mp3$", "", base, flags=re.I)
    parts = stem.split("_")
    out = {"call_id": stem, "dt": None, "ext": None, "number_token": None}
    m = re.fullmatch(r"(\d{8})-(\d{6})", parts[0]) if parts else None
    if m:
        try:
            out["dt"] = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
        except ValueError:
            pass
    if len(parts) > 1 and re.fullmatch(r"\d{3}", parts[1]):
        out["ext"] = parts[1]
    if len(parts) > 2:
        out["number_token"] = parts[2]
    return out


_DATE_FORMATS = ["%Y-%m-%d", "%Y%m%d", "%Y/%m/%d", "%m/%d/%Y", "%d-%m-%Y"]
_TIME_FORMATS = ["%H:%M:%S", "%H%M%S", "%H:%M", "%I:%M:%S %p", "%I:%M %p"]


def _parse_dt_columns(d: str, t: str) -> datetime | None:
    dd = tt = None
    for f in _DATE_FORMATS:
        try:
            dd = datetime.strptime((d or "").strip(), f).date()
            break
        except ValueError:
            continue
    for f in _TIME_FORMATS:
        try:
            tt = datetime.strptime((t or "").strip(), f).time()
            break
        except ValueError:
            continue
    if dd is None:
        return None
    return datetime.combine(dd, tt) if tt else datetime.combine(dd, datetime.min.time())


def _nth_sunday(year: int, month: int, n: int) -> date:
    d = date(year, month, 1)
    d += timedelta(days=(6 - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def local_iso(dt: datetime) -> str:
    if _TZ is not None:
        return dt.replace(tzinfo=_TZ).isoformat()
    start = datetime.combine(_nth_sunday(dt.year, 3, 2), datetime.min.time()) + timedelta(hours=2)
    end = datetime.combine(_nth_sunday(dt.year, 11, 1), datetime.min.time()) + timedelta(hours=2)
    off = "-07:00" if start <= dt < end else "-08:00"
    return dt.isoformat() + off


def strip_speaker_labels(text: str) -> str:
    return re.sub(r"\s+", " ", SPEAKER_RE.sub("", text or "")).strip()


def _float(v: str | None) -> float | None:
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


def load(paths, force: bool = False) -> int:
    set_csv_limit()
    if not os.path.exists(paths.csv):
        print("error: inbox csv not found", file=sys.stderr)
        return 2
    conn = dbm.connect(paths.db, create=True)
    existing = conn.execute("SELECT COUNT(*) FROM calls").fetchone()[0]
    if existing and not force:
        print(f"refusing to reload: calls table has {existing} rows (use --force)", file=sys.stderr)
        return 2
    salt = load_salt(paths.salt)

    rows: dict[str, tuple] = {}
    stats = dict(rows_read=0, duplicates_skipped=0, bad_timestamp_skipped=0, bad_seconds=0,
                 unparsed_file_names=0, plain_text_derived=0, ref_collisions_resolved=0,
                 no_ext=0)
    used_refs: set[str] = set()

    with open(paths.csv, "r", encoding="utf-8-sig", errors="replace", newline="") as f:
        reader = csv.DictReader(f)
        reader.fieldnames = [(h or "").strip().lower() for h in (reader.fieldnames or [])]
        for rec in reader:
            stats["rows_read"] += 1
            fname = rec.get("file") or ""
            info = parse_file_name(fname)
            dt = info["dt"]
            if dt is None:
                stats["unparsed_file_names"] += 1
                dt = _parse_dt_columns(rec.get("date", ""), rec.get("time", ""))
            if dt is None:
                stats["bad_timestamp_skipped"] += 1
                continue
            call_id = info["call_id"]
            if not call_id:
                stats["bad_timestamp_skipped"] += 1
                continue
            ext = info["ext"]
            if ext is None:
                stats["no_ext"] += 1
                ext = "unk"
            num = normalize_number(rec.get("number")) or normalize_number(
                info["number_token"] if (info["number_token"] or "").lower() != "null" else None)
            number_hash = hmac_hex(salt, num) if num else None
            seconds = _float(rec.get("audio_seconds"))
            if seconds is None:
                stats["bad_seconds"] += 1
            transcript = rec.get("transcript") or ""
            plain = rec.get("plain_text") or ""
            if not plain.strip() and transcript.strip():
                plain = strip_speaker_labels(transcript)
                stats["plain_text_derived"] += 1
            lang = (rec.get("language") or "").strip().lower() or None
            row = [call_id, None, local_iso(dt), dt.date().isoformat(), dt.hour, dt.weekday(), ext,
                   number_hash, 1 if num else 0, seconds, lang, transcript, plain, 0]
            if call_id in rows:
                stats["duplicates_skipped"] += 1
                prev = rows[call_id]
                if len(plain) <= len(prev[12]):
                    continue
            rows[call_id] = tuple(row)

    out_rows = []
    for call_id, row in rows.items():
        row = list(row)
        digest = hmac_hex(salt, call_id)
        stamp = row[2][:19].replace("-", "").replace(":", "").replace("T", "-")
        k = 6
        ref = f"{stamp}-{row[6]}-{digest[:k]}"
        while ref in used_refs:
            stats["ref_collisions_resolved"] += 1
            k += 1
            ref = f"{stamp}-{row[6]}-{digest[k - 6:k]}"
        used_refs.add(ref)
        row[1] = ref
        out_rows.append(row)

    with conn:
        if existing:
            conn.execute("DELETE FROM calls")
            conn.execute("DELETE FROM calls_fts")
        conn.executemany(
            "INSERT INTO calls (call_id, call_ref, ts, date, hour, weekday, ext, number_hash, has_number,"
            " seconds, language, transcript, plain_text, private_purged) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            out_rows)
        conn.execute("INSERT INTO calls_fts(rowid, call_ref, plain_text) "
                     "SELECT rowid, call_ref, plain_text FROM calls")
        conn.execute("DELETE FROM load_meta")
        stats["loaded"] = len(out_rows)
        conn.executemany("INSERT INTO load_meta(key, value) VALUES (?,?)",
                         [(k, str(v)) for k, v in stats.items()])
    conn.close()
    for k, v in stats.items():
        print(f"{k}: {v}")
    return 0
