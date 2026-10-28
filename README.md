# Walnut Grove Desk

A staff work app for a small veterinary clinic (Walnut Grove Animal Hospital, British Columbia). It sits beside the clinic's existing practice software and gives the front desk, the nurses and the owner-veterinarian one shared record for overnight calls, callbacks, lab results, message approvals, the staff schedule and an after-hours assistant.

Status: **prototype, example data.** Every client, pet, staff member, price and callback in the app is invented. Real call recordings were used only to produce aggregate counts and reviewed drafts, and the recordings themselves never left a private directory on the server. See `docs/SECURITY.md` before loading any real clinic data.

Live (sign-in required): https://77-42-69-194.sslip.io

---

## What it does

| Screen | Who | What |
|---|---|---|
| Brief | owner | Four numbers from SQL, overnight summary, suggestions to approve, a call-volume chart (real recorded volume or example numbers) and a short brief |
| Overnight | all | Conversations the after-hours assistant handled: confirm bookings, check emergencies, move requests to callbacks. Includes a "Try it as a client" simulator (text and phone) |
| Callbacks | desk, nurse, owner | Shared callback list (claim, mark done), with message drafts to approve for the desk |
| Lab results | all | Result stages (received, doctor reviewed, client told). Nurses advance them |
| Schedule | all, read only | Week roster with phone cover per day. Approved suggestions appear as dashed shifts |
| After-hours setup | owner | What the assistant may book, visit types, emergency word list, emergency hospitals (confirmed by the clinic only) and the exact wording |
| How it fits | all | The tools, and a live feed of the shared record |
| Assistant (side panel) | all | Answers prebuilt questions from database queries. Never a model guessing |
| Admin | admin | Users, audit log, CSV import, settings (clinic name, banner, rule thresholds, AI provider, Retell hook) |

Every action persists in the database and appears for other signed-in people within a few seconds (server-sent events, with a five-second poll as a fallback).

## Design rules the code follows

1. **One record.** Every change writes an `events` row. That table is the shared feed, the audit log and the source for the assistant. Triggers make it append-only.
2. **Permissions on the server.** Each route declares a permission. A test walks every API route and checks that the desk and nurse roles get 403 where they should.
3. **No model writes to clients.** The after-hours assistant speaks only from fixed templates. Dose and advice requests get a fixed refusal. A model, if the owner enables one, may only reword the owner's brief, and every number in the result is checked against the computed metrics.
4. **Emergencies fail toward safety.** Built-in emergency rules (phrases plus proximity rules such as "breathing" near "fast") are a floor that the owner's list can add to but never remove from. The assistant names a hospital only when the clinic has confirmed it.
5. **Real call data stays private.** Transcripts live in a directory only a separate Linux user can read. The web app receives only aggregate counts with small cells removed.
6. **Off by default.** AI features and the Retell voice hook are off. The SMS and voice adapters only record "would send".

## Architecture

```
Browser (React + TypeScript, Vite)
   |  HTTPS
Caddy  (TLS via Let's Encrypt, HSTS, reverse proxy, SSE not buffered)
   |  127.0.0.1:8000
FastAPI app (one uvicorn worker, systemd, runs as walnut-app)
   |- /api/*  JSON, CSRF header on writes, cookie sessions
   |- /api/stream  server-sent events
   |- SPA fallback serves frontend/dist
   |
SQLite (WAL, foreign keys on, BEGIN IMMEDIATE for writes)
   |- daily online backup (14 days kept)
```

| Path | Contents |
|---|---|
| `backend/app/` | FastAPI app: `api/` routes, `models/`, `security/` (permissions, sessions, passwords, rate limiting, headers), `services/` (events, SSE broadcaster, settings, metrics, number check), `aftercare/` (emergency detection, templates, state machine), `rules/` (suggestion rules), `assistant/` (panel answers), `adapters/` (LLM client, dry-run SMS and voice, PIMS CSV reader), `retell/` (voice-agent hook) |
| `backend/alembic/` | Database migrations (run at service start) |
| `backend/tests/` | pytest suite (136 tests) |
| `frontend/src/` | React screens, live-update client, API client |
| `e2e/` | Playwright end-to-end flows, run against a URL |
| `pipeline/` | Private call-analytics pipeline (code only, no data): loading, audit, rules labeler, gold-set tools, evaluation, purge, analytics, leak check, wipe script (30 tests on synthetic data) |
| `eval/` | After-hours assistant evaluation: 181 scenarios and the runner |
| `config/` | `templates.yaml` (recurring voicemails and reminders, draft) and `emergency_keywords.yaml` (urgency phrases, draft for owner approval) |
| `samples/` | Invented sample files for every import type and the PIMS adapter |
| `deploy/` | systemd units (app, backup, rules), Caddyfile, journald cap, health, backup, restore scripts |
| `reference/` | The single-file prototype the screens were built from (fake data) |
| `docs/` | Plan, contracts, decisions, security, operations, API, Retell hook, evaluation reports, screenshots |

### Data

- Operational tables: users, sessions, events, callbacks, lab results, drafts, shifts, suggestions, conversations, appointment requests, emergency routings, hospitals, visit types, open slots, settings, outbox.
- Analytics: `call_analytics` (aggregate rows only, `source = derived_from_real_calls` or `demo`), `imported_calls` (calls import schema; rows flagged private keep no summary and are excluded from every metric through the `visible_calls` view).
- The demo `call_log_demo` table holds synthetic missed-call history for the "example" labels.

## Real call analytics (summary)

Read `docs/DATA_AUDIT.md`, `docs/LABELER_EVAL.md` and `docs/ANSWER_SHEET_DRAFT.md` for the full picture. In short:

- 3,637 recorded calls, 96 hours, July 29 to October 26, 2026.
- A gold set of 112 hand-labeled calls. The rules labeler reaches 1.0 emergency recall on it after tuning, but **private-call recall is 0.57 and intent accuracy is 0.39**, both below the 0.95 target. Those numbers are optimistic because the rules were tuned on the same calls.
- To compensate for weak private detection, "when in doubt" is treated as private. In total 949 calls had their text deleted, including from the search index.
- The app receives 227 aggregate analytics rows. Peak weekday volume is about 4.5 recorded calls per hour at 9 am. About 11 percent of calls arrive after hours and 18 percent on weekends.

## Assistant evaluation (summary)

`docs/ASSISTANT_EVAL.md` reports on 181 scenarios. Emergency recall is 1.0 (41 of 41) and no reply gave medical advice or a dose. The first run scored 0.756; the rules were then improved while looking at the same scenarios, so a fresh, independently written set is the honest next check.

## Running it locally

Prerequisites: Python 3.12 (managed by `uv`), Node 22, SQLite 3.

```bash
# backend
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r backend/requirements.txt
cd backend
export WALNUT_ENV=dev WALNUT_SECRET_KEY=dev-only \
       WALNUT_FERNET_KEY=$(../.venv/bin/python -c 'from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())') \
       WALNUT_DB_PATH=/tmp/walnut-dev.db
../.venv/bin/python -m app.seed --reset-demo --create-users   # prints one-time passwords once
../.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000

# frontend (dev server proxies /api to 127.0.0.1:8001 by default; point it at 8000 or build instead)
cd frontend && npm ci && npm run build

# tests
cd backend && WALNUT_ENV=test ../.venv/bin/pytest -q
cd .. && pipeline/.venv/bin/python -m pytest pipeline/tests -q
```

Production settings live in an environment file (see `deploy/` and `docs/OPERATIONS.md`). Secrets are never committed.

## Deploying

`docs/OPERATIONS.md` covers installing the units, Caddy, the firewall (SSH, 80 and 443 only), backups, restores, health checks, updates and the fallback tunnel. `deploy/systemd/` holds the unit files; `deploy/Caddyfile` is the proxy configuration.

## Security

`docs/SECURITY.md` lists threats and protections, what is not protected, and what must change before real data: encrypted off-site backups, two-factor sign-in, access reviews, audit retention, consent and recording rules, a breach process, and a decision on the AI provider's data location (BC privacy law applies).

Key controls: argon2id passwords with forced first-change, server-side sessions with secure cookies, CSRF tokens, lockout and rate limits, security headers (CSP `default-src 'self'`, HSTS, frame denial), parameterised queries, escaped output, app and private data kept under separate Linux users, and dependency audits (`pip-audit` clean; the frontend ships with zero production advisories).

## Known gaps and open decisions

- Retell's voice-agent signature check was written without reference to its live docs; the shared secret header is the real protection. Verify before going live.
- No two-factor sign-in, no off-site backups yet.
- Real emergency hospital: none is confirmed. The owner must confirm one by phone.
- Opening hours and prices heard in calls conflict and need the clinic to confirm.
- Reboot was not tested on the server; services are enabled at boot.
- `docs/DECISIONS.md` records every choice made on the owner's behalf.

## Documentation index

| File | Purpose |
|---|---|
| `docs/PLAN.md` | Architecture, data model, permissions matrix, milestones |
| `docs/CONTRACT.md`, `docs/CONTRACT_WAVE2.md` | Binding rules and API shapes used to build the app |
| `docs/API.md` | Every endpoint, permission and response shape |
| `docs/RETELL.md` | Voice-agent hook: request and response shapes, runnable curl examples |
| `docs/DECISIONS.md` | Decisions made on the owner's behalf, with reasons |
| `docs/SECURITY.md` | Threats, protections, gaps, and what to fix before real data |
| `docs/OPERATIONS.md` | Restart, update, backup, restore, user and settings tasks |
| `docs/SERVER_NOTES.md` | Reconnaissance and installed versions |
| `docs/DATA_AUDIT.md` | Counts-only audit of the call file |
| `docs/LABELER_EVAL.md` | Rules labeler accuracy against the gold set |
| `docs/ANSWER_SHEET_DRAFT.md` | Caller questions and answers heard in calls (draft, needs confirmation) |
| `docs/ASSISTANT_EVAL.md` | After-hours assistant results and an honest history note |
| `docs/PROGRESS.md` | Milestone status with evidence |
| `docs/screenshots/` | Reference and app screenshots for every screen and role |

## Licence

Private repository. Not licensed for reuse.
