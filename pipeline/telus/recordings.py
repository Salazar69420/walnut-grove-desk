"""Recording downloader helpers: where files go, how they are written, tracked and securely removed.

Files live at `<root>/recordings/YYYY/MM/<recording_id>.<ext>`, mode 600, in directories of mode 700. A row in
`telus_recordings` tracks each recording. Nothing here logs or prints recording ids, call ids, URLs or paths.
Audio is never transcribed or sent anywhere by this module.
"""
from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .client import ReadOnlyClient

MAX_FILE_BYTES = 200 * 1024 * 1024
REC_DIR = "recordings"
PART_MAX_AGE = 3600  # seconds; older leftover temp files are removed by cleanup

EXT_BY_TYPE = {
    "audio/mpeg": "mp3", "audio/mp3": "mp3", "audio/mpeg3": "mp3", "audio/x-mpeg-3": "mp3",
    "audio/wav": "wav", "audio/x-wav": "wav", "audio/wave": "wav", "audio/vnd.wave": "wav",
    "audio/ogg": "ogg", "audio/opus": "ogg", "audio/mp4": "m4a", "audio/x-m4a": "m4a", "audio/aac": "aac",
    "audio/webm": "webm", "audio/flac": "flac", "application/octet-stream": "bin",
}
_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_STEM_RE = re.compile(r"^([A-Za-z0-9_-]{1,64})\.([a-z0-9]{1,8})$")
_PART_RE = re.compile(r"^\.[A-Za-z0-9_.-]{1,160}\.part$")


def ext_for(content_type: str) -> str:
    return EXT_BY_TYPE.get(content_type, "audio")


def rec_root(root: str) -> str:
    return os.path.join(root, REC_DIR)


def _mkdir700(path: str) -> None:
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        st = os.lstat(path)
        if not stat.S_ISDIR(st.st_mode):
            raise
    os.chmod(path, 0o700)


def dest_dir(root: str, call_date: str) -> str:
    """Create (mode 700 at every level) and return recordings/YYYY/MM for a call date."""
    m = _DATE_RE.match(call_date or "")
    year, month = (m.group(1), m.group(2)) if m else ("undated", "00")
    d = os.path.join(root, REC_DIR)
    _mkdir700(d)
    for part in (year, month):
        d = os.path.join(d, part)
        _mkdir700(d)
    return d


@dataclass(frozen=True)
class Fetched:
    ext: str
    bytes: int
    sha256: str
    rate_remaining: int | None
    rate_window: float | None


def fetch(client: ReadOnlyClient, root: str, recording_id: str, call_date: str, max_bytes: int) -> Fetched:
    """Download one recording to its final place: temp file in the same directory, fsync, rename.
    Raises the client's TelusError subclasses (or OSError); no partial file is left behind."""
    d = dest_dir(root, call_date)
    tmp = os.path.join(d, f".{recording_id}.{os.getpid()}.part")
    try:
        os.unlink(tmp)
    except OSError:
        pass
    res = client.download_recording(recording_id, tmp, max_bytes)
    ext = ext_for(res.content_type)
    final = os.path.join(d, f"{recording_id}.{ext}")
    try:
        os.chmod(tmp, 0o600)
        os.replace(tmp, final)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    _fsync_dir(d)
    return Fetched(ext=ext, bytes=res.bytes, sha256=res.sha256,
                   rate_remaining=res.rate_remaining, rate_window=res.rate_window)


def _fsync_dir(path: str) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


# ------------------------------------------------------------------ database
def upsert(conn, recording_id: str, telus_id: str, status: str, *, nbytes: int = 0, sha256: str | None = None,
           ext: str | None = None, downloaded_at: str | None = None, error_code: str | None = None) -> None:
    conn.execute(
        "INSERT INTO telus_recordings(recording_id, telus_id, status, bytes, sha256, ext, downloaded_at, error_code) "
        "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(recording_id) DO UPDATE SET telus_id=excluded.telus_id, "
        "status=excluded.status, bytes=excluded.bytes, sha256=excluded.sha256, ext=excluded.ext, "
        "downloaded_at=excluded.downloaded_at, error_code=excluded.error_code",
        (recording_id, telus_id, status, nbytes, sha256, ext, downloaded_at, error_code))
    conn.commit()


def cutoff_date(now: datetime, days: int) -> str:
    """Local calendar date (YYYY-MM-DD) `days` days back; calls on or after it are in the window."""
    from .calllog import _TZ
    local = now.astimezone(_TZ) if _TZ else now
    return (local - timedelta(days=days)).date().isoformat()


def window_counts(conn, since_date: str) -> dict:
    """found / already_had / skipped for calls in the window that have a recording id."""
    q = ("SELECT COUNT(*) AS found, "
         "SUM(CASE WHEN r.status='downloaded' THEN 1 ELSE 0 END) AS had, "
         "SUM(CASE WHEN r.status='skipped' THEN 1 ELSE 0 END) AS skipped "
         "FROM telus_calls c LEFT JOIN telus_recordings r ON r.recording_id=c.recording_id "
         "WHERE c.recording_id IS NOT NULL AND c.date>=?")
    r = conn.execute(q, (since_date,)).fetchone()
    return {"found": r["found"] or 0, "had": r["had"] or 0, "skipped": r["skipped"] or 0}


def pending(conn, since_date: str) -> list[tuple[str, str, str]]:
    """(recording_id, telus_id, date) not yet downloaded or skipped, newest first. Failed ones are retried."""
    rows = conn.execute(
        "SELECT c.recording_id, c.telus_id, c.date FROM telus_calls c "
        "LEFT JOIN telus_recordings r ON r.recording_id=c.recording_id "
        "WHERE c.recording_id IS NOT NULL AND c.date>=? AND (r.status IS NULL OR r.status='failed') "
        "GROUP BY c.recording_id ORDER BY MAX(c.start_local) DESC, c.recording_id", (since_date,)).fetchall()
    return [(r[0], r[1], r[2]) for r in rows]


# ------------------------------------------------------------------ storage and cleanup
def _entries(path: str):
    try:
        with os.scandir(path) as it:
            return list(it)
    except OSError:
        return []


def _walk_files(root: str):
    """Yield (path, DirEntry) for regular files two levels under recordings/. Symlinks are never followed."""
    base = os.path.join(root, REC_DIR)
    for ydir in _entries(base):
        if not ydir.is_dir(follow_symlinks=False):
            continue
        for mdir in _entries(ydir.path):
            if not mdir.is_dir(follow_symlinks=False):
                continue
            for f in _entries(mdir.path):
                if f.is_file(follow_symlinks=False):
                    yield mdir.path, f


def scan_storage(root: str) -> tuple[int, int]:
    """(file count, total bytes) of finished recordings (temp files are not counted)."""
    count = total = 0
    for _, f in _walk_files(root):
        if f.name.startswith("."):
            continue
        try:
            total += f.stat(follow_symlinks=False).st_size
        except OSError:
            continue
        count += 1
    return count, total


def secure_delete(path: str) -> bool:
    """Overwrite a regular file with zeros, fsync, then unlink. Never follows a symlink."""
    try:
        st = os.lstat(path)
        if not stat.S_ISREG(st.st_mode):
            return False
        fd = os.open(path, os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return False
    try:
        left = os.fstat(fd).st_size
        block = b"\x00" * (1024 * 1024)
        while left > 0:
            n = os.write(fd, block[:min(left, len(block))])
            left -= n
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)
    try:
        os.unlink(path)
    except OSError:
        return False
    return True


def cleanup(root: str, conn, keep_days: int, now: datetime) -> int:
    """Securely delete OUR OWN downloaded files older than keep_days (by download time) and leftover temp
    files. Only files whose name is a `downloaded` row in telus_recordings are touched. Returns the number of
    recordings deleted. Their rows become `skipped` (error_code `expired`) so they are not fetched again."""
    old = (now.astimezone(timezone.utc) - timedelta(days=keep_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    files: dict[str, str] = {}
    leftovers: list[str] = []
    for _, f in _walk_files(root):
        m = _STEM_RE.match(f.name)
        if m:
            files[m.group(1)] = f.path
        elif _PART_RE.match(f.name):
            try:
                if now.timestamp() - f.stat(follow_symlinks=False).st_mtime > PART_MAX_AGE:
                    leftovers.append(f.path)
            except OSError:
                pass
    deleted = 0
    rows = conn.execute("SELECT recording_id, telus_id FROM telus_recordings "
                        "WHERE status='downloaded' AND downloaded_at IS NOT NULL AND downloaded_at<?",
                        (old,)).fetchall()
    for r in rows:
        path = files.get(r["recording_id"])
        if path is not None:
            if not secure_delete(path):
                continue
            deleted += 1
        conn.execute("UPDATE telus_recordings SET status='skipped', error_code='expired' WHERE recording_id=?",
                     (r["recording_id"],))
    conn.commit()
    for p in leftovers:
        secure_delete(p)
    base = os.path.join(root, REC_DIR)
    for ydir in _entries(base):  # tidy empty month and year folders
        if ydir.is_dir(follow_symlinks=False):
            for mdir in _entries(ydir.path):
                if mdir.is_dir(follow_symlinks=False):
                    try:
                        os.rmdir(mdir.path)
                    except OSError:
                        pass
            try:
                os.rmdir(ydir.path)
            except OSError:
                pass
    return deleted


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
