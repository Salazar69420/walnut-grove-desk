#!/opt/walnut-desk/.venv/bin/python
"""Daily online backup of the app database (SQLite backup API), gzip, keep 14 days. Runs as walnut-app."""
import datetime, glob, gzip, os, shutil, sqlite3, sys
SRC = os.environ.get("WALNUT_DB_PATH", "/var/lib/walnut-desk/app.db")
DIR = os.environ.get("WALNUT_BACKUP_DIR", "/var/lib/walnut-desk/backups")
KEEP = int(os.environ.get("WALNUT_BACKUP_KEEP_DAYS", "14"))
stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
tmp = os.path.join(DIR, f".app-{stamp}.db")
src, dst = sqlite3.connect(SRC), sqlite3.connect(tmp)
src.backup(dst)
ok = dst.execute("PRAGMA integrity_check").fetchone()[0]
src.close(); dst.close()
if ok != "ok":
    os.unlink(tmp); sys.exit(f"integrity check failed: {ok}")
out = os.path.join(DIR, f"app-{stamp}.db.gz")
with open(tmp, "rb") as f, gzip.open(out, "wb") as g:
    shutil.copyfileobj(f, g)
os.chmod(out, 0o600); os.unlink(tmp)
cut = datetime.datetime.now().timestamp() - KEEP * 86400
old = [p for p in glob.glob(os.path.join(DIR, "app-*.db.gz")) if os.path.getmtime(p) < cut]
for p in old:
    os.unlink(p)
print(f"backup ok {os.path.basename(out)} size={os.path.getsize(out)} removed_old={len(old)}")
