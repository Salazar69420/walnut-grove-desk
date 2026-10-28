# Walnut Grove Desk: backend API (as built, M2 and M3)

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
`GET /api/export/{name}.csv`, perm `export.use` plus the list's own view perm: `callbacks` (`callbacks.view`), `results` (`results.view`), `drafts` (`drafts.view`), `shifts` (`schedule.view`), `events` (`tools.view`, feed tools only), `audit` (`admin.audit`). Unknown name 404, missing list perm 403. Response `text/csv` with `Content-Disposition: attachment; filename="walnut-<name>.csv"`, CRLF rows. String cells starting with `=`, `+`, `-`, `@`, tab or CR get a leading `'`. Each export writes an `Admin` event (`export`). `overnight` and `analytics` exports arrive with their milestones.

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
