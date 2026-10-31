# Contract: TELUS recordings button (app side and pipeline side)

Binding for both builders. Extends `docs/CONTRACT.md` and `docs/TELUS.md`. No em dashes anywhere. Repo is public: no secrets, no real data.

## Goal

An admin presses a button in the web app (Admin > TELUS). The server then downloads new call recordings from TELUS, read-only, into the private store. **The web app never touches TELUS credentials, recordings or the private directory.** It only drops a small request file in a shared "bridge" folder and reads back a counts-only status file. A privileged-separate worker (user `walnut-pipeline`) does the work.

## Separation of duties (do not break)

- `walnut-app` (web app): may read and write only the bridge folder, never `/srv/walnut-private`, never `credentials.env`.
- `walnut-pipeline` (worker): reads the request, runs the download, writes the status. Never reads the app database.
- Everything crossing the bridge is untrusted by the receiver and must be validated strictly.

## Bridge folder

`/var/lib/walnut-desk-telus/` (override with env `WALNUT_TELUS_DIR` for the app, `--bridge` for the pipeline). Group `walnut-telus` (members: `walnut-app`, `walnut-pipeline`), mode 2770, files mode 660. Layout:

```
requests/<job_id>.json    app writes, worker reads then deletes
config.json               app writes (settings), worker reads
status.json               worker writes atomically, app reads
```

### requests/<job_id>.json
File name must match `^[0-9a-f]{32}$` + `.json`. Regular file only (no symlinks), at most 4096 bytes.
```
{"v":1,"id":"<same 32 hex>","mode":"check"|"recordings","days":1..30,"max_items":1..1000,
 "requested_by":"<display name, max 60 chars, printable>","requested_at":"<ISO 8601 UTC>"}
```
Unknown keys are rejected. A bad file is deleted and counted as `rejected_requests` in status. The worker clamps `max_items` to config `max_per_run_cap`.

### config.json
```
{"v":1,"recordings_enabled":false,"max_gb":10,"keep_days":30,"max_per_run_cap":200,
 "updated_by":"<name>","updated_at":"<ISO UTC>"}
```
Ranges: `max_gb` 1..50, `keep_days` 1..90, `max_per_run_cap` 1..1000. Missing or invalid file means defaults above with `recordings_enabled` false. Recording downloads are refused unless `recordings_enabled` is true.

### status.json (counts only, never names, numbers, text, call ids or recording ids)
```
{"v":1,"updated_at":"<ISO UTC>",
 "connection":{"state":"unknown"|"ok"|"failed","code":"<code>","checked_at":"<ISO>|null","environment":"production"|"sandbox"|null,
               "write_capable_scopes":false,"calls_last_7_days":<int|null>},
 "job":{"id":"<32 hex>|null","mode":"check"|"recordings"|null,"state":"idle"|"queued"|"running"|"done"|"failed",
        "requested_by":"<name>|null","requested_at":"<ISO>|null","started_at":"<ISO>|null","finished_at":"<ISO>|null",
        "code":"<code>","found":0,"downloaded":0,"already_had":0,"skipped":0,"failed":0,"remaining":0,"deleted_old":0},
 "storage":{"recordings_count":0,"bytes":0,"free_bytes":0,"max_bytes":0},
 "rejected_requests":0}
```
Codes (fixed list, the app maps each to plain English): `ok`, `partial`, `recordings_disabled`, `not_configured`, `login_failed`, `write_scope_present`, `disk_cap_reached`, `disk_low`, `rate_limited`, `no_recordings`, `error`. The worker never puts free text from TELUS or exceptions into status.

## Worker (pipeline side)

- CLI `python -m pipeline telus-worker [--root /srv/walnut-private] [--bridge /var/lib/walnut-desk-telus]`: processes all valid requests oldest first, one at a time, then exits. Started by systemd `walnut-telus.path` when `requests/` becomes non-empty.
- Mode `check`: login, scope check (same rules as `telus-check`), 7-day count, write `connection`.
- Mode `recordings`: refuse if not enabled; login and scope check (refuse with `write_scope_present` if any write-capable scope); pull the call log for the last `days` days (reuse `telus-pull-calllog` logic, now also storing `recording_id`); download recordings not yet downloaded for calls in that window, newest first, up to `max_items`; delete our own copies older than `keep_days`; update `status.json` every 10 items.
- Download rules: GET only, through `ReadOnlyClient`. Content path `/restapi/v1.0/account/~/recording/{id}/content`. If TELUS redirects, follow at most one redirect and only to an allowlisted `https` host (production `media.ringcentral.com` and `platform.ringcentral.com`; sandbox `media.devtest.ringcentral.com` and `platform.devtest.ringcentral.com`), sending the Authorization header only to those hosts. Stream to a temp file in the same directory, require HTTP 200, non-empty, Content-Type `audio/*` or `application/octet-stream`, max 200 MB, then fsync and rename. File mode 600, directory 700. Respect `Retry-After` and `X-Rate-Limit-*` headers with bounded retries, sleep between files. Never log URLs, tokens or bodies.
- Stored at `<root>/recordings/YYYY/MM/<recording_id>.<ext>` plus a row in `telus_recordings` (recording_id, telus_id, status downloaded|failed|skipped, bytes, sha256, ext, downloaded_at, error_code). Disk rule: stop with `disk_cap_reached` when total recordings bytes would pass `max_gb`, and with `disk_low` when free space on the volume would fall under 5 GB.
- Audio is never transcribed or sent anywhere by this feature.

## App side

- New admin permission `admin.telus` (in `ADMIN_PERMS`, needs `is_admin`). Every new route is tagged so the permission-walker test covers it.
- `GET /api/admin/telus` returns `{"settings":{...config...},"status":{...parsed status, validated...},"bridge_ok":bool,"messages":{code:text}}`. A missing, oversized, malformed or stale status file gives `"status":null` and `bridge_ok` as appropriate; never raises.
- `PUT /api/admin/telus/settings` body `{recordings_enabled,max_gb,keep_days,max_per_run_cap}` validated with the ranges above; writes `config.json` atomically (temp file, chmod 660, rename); records an Admin event.
- `POST /api/admin/telus/check` and `POST /api/admin/telus/recordings {days, max_items}`: validate; refuse with 409 if a job is queued or running (read from status); refuse the recordings mode with 400 and plain text if `recordings_enabled` is false; write the request file (uuid4 hex name, chmod 660); record an Admin event `telus_check_requested` or `telus_recordings_requested` (no content). Return `{"job_id":...}`.
- Everything reading the bridge must be bounded (read at most 64 KB, no symlink following, ignore anything not matching the schema).
- The app never imports or calls anything under `pipeline/`.

## UI (Admin > TELUS tab, admin only)

Plain short words. Sections: Connection (state, last checked, calls in last 7 days, "Check connection" button), Recordings (the privacy note, an On/Off switch "Allow downloading call recordings" default Off, number boxes for storage limit in GB, keep for days, max per click, a "Download new recordings" button with a confirm dialog), Last run (state with a progress line while running, then found, downloaded, already had, skipped, failed, remaining, deleted old), Storage (count, size, free space). Poll `GET /api/admin/telus` every 3 seconds while a job is queued or running, otherwise every 30 seconds. Privacy note text: "Recordings are real client conversations. They are saved only in the private folder on the server and are never shown in this app."
