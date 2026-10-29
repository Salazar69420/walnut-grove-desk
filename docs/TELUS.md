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
