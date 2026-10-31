# Walnut Grove Desk: backend API (as built, waves 1 and 2)

All paths are under `/api` unless noted. JSON in and out, snake_case. Times in responses are UTC ISO-8601 with a `Z` (`2026-10-27T16:31:00.000Z`); `*_display` fields are America/Vancouver wall clock. See `docs/CONTRACT.md` for the rules this implements.

## Running it

```
cd /opt/walnut-desk/backend

# serve (single worker)
WALNUT_ENV=dev WALNUT_DB_PATH=/path/app.db ../.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000

# migrations
../.venv/bin/alembic upgrade head          # manual, uses WALNUT_DB_PATH
# The app ALSO runs `alembic upgrade head` on startup (lifespan), so a normal deploy needs no
# manual step. Set WALNUT_SKIP_MIGRATE=1 to turn that off. The seed CLI migrates first too.

# seed
../.venv/bin/python -m app.seed --reset-demo       # reseed operational demo tables (idempotent)
../.venv/bin/python -m app.seed --create-users     # 6 accounts, prints username<TAB>password once; refuses if any exist
# both flags may be combined; reset-demo summary goes to stderr, only the passwords go to stdout

# tests
../.venv/bin/pytest -q
```

Environment: `WALNUT_ENV` (`production|dev|test`, default `dev`), `WALNUT_SECRET_KEY` (required, 32+ chars, in production), `WALNUT_FERNET_KEY`, `WALNUT_DB_PATH` (required in production; dev default `backend/walnut-dev.db`), `WALNUT_LLM_ENV_PATH`, `WALNUT_IMPORT_DIR`, `WALNUT_PUBLIC_URL`, optional `WALNUT_FRONTEND_DIST` (default `frontend/dist`), `WALNUT_SKIP_MIGRATE`.

In production there is no `/docs`, `/redoc`, `/openapi.json` (they return 404, also through the SPA fallback). If `frontend/dist/index.html` exists, any other non-`/api` GET serves a file from it or `index.html`; unknown `/api/*` returns `404 {"detail":"Not found"}`.

## Conventions

- Lists: `{"items":[...]}`. Actions: `{"item":{...}}`.
- Errors: `{"detail":"..."}`. Status codes used: 400 (rule broken, plain-words message), 401 `not_authenticated`, 403 `forbidden` | `csrf` | `password_change_required`, 404, 409 conflicts (`Already taken by Marcus`, `Already closed`), 422 (schema validation, FastAPI default shape), 423 (locked), 429.
- Auth: cookie `__Host-wgd` (production; Secure, HttpOnly, SameSite=Lax, Path=/) or `wgd` (dev, test). Idle timeout 12 h, absolute 7 days.
- CSRF: every non-GET `/api` request (except `POST /api/auth/login` and `/api/retell/*`) must send `X-CSRF-Token` equal to `csrf_token` from login or `/api/auth/me`.
- Forced password change: while `must_change_password` is true every route except `auth/me`, `auth/logout`, `auth/change-password` returns `403 {"detail":"password_change_required"}`.
- Every response carries the CSP and the other security headers. `/api/*` and `/healthz` also send `Cache-Control: no-store`.
- Request bodies are strict pydantic models: unknown fields are rejected (422), text <= 2000 chars, names <= 120.
- Perm column below: a `require("...")` perm from `app/security/permissions.py`. `login` means any signed-in user past the password gate. Admin perms need `is_admin`.

## Endpoints

### Health
| Method | Path | Perm | Notes |
|---|---|---|---|
| GET | `/healthz` | none | Runs `SELECT 1`. `200 {"status":"ok"}` or `503 {"status":"error"}`. |

### Auth
| Method | Path | Perm | Request | Response |
|---|---|---|---|---|
| POST | `/api/auth/login` | none, CSRF exempt | `{username (<=120), password (<=256)}` | 200 me payload (below), sets cookie. 401 `Wrong username or password` (also unknown and inactive users). 423 `Too many tries. Wait 15 minutes.` (5 failures for a username lock it 15 min, the 5th failure already returns 423; unknown names lock the same way). 429 per-IP limit, 20 attempts per 10 min. |
| POST | `/api/auth/logout` | cookie optional | none | `{"ok":true}`, clears cookie. Needs CSRF when a session exists. |
| GET | `/api/auth/me` | signed in (allowed during forced change) | none | me payload. 401 if not signed in. |
| POST | `/api/auth/change-password` | signed in (allowed during forced change) | `{current_password, new_password}` | me payload. 400 with a plain message if the current password is wrong or the new one is under 12 chars, equals the username or the current password, or is in the common list. Other sessions of the user are ended. |

me payload: `{"user":{id,username,display_name,title,initials,role,is_admin,must_change_password},"perms":[sorted perm names],"csrf_token":"...","settings":{"clinic_name","banner_on"}}`.

### Admin (all need `is_admin`)
| Method | Path | Perm | Request | Response |
|---|---|---|---|---|
| GET | `/api/admin/users` | `admin.users` | | `{"items":[user]}` |
| POST | `/api/admin/users` | `admin.users` | `{username (2-32, a-z0-9 . _ -, starts with a letter, lowercased), display_name, title?, initials?, email?, role: owner|desk|nurse, is_admin?}` | 201 `{"item":user,"password":"<20 chars, shown once>"}`; account starts with `must_change_password`. 409 if the username exists. |
| PATCH | `/api/admin/users/{id}` | `admin.users` | any of `{role, is_admin, active, display_name, title, initials}` | `{"item":user}`. 409 if you would remove your own access or the last active admin. Deactivating ends that user's sessions. |
| POST | `/api/admin/users/{id}/reset-password` | `admin.users` | none | `{"item":user,"password":"<one-time>"}`; sets `must_change_password`, clears lockout, ends sessions. |
| GET | `/api/admin/audit` | `admin.audit` | query: `tool, actor (display name), action, entity_type, q (text contains), since, until (ISO datetimes), limit (1-200, default 50), offset` | `{"items":[audit event],"total","limit","offset"}`, newest first. Includes `Admin` and `Sign-in` tools, `ip` and `data_json`. |
| GET | `/api/admin/settings` | `admin.settings` | | `{"settings":{key:value}}` including defaults: `clinic_name`, `banner_on`, `tog_req`, `tog_msg`, `tog_missed`. |
| PUT | `/api/admin/settings` | `admin.settings` | `{"settings":{key:value,...}}` | `{"settings":{...all}}`. Known keys are type checked (`clinic_name` non-empty text <= 120, the rest booleans). Other keys must match `^[a-z][a-z0-9_.]{0,63}$` with JSON value <= 4000 chars. Keys starting `ai_`, `ai.`, `retell_`, `retell.`, `secret`, `fernet` are refused (400); those get their own endpoints later. Writes an `Admin` event listing key names only. |
| POST | `/api/admin/reset-demo` | `admin.reset_demo` | none | `{"ok":true,"counts":{table:rows}}`. Reseeds operational tables only (see Seed). |

user (admin view): `{id,username,display_name,title,initials,role,is_admin,must_change_password,email,active,locked,locked_until,created_at,last_login_at}`.

### Callbacks (tool `Callback list`)
| Method | Path | Perm | Notes |
|---|---|---|---|
| GET | `/api/callbacks?filter=all|mine|high` | `callbacks.view` | Sorted new, claimed, done; high priority first; newest first. `mine` = owned by you; `high` = high priority and not done. |
| POST | `/api/callbacks/{id}/claim` | `callbacks.work` | Atomic. 409 `Already taken by <name>` if someone else has it, `Already closed` if done. Claiming your own again is a no-op 200. Event: `Priya claimed Dhillon (Simba (cat))`. |
| POST | `/api/callbacks/{id}/done` | `callbacks.work` | Only the owner (or an owner-role user) may close a claimed callback, else 409 `Already taken by <name>`. Already done: 409 `Already closed`. Event: `Priya closed Dhillon (results)`. |

callback: `{id, client_label, pet_label, what, kind, via, received_at, at_display, priority, red_flag, owner_id, owner_name, status, source, conversation_id, closed_at}`.

### Lab results (tool `Results tracker`)
| Method | Path | Perm | Notes |
|---|---|---|---|
| GET | `/api/results` | `results.view` | Open results first, oldest first (age in Vancouver calendar days, ties by id), closed last. |
| POST | `/api/results/{id}/advance` | `results.advance` | Body optional `{"from_stage": 0|1|2}`; if it does not match the current stage, 409. Stage 0 to 1 sets `reviewed_by`, 1 to 2 sets `told_by`. At stage 2: 409 `Already closed by <name>`. Events: `Maple (spaniel), Blood panel: doctor reviewed` and `... : client told`. |

result: `{id, pet_label, owner_label, test, received_at, age_days, stage, stage_name, stage_names:["Received","Doctor reviewed","Client told"], late, reviewed_by, told_by, updated_at}`. `late` is stage < 2 and age >= 2 (amber in the UI).

### Messages to approve (tool `Recall sender`)
| Method | Path | Perm | Notes |
|---|---|---|---|
| GET | `/api/drafts` | `drafts.view` | All drafts by id (pending and sent). |
| PATCH | `/api/drafts/{id}` | `drafts.approve` | `{body (1-2000 chars)}`. Pending only, else 409 `Already approved by <name>`. Event `Priya edited a draft to Ms. Chen, for Biscuit`. |
| POST | `/api/drafts/{id}/approve` | `drafts.approve` | Atomic pending to sent, sets `approved_by` and `approved_at`, writes an `outbox` row (`channel sms`, `status would_send`, `source_type draft`) and logs `would send` through `app/adapters/sms.py`. Nothing is sent. Event `Priya approved: Annual exam recall to Ms. Chen, for Biscuit`. 409 if already approved. |

draft: `{id, kind, to_label, body, channel, status: pending|sent, created_by_tool, edited_by, approved_by, approved_at, suggestion_id}`. `edited_by` and `approved_by` hold display names.

### Schedule (tool `Staff roster`)
| Method | Path | Perm | Notes |
|---|---|---|---|
| GET | `/api/shifts` | `schedule.view` | Always 7 items, Mon to Sun: `{weekday, day, closed, shifts:[shift], desk_at_9, cover, busiest}`. `desk_at_9` = desk shifts with start <= 9:00 < end (reference `deskAt9`); `cover` is `ok` (2+), `warn` (1), `high` (0); Sunday has `desk_at_9: null`, `cover: null`. `busiest` is true on Saturday (reference note). |

shift: `{id, weekday, person_label, user_id, role: desk|nurse|doc|open, start_min, end_min, start:"08:00", end:"16:30", label:"Marcus 08:00-16:30", proposed, suggestion_id}`. `proposed` shifts are the dashed ones.

### Feed, tools, live updates
| Method | Path | Perm | Notes |
|---|---|---|---|
| GET | `/api/events?since=<id>&limit=<1-200, default 12>` | `tools.view` | Feed events, newest first, only ids above `since`. `Admin` and `Sign-in` events are excluded. `{"items":[{id,ts,t_display,tool,actor_id,actor_name,action,entity_type,entity_id,text}],"last_id"}` (`last_id` is the highest event id of any tool). Poll this every 5 s if SSE fails. |
| GET | `/api/tools` | `tools.view` | The 7 tool cards: `{"items":[{name,who,writes,event_count}]}`. |
| GET | `/api/stream` | login | `text/event-stream`. First `retry: 3000` and `: connected`, then per change `event: change\ndata: {"entity":"callbacks","id":12,"event_id":345}\n\n`, and a `: ping` comment every 15 s. Each recorded event sends one message for its entity (when it has one) plus one for `events`. Messages are published only after the database commit. 401 / 403 as for any route. |

SSE entities: `callbacks, results, drafts, shifts, suggestions, overnight, events, settings, hospitals, visit_types, users, analytics`. `entity_type` to entity: `callback`, `result`, `draft`, `shift`, `suggestion`, `conversation` (to `overnight`), `hospital`, `visit_type`, `user`, `setting`, `analytics`. Reset demo sends one message per operational entity.

### Export
`GET /api/export/{name}.csv`, perm `export.use` plus the list's own view perm: `callbacks` (`callbacks.view`), `results` (`results.view`), `drafts` (`drafts.view`), `shifts` (`schedule.view`), `events` (`tools.view`, feed tools only), `audit` (`admin.audit`). Unknown name 404, missing list perm 403. Response `text/csv` with `Content-Disposition: attachment; filename="walnut-<name>.csv"`, CRLF rows. String cells starting with `=`, `+`, `-`, `@`, tab or CR get a leading `'`. Each export writes an `Admin` event (`export`). Wave 2 adds `overnight` (`overnight.view`) and `analytics` (`brief.view`: columns source, metric, dim1, dim2, value, n).

## Code map for the next milestones

- `app/security/permissions.py`: `PERMS`, `ADMIN_PERMS`, `has_perm`, `perms_for`.
- `app/security/auth.py`: `require("perm")` (tags the dependency with `walnut_perm`, which the route walker test reads), `require_login`, `csrf_guard` (global dependency), session helpers.
- `app/services/events.py`: `record(db, tool, action, text, actor, entity_type, entity_id, data=None, *, ts=None, ip=None, publish=True)` flushes and queues SSE messages; they go out on commit and are dropped on rollback. Endpoints call `db.commit()` themselves. Tool name constants live here.
- `app/services/broadcaster.py`, `settings_store.py`, `csvutil.py`, `timeutil.py` (Vancouver helpers; `set_clock` freezes time in tests).
- `app/adapters/sms.py`: `queue_sms()` writes the `would_send` outbox row. Add a voice adapter next to it.
- `app/seed`: `reset_demo(db, actor, now)` and `create_users(db)`; `RESET_TABLES` lists what a reset replaces (outbox, emergency_routings, appointment_requests, sim_sessions, callbacks, drafts, shifts, conversations, lab_results, suggestions, open_slots, hospitals, visit_types, call_log_demo). It never touches users, settings, call_analytics, imported_calls, pims_* or events; it appends one `Admin` event `Demo data reset` and, on the very first seeding only, the 7 reference feed lines (action `seed_log`). Seed ids follow the reference (`c5` becomes callback 5, `o3` conversation 3, `r4` lab result 4 and suggestion 4).
- All tables from PLAN.md exist in the single initial migration `alembic/versions/0001_initial_schema.py`, including `login_attempts` (login throttling), the `visible_calls` view (imported calls with `contains_private_content = 0`; every UI query must read this view, never `imported_calls`), and the append-only triggers on `events`.
- Routes added later must use `Depends(require("..."))`. `backend/tests/test_permissions.py` walks every `/api` route and fails on one that has no perm tag and is not in its short allowlist.
- Database access is synchronous SQLAlchemy in the threadpool; every transaction starts with `BEGIN IMMEDIATE` (see `app/db.py`) so concurrent writers queue instead of failing.

## Seeded demo data (after `--reset-demo`)

Callbacks 1 to 7 (c1..c7), lab results 1 to 5, drafts 1 to 3, suggestions 1 to 4, 24 shifts (Mon to Sat, none on Sunday), 5 overnight conversations with 2 appointment requests and 2 emergency routings, visit types (5), hospitals (Boundary Bay row unconfirmed with empty phone and address, and "Backup hospital" placeholder), 3 open slots (next Thursday 9:30 am, Friday 2:00 pm, Monday 3:30 pm), and `call_log_demo` rows for the two previous calendar weeks (41 and 32 unanswered calls, 28 and 22 of those callers rang again within the hour; answered calls follow the example hourly volume on weekdays). Clock strings from the reference land on today in Vancouver; overnight `pm` times land on yesterday; lab results are `age` days before today at 8:47 am. All of it is invented example data.


---

# Wave 2

Specs: `docs/CONTRACT_WAVE2.md`. Retell: `docs/RETELL.md`. Migration `0002` makes `imported_calls.summary` nullable (private rows keep no summary); the `visible_calls` view is recreated.

## Overnight (tool `After-hours assistant`)
| Method | Path | Perm | Notes |
|---|---|---|---|
| GET | `/api/overnight` | `overnight.view` | `{"items":[{id, client_label, pet_label, channel:"Text"|"Call", at_display, created_at, kind, status, preferred_slot, summary, messages:[{who,text}], is_test, source}],"summary":{total, booking, callback, emergency, pending, emergency_pending}}`. Order: status new first, then emergency, booking, callback, newest first. |
| POST | `/api/overnight/{id}/confirm` | `overnight.manage` | booking new to confirmed; marks the slot taken; outbox sms `would_send`; event `Priya confirmed Tran for Thu Oct 29, 9:30 am`. |
| POST | `/api/overnight/{id}/entered` | `overnight.manage` | confirmed to entered; event `Tran: booking entered in ImproMed`. |
| POST | `/api/overnight/{id}/to-callback` | `overnight.manage` | booking new to called; creates a callback (kind Booking, `Wants to book. <summary>`); event in `Callback list`: `Priya moved Tran to callbacks`. |
| POST | `/api/overnight/{id}/followed-up` | `overnight.followup` | emergency new to handled; event `Anita followed up with Pereira after an emergency routing`. |
| POST | `/api/overnight/{id}/ack` | `overnight.manage` | callback new to handled. |

Wrong item kind: 400. Wrong status: 409 `Already confirmed` and similar. Unknown id: 404.

## Simulator (`sim.use`)
`POST /api/sim/start {"channel":"text"|"call"}` returns `{"session":{id, channel, node, messages:[{who,text}], options:[{index,label}], done, accepts_text}}`. `POST /api/sim/{id}/reply` with `{"option_index":n}` or `{"text":"..."}` (exactly one, text <= 500) returns the same shape, plus `"outcome":{"kind","conversation_id"}` when the conversation ends. Other users' sessions: 404. Finished session: 409. Bad index or empty text: 400. State lives in `sim_sessions` (`picks_json` holds picks and outcome). Outcomes write a conversation (source `sim`, `is_test`, client `You (test)`), an appointment request, callback (`Taken overnight by the assistant.`) or emergency routing, and an event: `New appointment request|callback|emergency routing from a test client`.

Engine (`app/aftercare/`): `templates.py` (every client-facing string, returned as a `Tpl` str so tests can prove provenance), `emergency.py` (`detect`, `normalize`, `DEFAULT_KEYWORDS`, `re:` entries, layer 2 hook), `keywords.py` (`load_keywords(db)` merges settings key `emergency_keywords` with `config/emergency_keywords.yaml`; YAML `status` plus `groups` as lists or `{phrases, heard_in}`), `engine.py` (`start`, `step`, `view`, `Ctx`, `State`), `service.py` (`build_ctx`, `persist_outcome`, slot generation). Client free text is never echoed except a pet name that passes a strict check. Layer 2 runs on free text only, and only when a provider is set and "Second emergency check" is on; unsure or error means emergency. Layer 1 also checks option labels.

## After-hours setup (`afterhours.view` to read, `afterhours.edit` to change)
`GET /api/afterhours/settings` returns `{toggles:{req,msg,missed}, hours_note, visit_types:[{id,name,minutes,bookable}], keywords, keywords_status, hospitals:[{id,name,place,phone,address,hours_claim,confirmed,is_placeholder}], wording:{opening_line,urgent_line,never_line}}`.
`PUT /api/afterhours/toggles {req?,msg?,missed?}`, `PUT /api/visit-types/{id} {bookable}` (returns `{"item"}`), `PUT /api/afterhours/keywords {keywords:[...], status}` (regexes validated, never empty, `re:` entries allowed), `PUT /api/afterhours/wording {opening_line?,urgent_line?,never_line?}` (the opening line must contain "automated assistant"), `POST /api/hospitals`, `PUT /api/hospitals/{id}` (resets `confirmed`), `POST /api/hospitals/{id}/confirm` (400 for a placeholder; event `Dr. Garg confirmed the emergency hospital list`), `POST /api/hospitals/{id}/unconfirm`. Settings keys used: `tog_req`, `tog_msg`, `tog_missed`, `emergency_keywords`, `wording`, `answer_sheet`, `rules`, `hours_note`. A demo reset now also puts the three toggles back (req on, msg on, missed off) and inserts missing wave 2 settings; it still does not touch other settings.

Seed: 14 days of open slots from tomorrow on Monday to Friday at 9:30, 11:00, 2:00 pm and 3:30 pm (40 rows); the seeded overnight bookings now point at the next real Thursday 9:30 and Monday 3:30 pm. The shifts endpoint counts `open` shifts as desk cover.

## Brief (`brief.view`) and suggestions (`suggestions.decide`)
`GET /api/brief?chart=real|example` returns `{greeting, date_line, kpis:[{key,value,label,alert}], overnight, suggestions:[{id,rule_key,title,why,status,evidence,decided_by,decided_at}], chart:{mode, available_modes, hours, calls, cover, per_person, limit, calls_label, cover_label}, text:{source:"template"|"model", paragraph}}`. KPI keys: `callbacks_open`, `results_waiting`, `missed_last_week` (label ends `(example)` when it comes from `call_log_demo`; otherwise counts `no_answer` or `voicemail` rows of `visible_calls` in the last 7 days), `overnight_pending`. Chart real mode reads `call_analytics` metric `weekday_avg_calls_by_hour` (dim1 = hour 8 to 17); cover per hour is the Monday to Friday average of desk or open shifts. `services/metrics.py` holds every query; `services/numbercheck.py: check(text, allowed) -> (ok, bad)` rejects model text with a number that is not in the record (digits, decimals, percents, clock times, number words zero to one hundred).
The paragraph is a template. With "Word the brief" on and a key saved, the model rewrites it (only the templated sentence is sent, no names); if the result fails `numbercheck` the template is used and the event `Brief wording rejected: number not in the record` is written. The result is cached per metric set in the internal `brief_cache` setting, so one model call per change.
`POST /api/suggestions/{id}/approve` (409 if not open) runs the reference effect: `desk_shift` adds five `open` proposed desk shifts (hours from the suggestion's evidence, default 08:00-10:00) and event `Dr. Garg approved an open desk shift, 08:00-10:00, Monday to Friday`; `missed_texts` sets `tog_missed` (event `... turned on texts to missed callers`); `recall_batch` adds the two extra recall drafts (event `Recall batch started: first drafts sent to the front desk for approval`); `old_results` moves stage 0 results with age >= `results_age_days` to stage 1 (event `... marked older results as reviewed, nurses can call clients`). The actor name is the signed-in user. `POST /api/suggestions/{id}/dismiss`. `POST /api/suggestions/run` runs the rules once and returns `{"created":[...]}`; `python -m app.rules` does the same for a systemd timer. Rules (`rules/engine.py`) are idempotent per `rule_key` while one is open, and also for 7 days after one was approved or dismissed (dismissed is included so a 15 minute timer does not nag).

## Assistant (`assistant.use`)
`GET /api/assistant/chips` returns `{greeting, chips:[{key,question}]}` for the caller's role. `POST /api/assistant/ask {key?|text?}` returns `{"answer":{paragraphs, bullets, after, link:{label,view}|null, source:"record"|"answer_sheet"|"fallback"}}` (plain text). A `key` outside the caller's role chips is 403. Free text uses the reference keyword table and takes the first matching key the role has; no match gives the fallback listing the role's questions. Keys: night, emerg, first, missed, recall, saturday, week, urgent, records, hours, pickup (creates a pending "Ready for pickup" draft for Pepper only if none is pending), waiting, script (`[what Dr. Garg wants said]`), flags. All figures come from queries; `records` and `hours` come from the `answer_sheet` setting (example content).

## Admin: AI provider (`admin.settings`)
`GET /api/admin/settings/ai`, `PUT /api/admin/settings/ai {provider?: off|deepseek|openrouter, base_url?, model?, api_key?, word_brief?, emergency_check?, allow_transcript_labeling?}`, `POST /api/admin/settings/ai/test`. The key is stored Fernet-encrypted (`WALNUT_FERNET_KEY`; dev and test derive one from the secret key, production requires it) in the internal `ai_config` setting and is never returned, logged or put in an event (only the last 4 characters are shown). Each save rewrites `WALNUT_LLM_ENV_PATH` with mode 0640 (`PROVIDER=`, `BASE_URL=`, `MODEL=`, `API_KEY=`, `ALLOW_TRANSCRIPT_LABELING=0|1`; `API_KEY` is empty when the provider is off); the PUT response has `llm_env_written` (false if the folder is not writable). Base URLs must be https (localhost allowed outside production). `adapters/llm.py`: `LlmClient.from_settings(db)`, `chat`, `complete_json`; 15 second timeout, errors carry no request or response body. The test endpoint returns `{"ok":bool,"detail":str}`.

## Admin: Retell (`admin.settings`)
`GET/PUT /api/admin/settings/retell {enabled?, dry_run?, regenerate_secret?, retell_api_key?}`; see `docs/RETELL.md`. Routes: `POST /api/retell/functions/{check_emergency|get_open_slots|create_appointment_request|create_callback|get_emergency_hospital}` and `POST /api/retell/webhook`; 404 while disabled, 401 without the right `X-Walnut-Secret`; session cookie and CSRF are not used. Code: `retell/schema.py`, `mapping.py`, `verify.py`, `routes.py`.

## Import (`admin.import`) and PIMS
`POST /api/admin/import/{calls|appointments|clients|pets|lab_results|analytics}` multipart `file` (`.csv` or `.jsonl`, 10 MB max, `.json` also for analytics as a list or `{"meta","rows"}`). Response `{"kind","accepted","rejected":[{"row","error"}] (first 50),"rejected_total"}`; good rows load even if others are rejected (row numbers are 1-based data rows). Bad file: 400, too large: 413, unknown kind: 404. Naive timestamps are read as Vancouver time.
- `calls`: `call_id, timestamp, caller_type, intent, outcome, urgency, red_flags (JSON list or ;-separated), promised_followup, contains_private_content, summary`. Rows are upserted by `call_id`. Private rows are stored with `summary` NULL and are hidden by the `visible_calls` view, which every metric reads. Event (tool `Call logger`): `Imported 4 calls`.
- `appointments` (`id, start, client_id, patient_id, provider, visit_type, status`), `clients` (`id, name, last_visit, recall_overdue`), `pets` (`id, client_id, name, species, breed, last_exam`), `lab_results` (`id, patient_id, test, resulted_at, status`) load the read-only `pims_*` tables by external id; event (tool `ImproMed sync`): `Imported 2 appointments (read only)`. Overdue recalls count `pims_patients.last_exam` older than 13 months (the example 120 is used only when no patients are loaded).
- `analytics`: `source` (`derived_from_real_calls` or `demo`), `metric, dim1, dim2, value, n`; importing replaces all existing rows of the sources present in the file.
`adapters/pims.py`: `PimsAdapter` protocol (`appointments(day)`, `clients()`, `patients()`, `lab_results(since)`) and `CsvExportAdapter(dir)` reading `appointments.csv, clients.csv, patients.csv, lab_results.csv`. `python -m app.pims_sync --dir samples/pims` loads a folder. Sample files (all invented): `samples/calls.csv`, `samples/calls.jsonl`, `samples/analytics.json`, `samples/pims/*.csv`. Shared validators: `services/importers.py`.

## Adapters
`adapters/messaging.py`: `SmsAdapter` / `VoiceAdapter` protocols, `DryRunSms(db).send(to_label, body)` and `DryRunVoice(db).call(to_label, script)` write `outbox` rows (`would_send`) and log `would send ...`. `queue_sms()` and `adapters/sms.py` remain as wave 1 compatible entry points.

## Tests
`tests/test_engine.py` (paths, every keyword from the list and `config/emergency_keywords.yaml` from the start node and mid-booking, 32 adversarial texts, hospitals, slots, toggles, template provenance) and `tests/test_wave2_api.py` (everything above). `tests/test_permissions.py` treats `/api/retell/*` as secret-protected routes.

## Admin TELUS endpoints (`admin.telus`, admin flag required)
Binding contract: `docs/CONTRACT_TELUS_RECORDINGS.md`. The app only touches the bridge folder (`WALNUT_TELUS_DIR`, default `/var/lib/walnut-desk-telus`); the separate worker does the TELUS work. Code: `app/api/admin_telus.py`.
- `GET /api/admin/telus` returns `{"settings":{recordings_enabled,max_gb,keep_days,max_per_run_cap,updated_by,updated_at},"status":{updated_at,connection,job,storage,rejected_requests}|null,"bridge_ok":bool,"busy":bool,"messages":{code:text}}`. `status` is counts only. It is `null` when `status.json` is missing, over 64 KB, a symlink, not a regular file, not JSON, or fails validation (unknown keys are dropped, wrong types reject the whole file). A status with job state `queued` or `running` and `updated_at` older than 24 hours is shown as `failed` with code `error`. When a request file is waiting and the status says otherwise, the response shows job state `queued` (the app never writes `status.json`). `busy` is true while a job is queued or running or a request file is waiting. Missing or invalid `config.json` gives the defaults with `recordings_enabled` false. `messages` maps every status code to a plain sentence.
- `PUT /api/admin/telus/settings {recordings_enabled:bool, max_gb:1..50, keep_days:1..90, max_per_run_cap:1..1000}` (all required, strict types, unknown fields rejected with 422) writes `config.json` atomically (temp file, mode 660, rename) and returns `{"settings"}`. Event (tool `Admin`): `telus_settings_changed`.
- `POST /api/admin/telus/check` writes a request file `requests/<uuid4 hex>.json` (mode 660, mode `check`) and returns `{"job_id"}`. Event: `telus_check_requested`.
- `POST /api/admin/telus/recordings {days:1..30, max_items:1..1000}` does the same for mode `recordings` and returns `{"job_id"}`. Event: `telus_recordings_requested`. 400 with plain text while recordings are turned off in the saved settings.
- Both POSTs return 409 while a job is queued or running (status file or a waiting request file under 24 hours old), and 503 with plain text when the bridge folder is missing, not a real directory (a symlink is refused) or not writable. A request file holds `{v,id,mode,days,max_items,requested_by,requested_at}` and never any call content. Events carry text only.
