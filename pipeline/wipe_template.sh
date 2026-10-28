#!/usr/bin/env bash
# =====================================================================================================
# WIPE.sh  -  irreversible removal of everything derived from the private call-transcript project.
#
# WHAT THIS DELETES (and nothing else):
#   1. WIPE_ROOT (default /srv/walnut-private): EVERY regular file below it is overwritten and unlinked
#      with `shred -u` (private.db and its -wal/-shm files, salt.bin, inbox CSV, gold lists, out/ reports,
#      analytics.json, this script itself ...). Symlinks are unlinked, then the directory tree is removed.
#   2. WIPE_APP_DB (default /var/lib/walnut-desk/app.db), if it exists:
#        - DELETE FROM call_analytics WHERE source='derived_from_real_calls'
#        - DELETE FROM imported_calls  (all rows)
#      using the sqlite3 CLI with PRAGMA secure_delete=ON, then VACUUM and a WAL checkpoint, so deleted
#      pages are zeroed rather than merely marked free. Other tables and other call_analytics rows stay.
#   3. WIPE_CLAUDE_DIR (default /root/.claude/projects): Claude Code session logs, i.e. every *.jsonl
#      file and every file inside a "tool-results" directory, are shredded. Empty tool-results
#      directories are then removed. Nothing else in that directory is touched.
#   4. If WIPE_ROOT is the default /srv/walnut-private and the account `walnut-pipeline` exists,
#      `userdel walnut-pipeline` is run (home directory is NOT removed by userdel here).
#
# ENVIRONMENT:
#   WIPE_ROOT        path to wipe              (default /srv/walnut-private)
#   WIPE_APP_DB      web app sqlite database   (default /var/lib/walnut-desk/app.db)
#   WIPE_CLAUDE_DIR  Claude Code projects dir  (default /root/.claude/projects)
#   DRY_RUN=1        only count and print what would be done; change nothing
#
# MUST RUN AS ROOT (or set WIPE_SKIP_ROOT_CHECK=1, intended only for automated tests).
#
# CAVEATS: `shred` overwrites in place. On copy-on-write or journaling-with-data filesystems, SSD
# wear-levelling, snapshots or backups, old blocks may survive. Delete snapshots/backups separately.
#
# OUTPUT: counts only (no file names, no content).
# =====================================================================================================
set -euo pipefail

main() {
  local ROOT="${WIPE_ROOT:-/srv/walnut-private}"
  local APP_DB="${WIPE_APP_DB:-/var/lib/walnut-desk/app.db}"
  local CLAUDE_DIR="${WIPE_CLAUDE_DIR:-/root/.claude/projects}"
  local DRY="${DRY_RUN:-0}"
  local DEFAULT_ROOT="/srv/walnut-private"

  if [[ "${WIPE_SKIP_ROOT_CHECK:-0}" != "1" && "$(id -u)" -ne 0 ]]; then
    echo "error: must run as root" >&2
    return 1
  fi

  # Refuse obviously dangerous roots.
  case "${ROOT%/}" in
    ""|"/"|"/srv"|"/root"|"/home"|"/var"|"/etc"|"/usr"|"/opt"|"/tmp"|"/bin"|"/lib"|"/boot")
      echo "error: refusing to wipe unsafe WIPE_ROOT" >&2
      return 1 ;;
  esac
  if [[ "${ROOT:0:1}" != "/" ]]; then
    echo "error: WIPE_ROOT must be an absolute path" >&2
    return 1
  fi

  local files_shredded=0 dirs_removed=0 app_analytics_rows=0 app_imported_rows=0
  local claude_files=0 user_removed=0

  # ---- 1. WIPE_ROOT -----------------------------------------------------------------------------
  if [[ -d "$ROOT" ]]; then
    files_shredded=$(find "$ROOT" -xdev -type f | wc -l)
    dirs_removed=$(find "$ROOT" -xdev -type d | wc -l)
    if [[ "$DRY" != "1" ]]; then
      find "$ROOT" -xdev -type f -exec shred -u -- {} +
      find "$ROOT" -xdev -type l -delete
      rm -rf --one-file-system -- "$ROOT"
    fi
  fi

  # ---- 2. app database ----------------------------------------------------------------------------
  if [[ -f "$APP_DB" ]] && command -v sqlite3 >/dev/null 2>&1; then
    local has_analytics has_imported
    has_analytics=$(sqlite3 "$APP_DB" "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='call_analytics';")
    has_imported=$(sqlite3 "$APP_DB" "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='imported_calls';")
    if [[ "$has_analytics" == "1" ]]; then
      app_analytics_rows=$(sqlite3 "$APP_DB" "SELECT COUNT(*) FROM call_analytics WHERE source='derived_from_real_calls';")
    fi
    if [[ "$has_imported" == "1" ]]; then
      app_imported_rows=$(sqlite3 "$APP_DB" "SELECT COUNT(*) FROM imported_calls;")
    fi
    if [[ "$DRY" != "1" ]]; then
      {
        echo "PRAGMA secure_delete=ON;"
        [[ "$has_analytics" == "1" ]] && echo "DELETE FROM call_analytics WHERE source='derived_from_real_calls';"
        [[ "$has_imported" == "1" ]] && echo "DELETE FROM imported_calls;"
        echo "VACUUM;"
        echo "PRAGMA wal_checkpoint(TRUNCATE);"
      } | sqlite3 "$APP_DB" >/dev/null
    fi
  fi

  # ---- 3. Claude Code session logs ---------------------------------------------------------------
  if [[ -d "$CLAUDE_DIR" ]]; then
    claude_files=$(find "$CLAUDE_DIR" -type f \( -name '*.jsonl' -o -path '*/tool-results/*' \) | wc -l)
    if [[ "$DRY" != "1" ]]; then
      find "$CLAUDE_DIR" -type f \( -name '*.jsonl' -o -path '*/tool-results/*' \) -exec shred -u -- {} +
      find "$CLAUDE_DIR" -depth -type d -name tool-results -empty -delete
    fi
  fi

  # ---- 4. service account --------------------------------------------------------------------------
  if [[ "${ROOT%/}" == "$DEFAULT_ROOT" ]] && id walnut-pipeline >/dev/null 2>&1; then
    user_removed=1
    if [[ "$DRY" != "1" ]]; then
      userdel walnut-pipeline || user_removed=0
    fi
  fi

  echo "dry_run=$DRY"
  echo "files_shredded=$files_shredded"
  echo "dirs_removed=$dirs_removed"
  echo "app_call_analytics_rows_deleted=$app_analytics_rows"
  echo "app_imported_calls_rows_deleted=$app_imported_rows"
  echo "claude_log_files_shredded=$claude_files"
  echo "user_removed=$user_removed"
}

# Single line on purpose: this script lives inside WIPE_ROOT and shreds itself; bash must not read
# further from the (overwritten) file after main returns.
main "$@"; exit $?
