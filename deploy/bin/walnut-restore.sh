#!/bin/bash
# Restore the app database from a backup. Usage: sudo walnut-restore.sh /var/lib/walnut-desk/backups/app-YYYYMMDD-HHMMSS.db.gz
set -euo pipefail
B="${1:?give a backup file}"; DB=/var/lib/walnut-desk/app.db
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
gunzip -c "$B" > "$T/r.db"
[ "$(sqlite3 "$T/r.db" 'PRAGMA integrity_check')" = ok ] || { echo "backup failed integrity check"; exit 1; }
systemctl stop walnut-desk
cp -a "$DB" "$DB.before-restore.$(date +%s)" 2>/dev/null || true
rm -f "$DB-wal" "$DB-shm"; install -o walnut-app -g walnut-app -m 600 "$T/r.db" "$DB"
systemctl start walnut-desk; sleep 3; curl -fsS http://127.0.0.1:8000/healthz && echo " restored from $B"
