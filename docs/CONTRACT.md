# Build contract (for every implementer)

Read `docs/PLAN.md` first, then this file. This file wins on any detail. The UI source of truth is `reference/mock-reference.html` (fake data only). Read it fully before touching UI or seed data.

## Hard rules
- No em dashes anywhere (code comments, UI copy, docs). Use commas, periods, or "to".
- Plain short words in the UI. Large click targets. Wording copied from the reference where a screen exists there.
- Never invent facts about the real clinic (prices, hours, hospitals, phone numbers). Example content says so.
- Do not read, open, grep or copy anything under `/srv/walnut-private` or any file named `*transcripts*`. You will never need it.
- Never send anything real. SMS and voice adapters only write `outbox` rows with status `would_send` and log `would send`.
- No debug endpoints. No `/docs`, `/redoc`, `/openapi.json` when `WALNUT_ENV=production`.
- All times stored as UTC ISO-8601 in the DB; displayed in `America/Vancouver`.

## Stack and paths
- Repo: `/opt/walnut-desk`. Python 3.12 venv at `/opt/walnut-desk/.venv` created with `UV_PYTHON_INSTALL_DIR=/opt/uv-python uv venv --python 3.12 /opt/walnut-desk/.venv`, deps installed with `uv pip install --python /opt/walnut-desk/.venv/bin/python -r backend/requirements.txt` (pin exact versions in `backend/requirements.txt`).
- Backend package: `backend/app` (import name `app`, run from `backend/`: `cd backend && ../.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000`).
- FastAPI, SQLAlchemy 2.x ORM (declarative, `Mapped[]`), Alembic (`backend/alembic`, one initial migration, later milestones add migrations), SQLite with `PRAGMA journal_mode=WAL`, `foreign_keys=ON`, `busy_timeout=5000` on every connection.
- Config from env (see `/etc/walnut-desk/app.env`): `WALNUT_SECRET_KEY`, `WALNUT_FERNET_KEY`, `WALNUT_DB_PATH`, `WALNUT_LLM_ENV_PATH`, `WALNUT_IMPORT_DIR`, `WALNUT_ENV` (`production`|`dev`|`test`), `WALNUT_PUBLIC_URL`. Tests use `WALNUT_ENV=test` and a temp DB.
- Frontend: `frontend/` React 18 + TypeScript + Vite, `react-router-dom`. Build output `frontend/dist`, served by FastAPI at `/` with SPA fallback (any non-`/api` GET returns `index.html`). Plain CSS: copy the `:root` tokens, dark-mode blocks and component classes from the reference into `frontend/src/styles/app.css` and reuse the same class names (`.card`, `.row`, `.tag`, `.btn`, `.kpi`, `.shift`, `.feed`, `.assist`, `.phone`, `.bub`, etc). Fonts via `@fontsource/figtree` and `@fontsource/ibm-plex-mono` (no Google Fonts link, CSP is `'self'`).
- Single uvicorn worker. Live updates via in-process SSE broadcaster.

## Auth and security (backend)
- `users`: id, username (unique, lowercase), email (nullable), display_name, title, initials, role (`owner`|`desk`|`nurse`), is_admin bool, password_hash (argon2id via argon2-cffi defaults), must_change_password bool, active bool, failed_count int, locked_until datetime nullable, created_at, last_login_at.
- `sessions`: id, token_hash (sha256 of a 32-byte urlsafe token), user_id, csrf_token, created_at, last_seen_at, expires_at, ip, user_agent. Idle timeout 12 h, absolute 7 days.
- Cookie name `__Host-wgd` in production (Secure, HttpOnly, SameSite=Lax, Path=/, no Domain). In `dev`/`test` use `wgd` without Secure.
- CSRF: `GET /api/auth/me` returns `csrf_token`. Every non-GET `/api` request must send header `X-CSRF-Token` equal to the session's token, else 403 `{"detail":"csrf"}`. Exempt: `POST /api/auth/login` (no session yet) and `/api/retell/*` (own secret).
- Login: `POST /api/auth/login {username, password}`. Generic error `{"detail":"Wrong username or password"}` with 401. After 5 failures for a username: lock 15 min (423 `{"detail":"Too many tries. Wait 15 minutes."}`). Per-IP limit: 20 attempts per 10 min (429). Inactive user = same generic 401.
- If `must_change_password`, every `/api` route except `auth/me`, `auth/logout`, `auth/change-password` returns 403 `{"detail":"password_change_required"}`.
- `POST /api/auth/change-password {current_password, new_password}`: new >= 12 chars, not equal to username, not in a small common-password list, not equal to current.
- Security headers middleware on every response: `Content-Security-Policy: default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; font-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'`, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: same-origin`, `Permissions-Policy: camera=(), microphone=(), geolocation=()`. HSTS is set by Caddy.
- Request bodies validated with pydantic models with max lengths (text fields <= 2000 chars, names <= 120).

## Permissions (single source of truth: `backend/app/security/permissions.py`)
```
PERMS = {
 "overnight.view": {"desk","nurse","owner"},
 "overnight.manage": {"desk","owner"},            # confirm, entered, to-callback, ack
 "overnight.followup": {"desk","nurse","owner"},  # mark emergency followed up
 "callbacks.view": {"desk","nurse","owner"},
 "callbacks.work": {"desk","nurse","owner"},      # claim, done
 "drafts.view": {"desk","owner"},
 "drafts.approve": {"desk","owner"},              # edit, approve
 "results.view": {"desk","nurse","owner"},
 "results.advance": {"nurse","owner"},
 "schedule.view": {"desk","nurse","owner"},
 "brief.view": {"owner"},
 "suggestions.decide": {"owner"},
 "afterhours.view": {"owner"},
 "afterhours.edit": {"owner"},
 "sim.use": {"desk","nurse","owner"},
 "tools.view": {"desk","nurse","owner"},
 "assistant.use": {"desk","nurse","owner"},
 "export.use": {"desk","nurse","owner"},          # each export also checks the list's view perm
}
ADMIN_PERMS = {"admin.users","admin.audit","admin.settings","admin.import","admin.reset_demo"}  # require is_admin
```
Use a FastAPI dependency `require("perm")`. `GET /api/auth/me` returns the user's list of perms so the UI hides what it cannot use. A test must iterate over every route in the app and assert desk and nurse get 403 on routes they lack.

## Shared record
`events`: id, ts, tool (one of the tool names below), actor_id nullable, actor_name, action (snake_case verb, e.g. `callback_claimed`), entity_type, entity_id, text (plain sentence for the feed, reference wording), data_json, ip. SQLite triggers `BEFORE UPDATE` and `BEFORE DELETE` on `events` raise `events are append-only`. Every state change goes through `services/events.py: record(db, tool, action, text, actor, entity_type, entity_id, data)` which inserts the row and publishes to the SSE broadcaster after commit. Tool names (exact): `Call logger`, `Callback list`, `Results tracker`, `Recall sender`, `After-hours assistant`, `Staff roster`, `ImproMed sync`, plus `Admin` and `Sign-in` for audit-only actions (these two are excluded from the How it fits feed and tool cards but shown in Admin > Audit log).

SSE: `GET /api/stream` (needs login) sends `event: change\ndata: {"entity":"callbacks","id":12,"event_id":345}\n\n` per event and a `: ping` comment every 15 s. Entities: `callbacks, results, drafts, shifts, suggestions, overnight, events, settings, hospitals, visit_types, users, analytics`. Frontend refetches lists for that entity. If SSE fails, poll `GET /api/events?since=<last_id>` every 5 s.

## Operational tables (all demo data, invented)
- `callbacks`: id, client_label, pet_label, what, kind (`Results|Triage|Pickup|Refill|Records|Courier|Booking|Message`), via (`Voicemail|Missed call|Call|Text`), received_at, priority (`high|normal`), red_flag bool, owner_id nullable, owner_name, status (`new|claimed|done`), source (`seed|sim|retell|overnight|assistant`), conversation_id nullable, closed_at.
- `lab_results`: id, pet_label, owner_label, test, received_at, stage (0 Received, 1 Doctor reviewed, 2 Client told), reviewed_by, told_by, updated_at. `age_days` computed from received_at in Vancouver dates.
- `drafts`: id, kind, to_label, body, channel (`text`), status (`pending|sent`), created_by_tool, edited_by, approved_by, approved_at, suggestion_id.
- `shifts`: id, weekday (0=Mon..6=Sun), person_label, user_id nullable, role (`desk|nurse|doc|open`), start_min, end_min, proposed bool (dashed), suggestion_id.
- `suggestions`: id, rule_key, title, why, evidence_json, status (`open|approved|dismissed`), decided_by, decided_at, created_at.
- `conversations`: id, channel (`text|call`), client_label, pet_label, kind (`booking|emergency|callback`), status (booking: `new|confirmed|entered|called`; emergency: `new|handled`; callback: `new|handled`), preferred_slot (display string), summary, messages_json (list of `{"who":"bot"|"client","text":...}`), source (`seed|sim|retell`), is_test bool, created_by, created_at.
- `appointment_requests`, `emergency_routings`, `sim_sessions`, `hospitals`, `visit_types`, `open_slots`, `settings`, `call_log_demo`, `call_analytics`, `imported_calls`, `pims_*`, `outbox`: see PLAN.md data model.

## Seed
`backend/app/seed/reference_data.py` holds the reference arrays as Python data (callbacks c1..c7, results r1..r5 with `age` in days, drafts d1..d3, suggestions r1..r4, roster from `buildRoster()`, overnight o1..o5 with conversations, visit types, hospitals, the 7 event log lines, tool cards `TOOLS`, chips `CHIPS`, greetings). Times in the reference like `9:31 am` become today's date in Vancouver at that time (overnight items with pm times become yesterday). `age` becomes `received_at = today - age days`. Hospital row 1: name "Boundary Bay Veterinary Specialty Hospital", place "Surrey", hours_claim "Says it is open 24 hours a day, 7 days a week", confirmed false, phone and address empty. Row 2: placeholder "Backup hospital".
`python -m app.seed --reset-demo` reseeds operational tables. `python -m app.seed --create-users` creates the 6 accounts (see PLAN.md) with random 20-char passwords, `must_change_password=true`, and prints `username<TAB>password` once to stdout. It refuses to overwrite existing users.

## API response shapes (JSON, snake_case)
- `GET /api/auth/me` -> `{"user":{"id","username","display_name","title","initials","role","is_admin","must_change_password"},"perms":[...],"csrf_token":"...","settings":{"clinic_name","banner_on"}}` or 401.
- Lists return `{"items":[...]}`; each item has `id` and display fields named as in the tables plus computed fields (`age_days`, `stage_names`, `at_display` like `9:31 am`, `owner_name`).
- Actions return the updated item `{"item":{...}}` and write an event with the reference feed text, e.g. claim: `"Priya claimed Dhillon (Simba (cat))"`, done: `"Priya closed Dhillon (results)"`, advance: `"Maple (spaniel), Blood panel: doctor reviewed"`, approve draft: `"Priya approved: Annual exam recall to Ms. Chen, for Biscuit"` (also an `outbox` row `would_send`).
- Conflicts (claiming an already-claimed callback, advancing a closed result) return 409 `{"detail":"Already taken by Marcus"}`.
- `GET /api/export/{name}.csv` returns `text/csv` with `Content-Disposition: attachment`. Cells starting with `= + - @` are prefixed with `'`.

## Frontend layout (match the reference)
- Top bar: logo `W`, clinic product name "Walnut Grove Desk", banner pill "Prototype, example data" (only if `banner_on`), spacer, signed-in name, theme toggle (Light / Dark / Auto, stored in localStorage), "Sign out".
- Three-column grid: left nav (user card with initials, name, title; nav items with counts as in `renderSide`, hidden if no perm), main work area, right assistant panel. Same breakpoints as the reference (1180px, 780px).
- Nav order: Brief, Overnight, Callbacks, Lab results, Schedule, After-hours setup, How it fits, Admin (admin only). Default landing: owner Brief, desk Overnight, nurse Lab results.
- Toasts with the reference wording. Every button disabled while its request is in flight.
- Screens fetch from the API only; no local demo state.

## Tests
- `backend/tests/` pytest, `cd backend && ../.venv/bin/pytest -q`. Use a fresh temp DB per test session with migrations or `create_all`.
- E2E: `e2e/` Python Playwright (`pip install playwright` into the venv, `playwright install --with-deps chromium`), base URL from env `WALNUT_E2E_URL`.
