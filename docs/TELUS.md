# TELUS Business Connect connector (read only)

The clinic phone system is TELUS Business Connect, which is built on RingCentral. This connector reads call
metadata through the RingCentral REST API into the private pipeline database. It can look, it cannot change
anything. Code: `pipeline/telus/`. Tests: `pipeline/tests/test_telus.py` (mocked HTTP only).

## What it does

* `telus-setup` stores the API credentials in a mode 600 file under the private root.
* `telus-check` logs in and prints a short summary: environment, server host, scopes granted, whether the four
  needed read scopes are present, token lifetime, and how many calls there were in the last 7 days. It prints no
  phone numbers, names or tokens. It exits non-zero if a read scope is missing or any write-capable scope is
  present.
* `telus-pull-calllog` copies call-log metadata into two new tables in `<root>/private.db`: `telus_calls` and
  `telus_sync_state`. It prints counts only.
* `python -m pipeline analytics` also emits `telus_calls_by_hour`, `telus_missed_by_hour`,
  `telus_missed_share` and `telus_voicemail_share` from `telus_calls`, with the usual rule that any cell with
  n < 5 is suppressed. Shares are over inbound calls. Hourly values are the average per weekday for hours 8 to 17,
  local time. If `telus_calls` is empty, none of these rows are written.

## Read-only guarantees

All HTTP goes through `ReadOnlyClient` (`pipeline/telus/client.py`). Its single request choke point enforces:

1. Only GET requests, and only to paths on the allowlist below. Anything else raises `ReadOnlyViolation`
   before a request object is built (nothing is sent, not even a login).
2. The one non-GET request is the login POST, made by a private method, and allowed only to the exact path
   `/restapi/oauth/token`.
3. The client has no `post`, `put`, `patch` or `delete` methods. `request(method, ...)` exists only so a wrong
   verb fails loudly.
4. Redirects are not followed. The `nextPage` URI in a response is never followed; pages are requested by
   number against the same allowlisted path.
5. Headers, tokens, query strings and response bodies are never logged. Exception messages contain the HTTP
   status and the path only, and are passed through redaction of the known secret values.
6. `telus-check` and `telus-pull-calllog` refuse a token that carries any write-capable scope. A scope counts as
   write-capable if its name does not start with `Read` or contains words such as Edit, Write, Send, Manage,
   Admin, Create, Delete, Update, Control. This is deliberately strict.
7. Waiting is bounded: at most 3 retries on HTTP 429 (waits `Retry-After`, else `X-Rate-Limit-Window`, capped at
   90 seconds) and on timeouts; one re-login on token expiry or HTTP 401.

Stored per call: id, local start time (America/Vancouver), duration, direction, a result from a fixed list
(`answered`, `missed`, `voicemail`, `hangup`, `failed`, `other`), extension id, `has_recording`, and HMAC-SHA256
hashes of the two phone numbers (digits only, same salt `<root>/salt.bin` and same normalisation as `load`, so
they match `calls.number_hash`). Raw numbers, names and free text are never stored. Recording audio and
voicemail are not downloaded by any command.

## GET allowlist

All under `/restapi/v1.0/account/~`. The list lives in `pipeline/telus/endpoints.py` (`GET_ALLOWLIST`).

| Path | Use |
|---|---|
| `/restapi/v1.0/account/~` | account info |
| `/call-log` | account call log (used by check and pull) |
| `/extension/~/call-log` | extension call log |
| `/extension/~/message-store` and `/extension/~/message-store/{id}` | voicemail and SMS list |
| `/recording/{id}` and `/recording/{id}/content` | recording metadata and audio |

Matching is a full match with no query string, no `..`, no `//`. Ids may contain letters, digits, `_`, `-`, `~`.

## Permissions to request

Create the app (or ask TELUS to create the credentials) with exactly these scopes and nothing else:

* `ReadCallLog`
* `ReadCallRecording`
* `ReadMessages`
* `ReadAccounts`

Do not add SMS, RingOut, call control, edit or admin scopes. `telus-check` fails if they are present.

## How to run

```
cd /opt/walnut-desk
P="sudo -u walnut-pipeline /opt/walnut-desk/pipeline/.venv/bin/python -m pipeline"
$P telus-setup            # hidden prompts, one value at a time; add --force to replace an existing file
$P telus-check            # must exit 0 before the first pull
$P telus-pull-calllog     # incremental; add --days N or --since 2026-08-01 to choose the window
$P analytics
```

`--since` takes ISO 8601; a value without a time zone is read as America/Vancouver local time. Without
`--since` or `--days`, the first run pulls the last 30 days and later runs continue from the last finished
sync minus one day (calls that were still in progress get their final values). Pages are committed as they
arrive and an interrupted pull resumes at the next page of the same window. Re-running never duplicates
rows (upsert by `telus_id`).

`telus-setup` is interactive only. There is no way to pass credentials on the command line or from the
environment, on purpose. The terminal limits one hidden line to about 4000 characters, which is enough for a
JWT.

## Where credentials live

`<root>/telus/credentials.env` (default `/srv/walnut-private/telus/credentials.env`), written atomically with
umask 077 into a mode 700 directory. Keys: `TELUS_ENV` (`sandbox` or `production`), `TELUS_SERVER_URL`,
`TELUS_CLIENT_ID`, `TELUS_CLIENT_SECRET`, `TELUS_JWT`. The loader refuses the file unless it is a regular file
(not a symlink), owned by the current user, with mode exactly 600. It is never committed. It is deleted with
the rest of the root by `WIPE.sh`. Access tokens are kept in memory only.

## Sandbox versus production

| | sandbox | production |
|---|---|---|
| `TELUS_ENV` | `sandbox` | `production` |
| default server | `https://platform.devtest.ringcentral.com` | `https://platform.ringcentral.com` |
| data | RingCentral developer test data | the clinic's real calls |

The sandbox from developers.ringcentral.com is not the clinic's account. It holds RingCentral test data, not
TELUS Business Connect data, and a sandbox app does not work against production. Sandbox runs only prove the
connector works; they say nothing about what the clinic's account allows. Server URL is overridable
(`TELUS_SERVER_URL`, https only, no path) in case TELUS fronts the API on a different host.

## Unverified (check with real credentials)

* Whether TELUS exposes the RingCentral developer portal for the clinic's account, who issues the app and
  JWT, and which server URL they require.
* The exact scope strings a TELUS-issued JWT returns, and whether extra benign scopes appear (they would be
  flagged as write-capable by the strict rule; adjust `endpoints.is_write_capable` if so).
* Whether the account-level `/account/~/call-log` is permitted for the credential (it may need an admin
  extension), or whether the extension-level log is needed.
* Field names in call-log records (`id`, `startTime`, `duration`, `direction`, `result`, `from.phoneNumber`,
  `to.phoneNumber`, `extension.id`, `recording.id`) and the full list of `result` values; unknown results are
  stored as `other` (see `RESULT_MAP` in `pipeline/telus/calllog.py`).
* Paging fields: the pull follows `navigation.nextPage` or `paging.totalPages`; `telus-check` uses
  `paging.totalElements` if present, else counts one page of up to 1000.
* The `view=Simple` parameter, the default sort order, and whether `dateFrom`/`dateTo` bound by start time.
* Rate-limit headers and exact limits (the client honours `Retry-After` and `X-Rate-Limit-Window`).
* The token response fields `expires_in` and `scope`, and that a refresh token may appear (it is ignored).
* How a voicemail that rang out is labelled (`Voicemail` versus `Missed`), which affects the shares.

## Recordings button (worker)

This section supersedes two statements above: recording audio IS now downloaded, but only by `telus-worker`
and only when an admin has switched it on, and `ReadOnlyClient.download_recording` may follow ONE redirect
(see "Download rules"). Everything else above still holds. Binding contract: `docs/CONTRACT_TELUS_RECORDINGS.md`.
Code: `pipeline/telus/worker.py`, `pipeline/telus/recordings.py`, `ReadOnlyClient.download_recording`.
Tests: `pipeline/tests/test_telus_recordings.py` (mocked HTTP only).

### Flow

1. An admin presses a button in the web app (Admin > TELUS). The app (user `walnut-app`) writes a small request
   file into the bridge folder. It never touches TELUS, the credentials or `/srv/walnut-private`.
2. `walnut-telus.path` starts `walnut-telus.service` (user `walnut-pipeline`), which runs
   `python -m pipeline telus-worker [--root /srv/walnut-private] [--bridge /var/lib/walnut-desk-telus]`.
3. The worker validates every request, handles them oldest first, one at a time, deletes each request file
   when handled, and writes `status.json` after each step. Then it exits. A lock file under `<root>/telus/`
   stops two workers running at once.
4. The app polls `status.json` and shows counts and plain-English text for the code.

Mode `check`: login, scope check (same strict rules as `telus-check`), 7-day call count, updates `connection`.
Mode `recordings`: refuse unless `recordings_enabled` is true in `config.json`; login; refuse if any
write-capable scope is present; pull the call log for the last `days` days (this also stores `recording_id`
and backfills it on the 1289 rows that predate it); download recordings not yet downloaded, newest first, up
to `min(max_items, max_per_run_cap)`; delete our own copies older than `keep_days`.

### Bridge folder

`/var/lib/walnut-desk-telus/`, group `walnut-telus` (members `walnut-app` and `walnut-pipeline`), mode 2770,
files 660.

| File | Written by | Read by |
|---|---|---|
| `requests/<32 hex>.json` | app | worker (then deleted) |
| `config.json` | app | worker |
| `status.json` | worker (temp file, fsync, chmod 660, rename) | app |

Request files: name `^[0-9a-f]{32}\.json$`, regular file (checked with `lstat`, opened with `O_NOFOLLOW`),
at most 4096 bytes, JSON validated by pydantic with unknown keys forbidden and strict types. A bad entry
(wrong name, symlink, directory, too big, malformed, wrong id, out of range) is deleted and counted in
`rejected_requests`. The counter is cumulative across runs. A malformed file younger than 2 seconds gets one
more read before it is rejected, in case the app is still writing it. `days` and `max_items` are required for
`recordings` and optional for `check`. `config.json`: missing or invalid means defaults with
`recordings_enabled` false.

### Status codes (fixed list, nothing else ever appears)

`ok`, `partial`, `recordings_disabled`, `not_configured`, `login_failed`, `write_scope_present`,
`disk_cap_reached`, `disk_low`, `rate_limited`, `no_recordings`, `error`.

* `partial`: some items downloaded and some failed. `error`: nothing downloaded and some failed, a missing
  required read scope, or an unexpected failure (only its type is logged, never its text).
* A state `unknown` connection carries code `ok` with no meaning; read `state`.
* Job state is `done` for `ok`, `partial`, `no_recordings` and for the stop codes `disk_cap_reached`,
  `disk_low`, `rate_limited` when at least one file was downloaded; otherwise `failed`.
* `status.json` holds only counts, fixed codes, the requester's display name and timestamps. The tests scan
  every status document written for recording ids, call ids, numbers, URLs, secrets and exception text.

Per-item results are kept in `telus_recordings` (`downloaded`, `failed`, `skipped` with a short `error_code`
such as `http_500`, `not_audio`, `too_large`, `gone`, `expired`). `failed` items are retried on the next run;
`skipped` ones (too large, HTTP 404/410, expired by `keep_days`) are not.

### Download rules

* GET only, through `ReadOnlyClient`, on `/restapi/v1.0/account/~/recording/{id}/content` only. The id must
  match `[A-Za-z0-9_-]{1,64}`.
* At most one redirect, and only to an https host with no port, no user info and an exact match in
  `endpoints.REDIRECT_HOSTS`: production `media.ringcentral.com`, `platform.ringcentral.com`; sandbox
  `media.devtest.ringcentral.com`, `platform.devtest.ringcentral.com`. Any other target raises
  `ReadOnlyViolation` before a request is built, so nothing, and no Authorization header, reaches it. A second
  redirect is refused. The Authorization header goes only to the TELUS server host and those hosts.
* Requires HTTP 200, a non-empty body, `Content-Type` `audio/*` or `application/octet-stream`, at most
  200 MB (checked on `Content-Length` and again while streaming). The body streams into a hidden temp file
  (`.<id>.<pid>.part`) in the final directory, is fsynced, then renamed. Any failure removes the temp file.
* Files: `<root>/recordings/YYYY/MM/<recording_id>.<ext>`, mode 600, every directory 700. `YYYY/MM` is the
  call's local date. `ext` comes from the content type (`mp3`, `wav`, `ogg`, `m4a`, `aac`, `webm`, `flac`, else
  `bin` or `audio`). The SHA-256 and byte count are stored in `telus_recordings`.
* Rate limits: HTTP 429 waits `Retry-After` (else `X-Rate-Limit-Window`), capped at 90 s, at most 3 retries,
  then the job stops with `rate_limited`. A response with `X-Rate-Limit-Remaining: 0` makes the worker wait
  `X-Rate-Limit-Window` (capped at 90 s) before the next file. There is a 1 second pause between files. After 10
  failures in a row the job stops. Sleeps are injectable, so tests run instantly.
* URLs, tokens, ids and bodies are never logged. Audio is never transcribed or sent anywhere.

### Disk rules

* Cap: `max_gb` (1 to 50, GiB) is the limit on the total size of finished files under `recordings/`. Before
  each file the worker allows `min(200 MB, cap - used, free - 5 GiB)`. If the cap has no room it stops with
  `disk_cap_reached`; if free space on the volume would fall under 5 GiB it stops with `disk_low`. A file that
  would not fit in the remaining room aborts (no partial file) and stops the job with the matching code.
  A write failing with "no space" also stops with `disk_low`.
* `keep_days` (1 to 90): after downloading, files whose `telus_recordings` row says `downloaded` more than
  `keep_days` days ago are securely deleted (overwritten with zeros, fsynced, unlinked) and the row becomes
  `skipped` with `expired`, so they are not fetched again. Only files that match one of our own rows are
  touched, never symlinks, never anything outside `recordings/`. Leftover `.part` files older than an hour
  are removed too. Empty month and year folders are removed. This runs only for an enabled `recordings` job.
  Secure delete is best effort on copy-on-write or journaling filesystems and SSDs; see the WIPE.sh caveats.
* `storage` in the status (count, bytes, free bytes, cap) is measured from the folder on every write.

### Database changes

`telus_calls.recording_id TEXT` is added with `ALTER TABLE` only if missing (every `dbm.connect`), and the
pull now stores `recording.id` and updates it on existing rows. Table `telus_recordings(recording_id PK,
telus_id, status, bytes, sha256, ext, downloaded_at, error_code)` is created if missing. The worker's pull for
a `days` window does not move the incremental cursor used by `telus-pull-calllog`.

### Unverified (check with real credentials)

Do these on the server as `walnut-pipeline`, with a throwaway request, and read only counts and codes.

| Unknown | How to verify |
|---|---|
| Audio format (MP3 or WAV, sizes) | Run one small job, then `ls -l` the file extension and sizes under `recordings/` (do not play or open it). The `ext` and `bytes` columns in `telus_recordings` show the same. If many are `bin` or `audio`, add the real type to `EXT_BY_TYPE`. |
| Media host for the redirect | Run one job. `redirect_blocked` in `telus_recordings.error_code` means TELUS redirected somewhere not on `REDIRECT_HOSTS`; find the host in the TELUS/RingCentral docs (never copy a signed URL out of a log), then add it to `endpoints.REDIRECT_HOSTS` deliberately. No redirect at all is also fine. |
| Rate-limit numbers | Run a job with `max_items` 200 and watch the elapsed time and whether `rate_limited` appears. Tune `FILE_SLEEP` and `MAX_RETRIES` from the observed `X-Rate-Limit-*` headers in the RingCentral docs. |
| Permission to fetch other users' recordings | Run one job. Many `http_403` (or `http_404`) codes in `telus_recordings.error_code` mean the credential's extension cannot read recordings of other extensions; ask TELUS for an admin-level read of the account call recordings. |
| `view=Simple` includes `recording` | After a first job, `SELECT COUNT(*) FROM telus_calls WHERE recording_id IS NOT NULL` against `has_recording=1`. If they differ the call log view needs to be `Detailed`. |
| TELUS retention period | Pull a long window (`--days 90` with `telus-pull-calllog`), then request recordings for the oldest calls with a job and see from which age `http_404` / `gone` starts. Download before that age, and set `keep_days` knowing it. |
| Write-scope strictness for a real JWT | `telus-check` (see above) must show `write_capable_scopes: none`; otherwise every job ends with `write_scope_present`. |

## Live numbers (telus-sync)

Feeds the "From the phones" part of the Brief. Binding contract: `docs/CONTRACT_LIVE_BRIEF.md`. Code:
`pipeline/telus/live.py` (metrics), `pipeline/telus/sync.py` (pull, validate, write), `Worker._sync` and
`Worker.run_sync_command` in `pipeline/telus/worker.py`. Tests: `pipeline/tests/test_telus_live.py` (made-up
data, mocked HTTP).

### How it runs

* `python -m pipeline telus-sync [--root] [--bridge /var/lib/walnut-desk-telus] [--days 35]` logs in
  read-only, refuses a token with any write-capable scope (code `write_scope_present`), pulls the last `--days`
  days (1 to 90) with `calllog.pull(track_state=False)`, so the cursor used by `telus-pull-calllog` is never read
  or moved, then computes the metrics from `telus_calls` and the `labels` table, validates them and writes
  `<bridge>/analytics.json` (temp file, fsync, chmod 660, rename, fsync folder). It exits 0 only on code `ok`.
  It takes the same lock as `telus-worker` (`<root>/telus/.worker.lock`); if a worker is running it prints
  `busy` and exits 1, and the next day's run catches up. It does not touch the `job` part of `status.json`.
* Request mode `sync` (same request file as `check`; `days` 1 to 30 and `max_items` optional, `max_items` is
  not used). `telus-worker` runs the same code and `days` defaults to 35. `job.state` is `done` for `ok`.
* `status.json` gets a new key `sync`: `{"state":"ok"|"failed","code":"<fixed code>","finished_at":"<ISO UTC>",
  "rows":<int>}`. It appears after the first sync, so older files and readers without it keep working. A failed
  run keeps the previous `rows` and leaves the previous `analytics.json` untouched. Codes come from the usual
  fixed list (`ok`, `not_configured`, `login_failed`, `write_scope_present`, `rate_limited`, `error`); exception
  text, numbers, hashes and ids never reach the status, the file or the logs.
* Timer: `deploy/systemd/walnut-telus-sync.timer` runs `walnut-telus-sync.service` every day at 06:20
  America/Vancouver (`Persistent=true`, so a missed run happens at the next boot). The service is a oneshot as
  `walnut-pipeline` with the same hardening as `walnut-telus.service`, `SupplementaryGroups=walnut-telus`,
  `UMask=0007`, `ReadWritePaths=/srv/walnut-private /var/lib/walnut-desk-telus`. Install by copying both files
  to `/etc/systemd/system/`, then `systemctl daemon-reload && systemctl enable --now walnut-telus-sync.timer`.
  The files are in the repo only; nothing has been installed.

### Definitions

All days are America/Vancouver local days (the `date`, `hour` and `weekday` columns were computed in that zone,
so the daylight-saving change needs no special case). `generated_at` is the sync time; "today" is its local
date. `7d` is the 7 full local days ending yesterday, `prev7d` the 7 before those. `window` in the file is the 28
full days ending yesterday. Unanswered means an inbound call whose result is `missed`, `voicemail` or `hangup`.
Rows are `{metric, dim1, dim2, value, n}` (at most 2000 rows, 256 KB, strings up to 40 chars of `[A-Za-z0-9_.:-]`).
Counts are never dropped for being small except where stated. With an empty `telus_calls` there are no rows.

| Metric | Definition |
|---|---|
| `live_calls` (period, `all`/`inbound`/`outbound`) | Calls in the period. `all` also counts calls whose direction is neither. |
| `live_inbound_result` (period, result) | Inbound calls by result. `failed` and any unknown result count as `other`. |
| `live_missed_followup` (period, bucket) | Each unanswered inbound call counted once, see below. |
| `live_after_hours` (period, `inbound`/`voicemail`) | Inbound calls (and the voicemail ones) that started after hours: Monday to Friday before 08:00 or from 18:00, Saturday before 09:00 or from 14:00, all Sunday. |
| `live_inbound_by_hour`, `live_missed_by_hour` (hour 8 to 17) | Inbound (or unanswered inbound) calls on Monday to Friday in that local hour over the 28 days, divided by the number of weekdays in those 28 days (20). `n` is the total count. Dropped when `n` is under 5. |
| `live_busiest_hour` (hour) | The hour among 8 to 17 with the most inbound calls (ties go to the earlier hour); `value` is its weekday average. Dropped when `n` is under 5. |
| `live_daily_inbound` (date) | Inbound calls on each of the last 14 days (including weekends). Days with `n` under 5 are dropped. |
| `live_past_followup_share` (`past_recordings`) | Share of the past labeled calls with `promised_followup` true, from the rules-v1 labels with gold winning, leaving out calls flagged private. `n` is the number of calls counted. Dropped when `min(promised, n)` is under 5, the same cell rule as `analytics`. Absent when there are no labels. |

`live_missed_followup` buckets, matched only by the salted number hashes in memory (index by hash, binary
search, no pairwise scan). For an unanswered inbound call at time t, in this order:

1. `too_recent`: the call is under 24 h old at `generated_at` (exactly 24 h is not recent). This wins even if a
   follow-up is already visible, because the 24 h window is still open.
2. `no_contact_24h`: the call has no number hash (a missing number never matches another missing number).
3. `rang_again_1h`: the same `from_hash` made another inbound call after t, up to and including t + 1 h.
4. `called_back_4h`: the first outbound call with `to_hash` equal to that `from_hash` after t is within 4 h
   (inclusive).
5. `called_back_24h`: that first outbound call is within 24 h (inclusive).
6. `no_contact_24h`: none of the above. A caller who rang again after more than 1 h, but was never phoned,
   counts here, because the contract's bucket means "none of these within 24 h".

Earlier calls never count, and an outbound call counts whatever its result (an attempt is a contact).

### Estimated or unverified

* Voicemail labelling: how TELUS marks a call that rang out into voicemail (`Voicemail` versus `Missed`) is
  unverified, so the split between `missed` and `voicemail` in `live_inbound_result` and `live_after_hours` may
  be off. The sum of unanswered calls is less affected.
* `hangup` is mapped from the result strings `Hang Up` and `Abandoned`. Whether a caller who hung up while
  waiting in the queue arrives as one of these (rather than `Missed` or `Call connected`) is unverified.
  Failed calls (`failed`) are not unanswered by this definition.
* An outbound call is treated as a callback by its `to` number only. Calls routed through an extension, a
  forwarded number or a different caller line will not match, so `no_contact_24h` can overstate the callers who
  never heard back (a person may also have been reached by text or by a call that is not in this log).
* A repeat call within 1 h counts as a contact whatever happened to it (it may have been missed again).
* Numbers are normalised to 10 digits before hashing, as in `load`; two different formats of one line only match
  if they normalise the same way.
* The hourly average divides by the weekdays in the window, public holidays included, so it is slightly low.
* The past follow-up share is the rules labeler's view (gold accuracy 0.92 per the contract).
