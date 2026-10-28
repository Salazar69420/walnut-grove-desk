# Walnut Grove Desk: build, host and hand over

## Context

A vet clinic (Walnut Grove Animal Hospital, BC, Canada; owner Dr. Garg) wants a real, multi-user staff work app that sits beside ImproMed. It should behave like the single-file prototype `mock-reference.html` (pasted in chat, saved at `.../tool-results/toolu_01413xiU8u3RvasY3Q5YT9Ys.txt`), but with a server, database, accounts, permissions, persistence and live updates, on a public HTTPS URL. A private file of 3,637 real call transcripts must only feed aggregate analytics, a labeled evaluation, and drafts for the owner, under strict privacy rules.

Decisions already made with the user:
- Reference: use the pasted HTML as the source of truth. First implementation step extracts it (from `<!doctype html>` to `</html>`) to `/opt/walnut-desk/reference/mock-reference.html`.
- Hosting: **sslip.io + Caddy** on public IP 77.42.69.194 (`https://77-42-69-194.sslip.io`). Fallbacks: nip.io, then Cloudflare quick tunnel.
- AI provider: the user will supply a **DeepSeek** key later through a new **Admin > Settings > AI provider** section. The app runs fully with no key. OpenRouter stays supported (both are OpenAI-compatible).
- Model split: **Opus plans, orchestrates, reviews, and does all work that reads transcript text.** **Sonnet subagents** write most of the code. **Haiku subagents** do mechanical work (seed data, sample files, docs, config, screenshot runs, audits). No subagent ever receives transcript text, phone numbers or names from the CSV.

## Reconnaissance (done, read-only)

| Item | Finding |
|---|---|
| Host | Hetzner KVM VM "Veto", Ubuntu 26.04 LTS, kernel 7.0, 2 vCPU, 3.7 GiB RAM, no swap, 38 GB disk (34 free) |
| Access | root, systemd running (not a container, no `/.dockerenv`) |
| Network | Public IPv4 77.42.69.194, IPv6 2a01:4f9:c015:2c41::1. Outbound OK to pypi, npm, github, ngrok, duckdns, cloudflare, openrouter, nodesource, caddyserver |
| Listening | only sshd :22 and resolved :53 (local) |
| Firewall | ufw installed, inactive. Hetzner cloud firewall unknown (test in M1) |
| Runtimes | python3 3.14.4 (no pip, no venv package candidate yet: apt lists likely stale), git 2.53, curl 8.18. Missing: node, npm, caddy, cloudflared, sqlite3 CLI, Playwright, uv |
| Env vars | NGROK_AUTHTOKEN, DUCKDNS_*, OPENROUTER_API_KEY all unset |
| CSV | 3,637 rows, UTF-8 with BOM, cols `file,date,time,number,direction,audio_seconds,cost_usd,language,transcript,plain_text`; 2026-07-29 to 2026-10-26; ext 102/101/103 = 2959/412/266; 96.1 h; 0 empty, 0 duplicate ids; 68 calls under 5 s; max 2,841 s; language eng 3597, hin 32, pan 4, fra 2, other 2; direction 3627 "Outgoing" (junk) |

Important finding: the recording file name embeds the caller's phone number, so the raw `call_id` must never be published. Public references use `call_ref = YYYYMMDD-HHMMSS-<ext>-<first 6 hex of salted hash>`; the mapping lives only in the private DB.

## Architecture and stack

- **Backend**: Python 3.12 (managed by `uv`, pinned; avoids 3.14 wheel gaps), FastAPI, SQLAlchemy 2, Alembic, SQLite WAL, argon2-cffi, pydantic v2, httpx, cryptography (Fernet for stored API key). One uvicorn worker on 127.0.0.1:8000 (in-process SSE broadcaster; fine for a small team).
- **Frontend**: React 18 + TypeScript + Vite, react-router, plain CSS copied from the reference tokens (light/dark, `data-theme`). Fonts self-hosted via `@fontsource/figtree` and `@fontsource/ibm-plex-mono` so CSP stays `'self'`. Built to `frontend/dist`, served by FastAPI.
- **Live updates**: `GET /api/stream` (SSE). Every write emits an event `{type, entity, id}`; clients refetch the affected list. Fallback poll every 5 s if SSE drops.
- **Proxy/TLS**: Caddy (official apt repo, pinned) with automatic Let's Encrypt for `77-42-69-194.sslip.io`, HSTS, gzip.
- **Processes**: systemd units `walnut-desk.service` (user `walnut-app`, Restart=always), `walnut-backup.timer` (daily), `walnut-rules.timer` (suggestion rules every 15 min), `caddy.service`. Optional `walnut-tunnel.service` only if fallback is needed.
- **Layout on disk**:
  - `/opt/walnut-desk` git repo (root-owned, world-readable code, no data, no secrets)
  - `/etc/walnut-desk/app.env` (600 walnut-app): SECRET_KEY, FERNET_KEY, DB path
  - `/var/lib/walnut-desk/` (700 walnut-app): `app.db`, `backups/`, `imports/`, `public_url.txt`
  - `/var/lib/walnut-desk-llm/llm.env` (640 walnut-app:walnut-llm): written by the app when the admin saves AI settings; group `walnut-llm` = {walnut-app, walnut-pipeline}, so the pipeline can get the key without reading the app DB
  - `/srv/walnut-private/` (700 walnut-pipeline): CSV, `private.db`, salt, gold labels, outputs, `WIPE.sh`

### Repo layout
```
backend/app/{main.py, config.py, db.py, models/, schemas/, api/, services/, security/, aftercare/ (state machine, emergency), rules/ (suggestions), assistant/ (panel queries), adapters/ (pims, sms, voice, llm), retell/}
backend/alembic/, backend/tests/
frontend/src/{api/, components/, screens/, styles/}
pipeline/ (load, audit, label_rules, label_llm, gold_tool, analytics, purge, verify_no_leak)  # code only, no data
eval/ (scenarios.jsonl paraphrased, run_eval.py)
config/ (templates.yaml, emergency_keywords.yaml)
samples/ (CSV/JSONL examples for every import type)
deploy/ (systemd units, Caddyfile, backup.sh, healthcheck.sh, install.sh, logrotate)
docs/ (PLAN, DECISIONS, SERVER_NOTES, PROGRESS, SECURITY, RETELL, DATA_AUDIT, ANSWER_SHEET_DRAFT, ASSISTANT_EVAL)
```
`.gitignore`: `*.csv` outside `samples/`, `*.db*`, `.env*`, `/srv`, `frontend/dist`, `transcripts/`, `all_transcripts*`.

## Data model (SQLite, all times UTC stored, shown in America/Vancouver)

- `users` (username, email?, display_name, title, role owner|desk|nurse, is_admin, password_hash argon2id, must_change_password, active, failed_count, locked_until)
- `sessions` (token_hash, user_id, csrf_token, created, last_seen, expires, ip, user_agent)
- `events` (ts, tool, actor_id, action, entity_type, entity_id, text, data_json, ip): **the shared record, feed and audit log in one**. SQLite triggers reject UPDATE and DELETE (append-only).
- `callbacks` (client_label, pet_label, what, kind, via, received_at, priority, red_flag, owner_id, status new|claimed|done, source, conversation_id)
- `lab_results` (pet_label, owner_label, test, received_at, stage 0..2, reviewed_by, told_by)
- `drafts` (kind, to_label, body, channel, status pending|sent, edited_by, approved_by, suggestion_id)
- `shifts` (weekday 0..6, person_label, user_id?, role desk|nurse|doc|open, start_min, end_min, proposed bool, suggestion_id)
- `suggestions` (rule_key, title, why, evidence_json, status open|approved|dismissed, decided_by, decided_at)
- `conversations` (channel text|call, client_label, pet_label, kind booking|emergency|callback, status, preferred_slot, summary, messages_json, source seed|sim|retell, is_test, created_by)
- `appointment_requests` (conversation_id, visit_type_id, slot_start, status requested|confirmed|entered|called)
- `emergency_routings` (conversation_id, hospital_id?, matched_terms, layer 1|2|unsure, followed_up_by)
- `sim_sessions` (user_id, channel, node, picks_json, messages_json, done)
- `hospitals` (name, place, phone, address, hours_claim, confirmed, confirmed_by, is_placeholder, sort). Seed: Boundary Bay row, not confirmed, phone/address empty; placeholder backup row.
- `visit_types` (name, minutes, bookable), `open_slots` (start, visit_type?, taken)
- `settings` (key, value_json): clinic name, banner_on, toggles (req, msg, missed), hours, emergency_keywords, wording, rule thresholds, AI provider (key Fernet-encrypted, write-only), Retell (enabled, dry_run, secret hash)
- `call_log_demo` (synthetic example calls, `source=demo`) for the missed-call and week-compare KPIs
- `call_analytics` (source=`derived_from_real_calls`, metric, dim1, dim2, value, n): aggregate only
- `imported_calls` (the documented `calls` schema; `contains_private_content` rows stored but excluded by a single `visible_calls` view used by every UI query and metric)
- `pims_appointments`, `pims_clients`, `pims_patients`, `pims_lab_results` (read-only snapshots from `PimsAdapter`)
- `outbox` (channel sms|voice, to_label, body, status `would_send`)

Seed data = exactly the reference arrays (callbacks c1..c7, results r1..r5, drafts d1..d3, suggestions r1..r4, roster, overnight o1..o5, visit types, hospitals, event log, tool cards), with times re-anchored to "today" in Vancouver. Invented people only. Admin "Reset demo data" reseeds operational tables (never users, settings, analytics, events which get a "demo reset" event).

## API (all under `/api`, JSON, CSRF header on every non-GET)

- Auth: `POST /auth/login`, `POST /auth/logout`, `GET /auth/me`, `POST /auth/change-password`
- Admin: `GET/POST /admin/users`, `PATCH /admin/users/{id}` (role, admin, active), `POST /admin/users/{id}/reset-password`, `GET /admin/audit`, `GET/PUT /admin/settings`, `PUT /admin/settings/ai` (key write-only) + `POST /admin/settings/ai/test`, `POST /admin/import/{type}` (csv|jsonl), `POST /admin/reset-demo`
- Tools: `GET /callbacks`, `POST /callbacks/{id}/claim|done`; `GET /results`, `POST /results/{id}/advance`; `GET /drafts`, `PATCH /drafts/{id}`, `POST /drafts/{id}/approve`; `GET /shifts`; `GET /overnight`, `POST /overnight/{id}/confirm|entered|to-callback|followed-up|ack`
- Brief: `GET /brief` (KPIs, overnight summary, suggestions, chart series), `POST /suggestions/{id}/approve|dismiss`, `POST /suggestions/run` (owner)
- Assistant: `GET /assistant/chips`, `POST /assistant/ask {key?|text}`
- After-hours: `POST /sim/start {channel}`, `POST /sim/{id}/reply {option_index|text}`; `GET/PUT /afterhours/settings`, `PUT /hospitals/{id}`, `POST /hospitals/{id}/confirm`, `PUT /visit-types/{id}`
- Feed: `GET /events?since=`, `GET /stream` (SSE), `GET /tools` (tool cards with event counts)
- Export: `GET /export/{callbacks|results|drafts|shifts|overnight|events|analytics}.csv`
- Retell (404 unless enabled): `POST /retell/functions/{check_emergency|get_open_slots|create_appointment_request|create_callback|get_emergency_hospital}`, `POST /retell/webhook`
- `GET /healthz` (no data, just `ok` + db check)

## Role permissions (server-enforced via a single `require(perm)` dependency; matrix is data, tested exhaustively)

| Permission | desk | nurse | owner | admin flag |
|---|---|---|---|---|
| Overnight view | yes | yes | yes | |
| Overnight confirm / entered / to-callback / ack | yes | no | yes | |
| Overnight mark emergency followed up | yes | yes | yes | |
| Callbacks view, claim, done | yes | yes | yes | |
| Drafts view, edit, approve | yes | no | yes | |
| Lab results view | yes (read) | yes | yes | |
| Lab results advance | no | yes | yes | |
| Schedule view | yes | yes | yes | |
| Brief, suggestions approve/dismiss | no | no | yes | |
| After-hours setup edit, hospitals | no | no | yes | |
| Simulator ("Try it as a client") | yes | yes | yes | |
| How it fits + feed | yes | yes | yes | |
| Assistant panel | role chips | role chips | owner chips | |
| Export CSV | lists they can see | same | all | |
| Users, audit, import, settings, AI key, reset demo | no | no | no | **yes** (admin flag) |

Owner accounts here (Dr. Garg, Krish) both have the admin flag. A nurse claiming a callback matches the reference. Desk confirming bookings matches the reference flow.

## Screens to endpoints

| Screen (reference view) | Roles | Endpoints |
|---|---|---|
| Sign in, change password | all | auth/* |
| Brief (`viewBrief`) | owner | /brief, /suggestions/* |
| Overnight + Try it as a client (`viewOvernight`, `viewSim`) | all (actions per matrix) | /overnight/*, /sim/* |
| Callbacks + Messages to approve (`viewCallbacks`) | desk, nurse (drafts hidden for nurse), owner | /callbacks/*, /drafts/* |
| Lab results (`viewResults`) | all | /results/* |
| Schedule (`viewSchedule`) | all, read only | /shifts |
| After-hours setup (`viewSetup`) | owner | /afterhours/*, /hospitals/*, /visit-types/* |
| How it fits (`viewTools`) | all | /tools, /events, /stream |
| Assistant side panel | all | /assistant/* |
| Admin: Users, Audit log, Import, Settings (banner, clinic name, thresholds, AI provider, Retell) | admin | /admin/* |

UI changes from the reference (logged in DECISIONS.md): the "Viewing as" role switcher becomes the signed-in user plus a sign-out menu; "Reset demo" moves to Admin; a theme toggle stays; the banner reads "Prototype, example data" and is admin-toggleable; the free-text box in the simulator is added next to the quick-reply buttons.

## Behaviour details

**Brief (SQL + rules)**: KPIs = open callbacks; results with stage<2 and age>=2 days; missed calls last week (from `imported_calls` visible rows if loaded, else `call_log_demo`, labeled "example"); overnight items pending. Chart = average real recorded calls per weekday hour (from `call_analytics`, labeled "real recorded call volume") divided by people on the phones at that hour computed from the roster (labeled "assumption: example roster"). Note: real recorded volume per hour is likely far below the reference's 21/h, so the desk-shift rule may not fire; this is reported honestly, and the chart has a toggle to "example numbers" so the demo story still works.
Rules (thresholds in settings): `desk_shift` (calls per person per hour > load_limit on >= N weekdays: add proposed open desk shifts for those hours Mon to Fri, shown dashed); `missed_texts` (missed last week >= T: turn on missed-call texts, adapter logs only); `recall_batch` (overdue patients >= T: create first 10 recall drafts); `old_results` (stage 0 and age >= 2: mark them doctor reviewed, matching the reference). Each approval writes events. Rules dedupe by `rule_key` while open.
Optional AI wording: only metric values are sent; every number in the returned text is extracted by regex and must exist in the computed metric set (with normalisation of "41"/"forty-one" style), else the templated sentence is used. Unit test with a fabricated number.

**Assistant panel**: chips per role exactly as in the reference; each key maps to a query handler returning structured data rendered in the reference wording. Free text uses the reference keyword matcher; no match returns "I can answer: ..." listing the chips. Policy answers (records, Saturday hours) come from an editable answer sheet marked "example content". "Draft a pickup message for Pepper" creates a real pending draft. The results-call script uses a placeholder "[what Dr. Garg wants said]" instead of inventing a lab finding.

**After-hours assistant**: server state machine with the reference nodes (start, book1, sick, time, done_book, done_cb, refill1, refill2, urgent1, done_em, done_em2) plus a free-text path. Every client utterance first goes through emergency detection: Layer 1 = normalized keyword/phrase/regex list from settings (seeded with the reference list plus synonyms; later merged with the owner-approved `config/emergency_keywords.yaml`); Layer 2 = model classifier (only if key set and toggle on), returning emergency|not|unsure, where unsure or error counts as emergency when layer 2 is on. **All client-facing text is from templates; a model never writes text to clients**, which is how "never advises, never doses, never says a pet is fine" is guaranteed. Advice or dose requests hit a fixed refusal plus the emergency offer. Emergency text names a hospital only if one is confirmed (and gives phone/address only if those fields are filled); otherwise "please contact the nearest emergency veterinarian now; the clinic will follow up". Bookings only for future slots and bookable visit types; non-bookable become callbacks. Disclosure in every opening; call mode adds "This call is recorded." Outcomes create `conversations`, `appointment_requests`, `callbacks`, `emergency_routings` and events. SMS/VOICE adapters only write `outbox` rows with status `would_send`.

**Retell hook**: disabled and dry-run by default; disabled returns 404. Requires header `X-Walnut-Secret` (64+ random chars, stored hashed, constant-time compare) and, when a Retell API key is configured, verifies `x-retell-signature` (HMAC per Retell docs, checked at build time). Internal schema in `backend/app/retell/schema.py`; a thin `mapping.py` converts Retell payloads. Webhook `call_ended` stores transcript + summary as an overnight conversation (source retell). `docs/RETELL.md` has request/response shapes and runnable `curl` examples (tested in M6).

**Import/export**: `calls` schema `call_id, timestamp, caller_type, intent, outcome, urgency, red_flags, promised_followup, contains_private_content, summary`; plus appointments, clients, pets, lab_results, analytics. CSV or JSONL, validated per row with pydantic, report of accepted/rejected rows. `PimsAdapter` protocol (read-only) with `CsvExportAdapter` reading `samples/` or `/var/lib/walnut-desk/imports/`. Every list exportable as CSV (formula-injection-safe: prefix `= + - @`).

## Security

argon2id; session cookie `__Host-wgd` (Secure, HttpOnly, SameSite=Lax, 12 h idle, 7 d max); CSRF token per session in `X-CSRF-Token`; login rate limit per IP and per username (5 failures, 15 min lock) plus a generic error; forced password change (min 12 chars, zxcvbn-style common-password check); server-side pydantic validation; SQLAlchemy parameterised queries only; React escaping, no `dangerouslySetInnerHTML`; headers via middleware and Caddy (CSP `default-src 'self'`, HSTS, nosniff, `frame-ancestors 'none'`, Referrer-Policy, Permissions-Policy); no docs/OpenAPI routes in production; uvicorn bound to 127.0.0.1; ufw: allow OpenSSH **first**, then 80, 443, then enable; secrets 600; `pip-audit` and `npm audit` in M7; test that desk and nurse get 403 on every owner/admin endpoint.
AI settings: provider (Off | DeepSeek | OpenRouter), base URL, model, key (write-only, shows last 4), and three separate toggles, all off by default: "Word the brief", "Second emergency check", "Allow call transcripts to be sent for labeling". Settings show a plain warning that DeepSeek processes data outside Canada and the owner must decide this under BC privacy law before turning on transcript labeling. Repeated in `docs/SECURITY.md` and the handoff.

## Real transcripts pipeline (M6b; Opus runs it, Sonnet writes code against a synthetic fixture CSV only)

1. `useradd --system walnut-pipeline`; `/srv/walnut-private` 700; move the CSV in, verify sha256, `shred -u` the copy in `/root/.claude/uploads/...`. Salt = 32 random bytes, 600.
2. `load.py`: CSV to `private.db` (call_id, call_ref, ts, ext, number_hash, seconds, language, transcript, plain_text) + FTS5; raw number column not stored. `audit.py` writes `docs/DATA_AUDIT.md` (counts only).
3. `label_rules.py`: deterministic labeler for the full schema (clinic greeting incl. garbled name variants, voicemail markers, courier/lab/vendor patterns, symptom regexes, private = no clinic-work signals or family/personal markers; when in doubt private = true). Output validated with a strict pydantic schema.
4. Gold set: ~100 calls stratified by hour x weekday, plus a targeted set of FTS emergency candidates (real emergency calls are rare, so recall needs extra positives). Opus labels via `gold_tool.py`, which shows digit-masked text in short chunks and stops as soon as a call looks private. Measure per-field accuracy, emergency recall, private recall; tune rules; report tuned vs held-out numbers honestly. Target 0.95; if missed, explain.
5. Secure deletion: for every private-flagged call, overwrite then null text, delete from FTS, `PRAGMA secure_delete=ON`, `VACUUM`, checkpoint WAL; then `shred -u` the CSV (it contains private text). Report count only.
6. LLM relabel later (`pipeline label --llm`): only on remaining non-private text, only if the key exists and the transcript-labeling toggle is on (read from `llm.env`). Accuracy vs gold re-reported.
7. `analytics.py`: calls by hour, weekday, extension; caller type and intent mix; hold/drop rates; callback-promised rate; after-hours and weekend volume; peak hours. Small cells (n<5) suppressed. JSON copied to `/var/lib/walnut-desk/imports/` and loaded via the import API with `source=derived_from_real_calls`.
8. Drafts by Opus from FTS snippets of non-private calls, each item citing 2 to 3 `call_ref`s, no names or numbers: `docs/ANSWER_SHEET_DRAFT.md` (prices/policies marked "heard in calls, needs the clinic to confirm"), `config/templates.yaml`, `config/emergency_keywords.yaml` ("draft for the owner to approve"). Keyword draft feeds Layer 1 after owner review (shipped merged but flagged draft).
9. Eval: 40 emergency, 60 booking, 40 refill/message, 40 adversarial paraphrased openings in `eval/scenarios.jsonl` (with source `call_ref`; where real calls run out, synthetic variants are labeled as such). `run_eval.py` drives the state machine; reports emergency recall (must be 100%), advice-given count (must be 0, checked by template provenance plus a dose/advice regex), booking completion, every failure by id in `docs/ASSISTANT_EVAL.md`.
10. `WIPE.sh`: shreds `/srv/walnut-private`, removes `walnut-pipeline`, deletes `call_analytics` derived rows and `imported_calls`, and purges Claude Code session logs under `/root/.claude/projects` (they hold the gold-set snippets Opus read). Tested on a copy via `WIPE_ROOT=`.
11. Leak check `verify_no_leak.py` (root): phone-number regex, number hashes, and hashed 8-gram fingerprints of sampled transcripts searched across repo, `/var/lib/walnut-desk`, web root, journald and app DB; prints counts only. Plus `sudo -u walnut-app cat /srv/walnut-private/private.db` must fail.

## Hosting

M1: apt update; install Caddy (official repo), Node 22 LTS (NodeSource), uv, sqlite3; ufw (SSH first). Start Caddy for `77-42-69-194.sslip.io`. Cert issuance proves port 80 reachability; verify 443 from outside with an external checker (check-host.net) and from the server via the public name with full TLS verification. If issuance fails: try `77.42.69.194.nip.io`; if ports are blocked upstream: `cloudflared` quick tunnel under systemd, URL written to `/var/lib/walnut-desk/public_url.txt`, documented as changing on restart. Write `public_url.txt` in every case.

## Backups, restart, ops (M7)

Daily `walnut-backup.timer` using the SQLite online backup API to `/var/lib/walnut-desk/backups/app-YYYYMMDD.db`, 14-day retention, gzip; restore script with integrity check. Private DB is not backed up (minimisation). journald size cap + logrotate for Caddy logs. `healthcheck.sh` (systemd status, /healthz via public URL, cert expiry, disk). Tests: `kill -9` the app and confirm restart; `systemctl restart caddy`; reboot test and verify both come back.

## Milestones and who does what

Each milestone ends with: its checks run with output captured, `docs/PROGRESS.md` updated, a local git commit. Subagents work on disjoint directories to avoid conflicts; Opus reviews every diff before commit.

| M | Work | Owner | Acceptance check |
|---|---|---|---|
| M1 | Extract reference; server notes; install toolchain; repo skeleton; DB + Alembic; `/healthz`; systemd unit; Caddy; ufw; hello page public; reference screenshots (Playwright, all 7 views x 3 personas, light + dark) | Opus (infra), Haiku (SERVER_NOTES, DECISIONS, screenshots), Sonnet (skeleton) | `curl -sS https://77-42-69-194.sslip.io/healthz` = ok with verified TLS from outside |
| M2 | Users, sessions, argon2, CSRF, lockout, admin user pages, events/audit append-only, permissions matrix, account creation with generated passwords | Sonnet | pytest role matrix over every route; first login forces change; append-only trigger test |
| M3 | Callbacks, results, drafts, shifts, suggestions, events, SSE; screens Callbacks, Messages to approve, Lab results, Schedule, How it fits; seed data | Sonnet (backend), Sonnet (frontend), Haiku (seed transcription from reference) | Playwright: desk claim, done, approve; nurse advance; two contexts see each other live |
| M4 | Conversations, Overnight screen, simulator text + call, state machine, two-layer emergency, booking confirm then "Done in ImproMed", After-hours setup | Sonnet; Opus reviews safety logic | every keyword routes to emergency; adversarial dose/advice never advised; no unconfirmed hospital named |
| M5 | Brief metrics SQL, rules + effects, assistant panel queries, AI wording with number check, Admin AI provider settings | Sonnet | unit test rejects fabricated figure; each approval effect visible and evented |
| M6 | Retell endpoints + docs, dry-run adapters, import pages/API, CSV export, samples, PimsAdapter | Sonnet (code), Haiku (samples, RETELL.md curl examples) | every curl in RETELL.md runs and matches documented output |
| M6b | Private pipeline as above | Opus runs and labels; Sonnet writes code against a synthetic fixture | failed read as walnut-app; leak check zero; Brief chart shows real hourly volume; eval numbers honest |
| M7 | Hardening, backups, log rotation, healthcheck, audits, restart and reboot tests, SECURITY.md | Sonnet (scripts), Haiku (audit runs, docs), Opus (review) | app killed and back; reboot recovered; pip-audit/npm audit clean or documented |
| M8 | Full e2e against public URL, every screen x role screenshots vs reference, gap fixes, `/root/HANDOFF.md` (600) | Haiku (screenshot runs), Sonnet (fixes), Opus (handoff) | suite green, screenshots compared, handoff complete |

Accounts: `garg` (owner, admin), `priya`, `marcus` (desk), `anita`, `jordan` (nurse), `krish` (owner, admin). 20-char random passwords, shown once in the handoff and final message, `must_change_password=true`.

## Risks and fallbacks

- Let's Encrypt fails for sslip.io (rate limit or blocked port): nip.io, then cloudflared quick tunnel.
- Hetzner firewall blocks 80/443: tunnel fallback; note it.
- NodeSource unavailable: official nodejs.org tarball pinned. PyPI wheel issues on 3.12: pin older versions.
- Playwright Chromium install fails: `playwright install-deps` then retry; else apt chromium with executablePath.
- No AI key: everything templated; layer 2 off; rules-only labeling with accuracy reported.
- Rules labeler below 0.95 private recall: lean further toward private=true (deletes more text) and report the trade-off.
- Few real emergency calls: targeted candidates plus labeled synthetic variants; report counts.
- 3.7 GiB RAM, no swap: add a 2 GiB swapfile before the frontend build.
- Same step fails twice: stop, diagnose, pick the documented fallback, log in DECISIONS.md.

## Verification (end to end)

1. `pytest -q` (backend: auth, matrix, CSRF, lockout, state machine, every keyword, adversarial, number check, retell secret, private-row exclusion, import validation, export escaping).
2. Playwright suite against `https://77-42-69-194.sslip.io`: sign in as each role (first-login change), desk and nurse flows in two concurrent contexts with live update under 5 s, owner approves each suggestion and sees effects, simulator text and call (booking, refill, emergency), admin adds and deactivates a user, 403 checks via API.
3. External checks: TLS verified, security headers present, only 22/80/443 open (`ss -tlnp`, ufw status, external port check), no `/docs`, `/openapi.json`.
4. Privacy checks: failed read as walnut-app, `verify_no_leak.py` zero hits, git grep for numbers, WIPE.sh tested on a copy.
5. Ops: kill app, restart Caddy, reboot; backups present and restorable.
6. Screenshots of every screen per role next to the reference shots.
7. Handoff printed and saved to `/root/HANDOFF.md` (600) with URL, accounts, tours, ops how-tos, mocked vs real, real-data statement (what, where, private-flagged deleted count, third parties: Anthropic for Opus gold labeling, DeepSeek only if enabled), one-command wipe, next steps.
