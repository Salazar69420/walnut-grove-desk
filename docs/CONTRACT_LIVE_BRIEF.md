# Contract: live Brief (real phone numbers on Dr. Garg's dashboard)

Binding for both builders. Extends `docs/CONTRACT_TELUS_RECORDINGS.md`, `docs/TELUS.md`, `docs/CONTRACT_WAVE2.md`. No em dashes. Repo is public: no secrets, no real data, no real-looking ids.

## Goal and privacy line

The Brief shows REAL numbers computed from the clinic's TELUS call log. **Counts only.** No caller names, phone numbers, call ids, transcript text or quotes ever reach the app, its database, its logs or its UI. Small cells (n under 5) are dropped by the pipeline. Tiles with no real source stay clearly tagged "Example". The app never touches TELUS credentials, recordings or the private store; it only reads `analytics.json` from the bridge folder.

## Data flow

TELUS (read-only) -> pipeline `telus-sync` (user walnut-pipeline, private store, number hashes) -> `/var/lib/walnut-desk-telus/analytics.json` (aggregate rows only) -> app ingest (validated, bounded) -> `call_analytics` rows `source = 'telus_live'` -> Brief, assistant answers, suggestion rules.

## analytics.json (written atomically by the worker, mode 660)

```
{"v":1,"generated_at":"<ISO UTC>","timezone":"America/Vancouver",
 "window":{"from":"YYYY-MM-DD","to":"YYYY-MM-DD"},
 "rows":[{"metric":"<name>","dim1":"<str|null>","dim2":"<str|null>","value":<number>,"n":<int>}]}
```
Max 2000 rows, file max 256 KB. Strings max 40 chars, charset `[A-Za-z0-9_.:-]`. Anything else is rejected by the app.

### Metrics (all in the TELUS local day, America/Vancouver; "7d" = the 7 full days ending yesterday, "prev7d" = the 7 days before that)

| metric | dim1 | dim2 | value |
|---|---|---|---|
| `live_calls` | period `7d`/`prev7d` | direction `all`/`inbound`/`outbound` | call count |
| `live_inbound_result` | period | result `answered`/`missed`/`voicemail`/`hangup`/`other` | inbound call count |
| `live_missed_followup` | period | `rang_again_1h`, `called_back_4h`, `called_back_24h`, `no_contact_24h`, `too_recent` | count of inbound unanswered calls (missed, voicemail, hangup) by what happened next, matched ONLY by salted number hashes: same caller phoned again within 1 h; the clinic phoned that number within 4 h; within 24 h; none of these within 24 h; call under 24 h old |
| `live_after_hours` | period | `inbound`/`voicemail` | count; after hours = weekdays before 08:00 or after 18:00, Saturday before 09:00 or after 14:00, all Sunday |
| `live_inbound_by_hour` | hour `8`..`17` | null | average inbound calls per weekday in that hour over the last 28 days (n = total count) |
| `live_missed_by_hour` | hour `8`..`17` | null | same for unanswered inbound |
| `live_busiest_hour` | hour | null | value = average per weekday; the single busiest hour |
| `live_daily_inbound` | `YYYY-MM-DD` | null | inbound calls that day, last 14 days |
| `live_past_followup_share` | `past_recordings` | null | share of past labeled transcripts (Jul 29 to Oct 26) where a follow-up was promised, from the rules labeler (gold accuracy 0.92), n = calls; dropped if n under 5 |

Rates are computed by the app, not stored. Missing metric means "no data yet": the UI shows "Not available yet", never a made-up value.

## Worker additions (pipeline side)

- New CLI `python -m pipeline telus-sync [--root] [--bridge] [--days 35]`: windowed call-log pull (never moves the `telus-pull-calllog` cursor), computes the metrics above from `telus_calls` hashes and the past labels table, writes `analytics.json` atomically, updates `status.json.sync` = `{"state":"ok|failed","code":"<code>","finished_at":"<ISO>|null","rows":<int>}` (add this key to the status contract; the app must tolerate its absence).
- Request mode `sync` (request file contract: `days` and `max_items` optional, as for `check`). `telus-worker` handles it by running the same code.
- systemd: `walnut-telus-sync.service` (oneshot as walnut-pipeline, same hardening as walnut-telus.service, ExecStart `... -m pipeline telus-sync`) and `walnut-telus-sync.timer` (daily 06:20 America/Vancouver, plus `Persistent=true`).
- Hash matching must never expose or log numbers. Tests prove no raw number, hash or id appears in analytics.json (scan it).

## App additions

- `app/services/live_ingest.py`: bounded safe read (lstat, O_NOFOLLOW, 256 KB), strict schema, replaces all `call_analytics` rows with `source='telus_live'` in one transaction, records an event (tool "Call logger", text "Live call numbers updated: N rows", no content), and publishes the `analytics` SSE entity. Ingest runs: (a) inside `python -m app.rules` (the existing 15-minute timer), (b) after a successful `POST /api/admin/telus/sync`. Never raises into the request path.
- `POST /api/admin/telus/sync` (perm `admin.telus`, same 409 and bridge rules as check): writes a request with mode `sync`. UI: an "Update call numbers now" button on the Admin TELUS tab and a line "Call numbers last updated ...".
- `GET /api/brief` gains `live`: `{"available":bool,"as_of":"<ISO>|null","tiles":[...],"missed_followup":{...},"after_hours":{...},"busiest_hour":{...},"daily":[...],"past_followup_share":{...}|null}` and each existing tile gets `"origin":"live"|"example"`. Keep every existing field working.
- Brief screen (`frontend/src/views/Brief.tsx`): a new top row "From the phones" with four REAL tiles: Missed calls last 7 days (with change vs the week before), Callers who never rang back (no contact in 24 h, out of missed), Calls last 7 days (inbound and outbound), After-hours calls last 7 days. A small tag on each: "Real, updated <time>" or "Not available yet". The four old tiles (open callbacks, results waiting, overnight items) get an "Example" tag when their rows are seeded demo data. Add a compact "Last 14 days" inbound bar chart. The existing chart defaults to real hourly volume from `live_inbound_by_hour` when present, labeled "Real recorded call volume, average weekday, last 4 weeks".
- Brief paragraph: templated sentence from the real numbers (the existing number check still guards any model wording).
- Suggestion rules use real numbers when present: `missed_texts` evidence "N calls went unanswered last week and M of those callers had no contact within 24 hours"; `desk_shift` uses `live_inbound_by_hour` (calls per person per hour) with the roster for people on the phones. Example evidence otherwise, labeled example. Existing approval effects unchanged.
- Assistant answers `missed`, `week`, `night`, `first` use the real numbers when present, with "(real, from the phone system)"; otherwise unchanged and labeled example.
- Tests: ingest validation (garbage, oversized, wrong types, symlink, directory, injected strings), replacement is atomic, brief shape with and without live data, tiles origin tags, suggestions evidence, assistant answers, permission walker, no number/name/text in any response. Frontend build with zero TypeScript errors.
