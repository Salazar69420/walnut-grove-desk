# HANDOFF.md: continue the Walnut Grove Desk work

This file is for a fresh Claude Code instance (or a human) picking up the project with no prior context. Read it top to bottom once, then read `docs/DECISIONS.md` and `docs/SECURITY.md` before changing anything.

**This repository is PUBLIC.** Never commit passwords, API keys, the Fernet or app secret, server env files, database files, transcripts, call audio, or anything derived from real call text beyond aggregate counts. Passwords and secrets live only on the server (see section 6).

---

## 0. Ground rules the owner set (follow these)

1. **Never read or open `/srv/walnut-private/`** (except the pipeline user, through the commands in `pipeline/README.md`, and only when the owner asks for pipeline work). Never print transcript text. Mask digits when showing any call text.
2. **No em dashes** in any text, code comment, UI copy or document. Use commas, periods or "to".
3. **Plain, short words in the UI.** Big click targets. One clear screen per role.
4. **Do not invent facts about the real clinic** (prices, hours, policies, hospitals, phone numbers, staff). Everything in the app is example content and says so. Facts heard in calls are "heard in calls, needs the clinic to confirm".
5. **Emergencies fail toward safety.** Never let the assistant give medical advice, doses, or say a pet is fine. Never name a hospital that the clinic has not confirmed.
6. **Never send anything real.** SMS and voice adapters only log "would send" to the `outbox` table. The Retell hook stays off and in dry run.
7. **Do not disable TLS verification. Do not expose anything beyond the app** (no database port, no debug endpoints, no `/docs` in production).
8. The owner wants a confirmation before any hard-to-reverse or outward-facing action (pushing public content, deleting data, changing live settings) unless they have explicitly said to proceed.
9. Commit messages: do not put model names in them (system rule). Earlier commits (`2398cbb` to `b55f8e9`) carry an older co-author line; leave history alone unless the owner asks.

---

## 1. What exists

A staff work app for Walnut Grove Animal Hospital (British Columbia): overnight after-hours inbox, shared callbacks, lab result stages, message approvals, staff schedule, a Brief for the owner with suggestions, an after-hours assistant with a simulator, a side assistant that answers from database queries, import/export, and a Retell voice-agent hook (off).

- **Live URL:** https://77-42-69-194.sslip.io (stable while the server IP `77.42.69.194` stays the same; not a tunnel)
- **Repository:** https://github.com/Salazar69420/walnut-grove-desk (public, branch `main`)
- **Server:** Hetzner VM named `Veto`, Ubuntu 26.04 LTS, 2 vCPU, 3.7 GiB RAM, 2 GiB swap at `/swapfile`, root login, systemd. Not a container.
- **Working copy on server:** `/opt/walnut-desk` (git, branch `main`, clean at handoff)

**Clinic CRM (EliseAI-style, for a vet clinic), built on invented data:** Home, Tasks, Contacts with Guest Card, conversations (practice mode), Calendar, Knowledge & Fees with a Pending approval queue, Phone call log, Campaigns, Reports. Plan: `docs/PLAN_CLINIC_CRM.md`. What the real calls say about priorities: `docs/CALL_ANALYSIS.md`. Endpoints: `docs/API.md`.

Read `README.md` in the repo for the architecture map and `docs/PROGRESS.md` for the milestone status with evidence.

---

## 2. Repository map

| Path | What it is | Start here when |
|---|---|---|
| `README.md` | Overview, architecture, run, deploy, security, known gaps | First read |
| `docs/PLAN.md` | Original plan, data model, permissions matrix, milestones | Changing the design |
| `docs/CONTRACT.md`, `docs/CONTRACT_WAVE2.md` | Binding rules and API shapes the code was built to | Adding endpoints |
| `docs/API.md` | Every endpoint, permission, response shape | Touching the API |
| `docs/DECISIONS.md` | Every choice made on the owner's behalf, with reasons | Anything "why is it like this" |
| `docs/SECURITY.md` | Threats, protections, gaps, what to fix before real data | Before any real data or deployment |
| `docs/OPERATIONS.md` | Restart, update, backup, restore, users, settings | Running the server |
| `docs/RETELL.md` | Voice-agent hook, request shapes, curl examples | Retell work |
| `docs/DATA_AUDIT.md`, `docs/LABELER_EVAL.md` | Counts-only audit; rules labeler accuracy | Pipeline work |
| `docs/ANSWER_SHEET_DRAFT.md` | Caller questions and answers heard in calls (draft, unconfirmed) | Assistant wording |
| `docs/ASSISTANT_EVAL.md` | Assistant results plus an honest history note | Assistant changes |
| `docs/SERVER_NOTES.md` | Recon and installed versions | Server changes |
| `docs/screenshots/` | Reference screenshots and app screenshots per role | UI checks |
| `backend/app/` | FastAPI app (see section 3) | Most logic |
| `backend/tests/` | 136 pytest tests | Run after any backend change |
| `backend/alembic/versions/` | Migrations `0001_initial_schema.py`, `0002_imported_calls_summary_nullable.py` | Schema changes |
| `frontend/src/` | React + TypeScript screens; `views/` per screen; `live.ts` SSE client; `ctx.tsx` app state; `styles.css` from the reference | UI changes |
| `e2e/` | Playwright flows (`test_flows.py`), screenshot scripts, `reference_shots.py` | Testing against a URL |
| `pipeline/` | Private analytics pipeline code (no data). Has its own venv and 30 tests | Pipeline work |
| `eval/` | `scenarios.jsonl` (181 scenarios) and `run_eval.py` | Assistant evaluation |
| `config/` | `templates.yaml`, `emergency_keywords.yaml` (both DRAFT for owner approval) | Keyword and template review |
| `samples/` | Invented sample CSV/JSON for every import kind and the PIMS adapter | Import testing |
| `deploy/` | systemd units (`walnut-desk`, `walnut-backup`, `walnut-rules`), `Caddyfile`, journald conf, `bin/` health, backup and restore scripts | Deploying |
| `reference/mock-reference.html` | The single-file prototype the UI was built from (fake data) | UI source of truth |

### 2.1 backend/app layout

- `main.py`: app factory, routers, SPA fallback, production hides docs routes
- `config.py`: environment settings (`WALNUT_*` variables)
- `db.py`: engine with WAL, foreign keys, busy timeout; `BEGIN IMMEDIATE` on writes
- `models/`: SQLAlchemy models (core and ops tables)
- `security/`: `permissions.py` (single source of truth for roles), `auth.py`, `passwords.py` (argon2id), `ratelimit.py`, `headers.py`, `crypto.py` (Fernet helpers)
- `services/`: `events.py` (write the shared record and publish), `broadcaster.py` (SSE), `settings_store.py`, `metrics.py`, `numbercheck.py`, `importers.py`, `csvutil.py`, `timeutil.py`
- `api/`: `auth`, `admin`, `admin_ai`, `callbacks`, `results`, `drafts`, `shifts`, `overnight`, `sim`, `afterhours`, `brief`, `feed`, `export`, `imports`, `health`, `schemas`, `serializers`
- `aftercare/`: `engine.py` (state machine), `emergency.py` (built-in rules, proximity rules), `keywords.py` (merges settings and YAML; built-ins are a floor), `templates.py` (every client-facing string), `service.py` (context, outcomes, slots)
- `rules/engine.py` and `rules/__main__.py`: suggestion rules, run by `walnut-rules.timer`
- `assistant/`: handlers for the 14 side-panel questions
- `adapters/`: `llm.py` (OpenAI-compatible client, 15 s timeout, never logs bodies), `messaging.py` (dry-run SMS and voice), `sms.py` (shim), `pims.py` (read-only CSV adapter)
- `retell/`: `schema.py`, `mapping.py`, `verify.py` (signature check, unverified against live docs), `routes.py`
- `seed/`: `reference_data.py` (demo data copied from the prototype), `__init__.py`, `__main__.py` (`--reset-demo`, `--create-users`)
- `pims_sync.py`, `migrate.py`

### 2.2 Important behaviour rules in the code

- Permissions are declared with `require("perm")`. The walker test in `backend/tests` fails if any `/api` route has no perm tag (except its short allowlist).
- Every state change calls `events.record(...)`, which writes the append-only `events` row and publishes to SSE after commit.
- The settings `banner_on` and `clinic_name` come from `/api/auth/me`. The frontend reloads them on any `settings` event (fixed in `ae8c520`).
- Emergency detection: built-in list in `aftercare/emergency.py` is always active. Settings and `config/emergency_keywords.yaml` add to it.
- Client text comes only from `aftercare/templates.py`. A model never writes to clients.
- `visible_calls` is a SQL view that hides rows flagged `contains_private_content`. Every metric and list must read through it.

---

## 3. How to run and test

```bash
# backend tests (from repo root)
cd /opt/walnut-desk/backend && WALNUT_ENV=test ../.venv/bin/pytest -q          # expect 290 passed

# pipeline tests (synthetic data only)
cd /opt/walnut-desk && pipeline/.venv/bin/python -m pytest pipeline/tests -q   # expect 30 passed

# frontend
cd /opt/walnut-desk/frontend && npm ci && npm run build                         # zero TypeScript errors expected

# end to end against the public URL (needs a TSV of test accounts, never committed)
cd /opt/walnut-desk/e2e && WALNUT_E2E_URL=https://77-42-69-194.sslip.io \
  WALNUT_E2E_CREDS=<path to tsv> WALNUT_E2E_NEWPASS=<new password> \
  WALNUT_E2E_OWNER=<user> WALNUT_E2E_DESK=<user> WALNUT_E2E_NURSE=<user> \
  WALNUT_E2E_DESK_NAME=<display name of desk user> \
  /opt/walnut-tools/.venv/bin/pytest -q test_flows.py                            # expect 15 passed
```

Notes:
- Playwright 1.49.1 and Chromium live in `/opt/walnut-tools/.venv`. Do not run `playwright install` again.
- The login rate limit is 20 attempts per IP per 10 minutes. Repeated e2e runs need `delete from login_attempts;` in the app DB first.
- The e2e suite changes data. Reset the demo afterwards (section 5).
- Assistant evaluation: `cd backend && ../.venv/bin/python ../eval/run_eval.py --db <copy of app.db>` writes `docs/ASSISTANT_EVAL.md`. Always run it on a copy.

### 3.1 Git

- `origin` is `https://github.com/Salazar69420/walnut-grove-desk.git`. The `gh` CLI is signed in as `Salazar69420`. If `git push` asks for a username, run `gh auth setup-git` once.
- Before any push: run `git status`, then check the staged files for `*.db`, `*.env`, `*.tsv`, anything under `/srv`, transcripts, and call references (date-time-extension-hash strings shaped like `YYYYMMDD-HHMMSS-EXT-HASH`). Run `git grep` for the one-time passwords too. Anything you did not mean to publish stops the push.

---

## 4. Server: what runs and where

| Thing | Where | Notes |
|---|---|---|
| App service | `systemctl status walnut-desk` | runs as `walnut-app`, uvicorn on `127.0.0.1:8000`, runs `alembic upgrade head` before start |
| Web server | `systemctl status caddy` | TLS for `77-42-69-194.sslip.io`, reverse proxy, HSTS, access log to journal |
| Backup timer | `walnut-backup.timer` (daily 10:17 UTC) | online backup to `/var/lib/walnut-desk/backups/`, 14 days kept |
| Rules timer | `walnut-rules.timer` (every 15 min) | runs `python -m app.rules` |
| Firewall | `ufw` | allows 22, 80, 443 only |
| Health | `walnut-health` | service states, local and public health, cert days left, disk, last backup |
| Restore | `walnut-restore <backup file>` | stops app, keeps old DB as `app.db.before-restore.*`, checks integrity |
| App DB | `/var/lib/walnut-desk/app.db` (mode 600, owner `walnut-app`) | |
| App env | `/etc/walnut-desk/app.env` (mode 600) | secret key, Fernet key, paths. Never copy into the repo |
| LLM env | `/var/lib/walnut-desk-llm/llm.env` (written by the app, mode 0640) | group `walnut-llm` |
| Public URL file | `/var/lib/walnut-desk/public_url.txt` | |
| Caddy config | `/etc/caddy/Caddyfile` (copy of `deploy/Caddyfile`) | after edits: `caddy validate` then `systemctl reload caddy` |
| Units | `/etc/systemd/system/walnut-*.service` and `.timer` (copies of `deploy/systemd/`) | after edits: `systemctl daemon-reload` |
| Private store | `/srv/walnut-private/` (mode 700, owner `walnut-pipeline`) | contains `private.db`, `salt.bin`, `gold/`, `out/`, `inbox/`, `WIPE.sh`. The app user is denied (tested). Do not read |
| Initial passwords | `/root/HANDOFF.md` and `/root/.walnut-initial-passwords.tsv` (root only) | Not in the repo. Shred after handing out |
| Node, Caddy, uv, sqlite3 | system packages | Node 22.23.4, Caddy 2.11.7, uv 0.9.5, Python 3.12.12 under `/opt/uv-python` |

Current health at handoff: all services active; local and public `/healthz` ok; certificate 89 days left; disk 18 percent used.

Deployed code state at original handoff: code at `fd887e0`; `2fc9119` only added this file. For the current state see section 11 (live journal).

---

## 5. Data state (what is real and what is demo)

- **Operational cards:** all invented demo data copied from the prototype. Reset with `sudo -u walnut-app bash -c 'set -a; . /etc/walnut-desk/app.env; set +a; cd /opt/walnut-desk/backend && ../.venv/bin/python -m app.seed --reset-demo'`. The reset keeps users and settings, adds a "Demo data reset" event, and puts the three after-hours toggles back to their defaults.
- **Users:** `garg`, `priya`, `marcus`, `anita`, `jordan`, `krish` (real staff accounts, one-time passwords in `/root/HANDOFF.md`, must change at first sign-in). Three temporary accounts from the e2e run exist but are deactivated with random unknown passwords: `e2e_owner`, `e2e_desk`, `e2e_nurse`. Delete them if the owner agrees.
- **Analytics:** 227 aggregate rows in `call_analytics` with `source = derived_from_real_calls`. Cells under 5 are removed. Used by the Brief chart's "Real recorded volume" mode.
- **Real call data** in `/srv/walnut-private/`: 3,637 calls, 949 private-flagged calls had text deleted, 2,688 keep text (clinic work), 112 gold labels. The raw CSV was shredded after a checksum-verified move.
- **Who saw transcript text:** the orchestrating model (Opus) read masked snippets while building the gold set, drafts and eval. Subagents never saw it. DeepSeek and other providers have seen none.
- **Wipe everything real:** `sudo bash /srv/walnut-private/WIPE.sh` (`DRY_RUN=1` first). Tested on a copy. Also delete `/var/lib/walnut-desk/backups/*` if the aggregate rows should go.

---

## 6. Secrets and passwords (never in the repo)

- App secret and Fernet key: `/etc/walnut-desk/app.env`. Changing the Fernet key makes saved AI and Retell keys unreadable; re-enter them after.
- One-time passwords: `/root/HANDOFF.md`. Hand out, then `shred -u`.
- DeepSeek key: not yet provided. The owner will enter it in Admin > Settings > AI provider. Keep all three AI switches off until the owner decides (DeepSeek processes data outside Canada; transcript labeling needs a privacy decision).
- Retell secret: generated in Admin > Settings > Retell; shown once.
- To sign everyone out: `sqlite3 /var/lib/walnut-desk/app.db "delete from sessions"`.

---

## 7. Known gaps (be honest about these)

1. **Private-call detection is weak.** Rules catch 4 of 7 private calls in the gold set (target 0.95). The production policy compensates by flagging uncertain calls as private, which deleted text from about 27 percent of real work calls. A model labeler would do better, but only after a privacy decision.
2. **Intent accuracy is 0.39** on the gold set (rules only). Gold set is 112 calls, tuned on the same calls, so figures are optimistic.
3. **Assistant evaluation is not independent.** The first run scored emergency recall 0.756; rules were then improved while looking at the same scenarios. A fresh, independently written scenario set is the honest next test.
4. **Retell signature check** in `backend/app/retell/verify.py` was written from memory and is not verified against Retell's live docs. The shared-secret header is the real protection. Verify before going live.
5. **No two-factor sign-in.** Add before real data.
6. **No off-site or encrypted backups.** Backups sit on the same disk, unencrypted.
7. **No reboot test.** Services are enabled at boot, but run `reboot` on a planned window and then `walnut-health`.
8. **No emergency hospital is confirmed.** The assistant says "contact the nearest emergency veterinarian" until the clinic confirms one by phone. One call named "Animal Emergency of the Fraser Valley"; it is not in the list.
9. **Opening hours and prices conflict.** Staff on calls say open until 7 pm weekdays, 9 to 5 weekends, and the prototype example says closed after 6 pm. Exam prices heard: 30, 47 and about 50 dollars. The clinic must publish one set.
10. **Answer sheet, templates and keyword YAML are drafts** awaiting Dr. Garg's review (`docs/ANSWER_SHEET_DRAFT.md`, `config/templates.yaml`, `config/emergency_keywords.yaml`).
11. **The public repo already contains** call references (date, time, extension and hash of real calls) in four docs and the config, the heard prices and hours, the named third-party business, and paraphrased caller openings in `eval/scenarios.jsonl`. The owner chose to publish this as-is. If the owner later asks for a scrub, the plan is: remove those items, commit, and only rewrite history with explicit approval.
12. **Shreds are best effort** on SSDs and copy-on-write filesystems. Provider snapshots may keep old blocks.
13. **Retell's functions and webhook are dry-run and off.** Nothing has been connected to a real phone system.
14. **Import and some admin flows** (add user, deactivate, reset password, Test connection for AI, Retell secret regeneration) were not run end to end in the browser; they are covered by API tests only.

---

## 8. What to do next (in order)

1. **Owner review:** Dr. Garg approves or edits `config/emergency_keywords.yaml`, confirms (by phone) an emergency hospital with its real number and address, and sets real opening hours and one price list. Then update the answer sheet and the assistant's example hours text.
2. **Owner decision on DeepSeek:** enter the key only if the owner accepts the data location. Turn on "Word the brief" and "Second emergency check" first; keep "Allow call transcripts to be sent for labeling" off until a privacy decision.
3. **Independent assistant test:** write 60 to 100 new scenarios (someone other than the author), run `eval/run_eval.py` on a copy of the DB, and record results in `docs/ASSISTANT_EVAL.md`.
4. **Verify Retell signatures** against the current Retell docs, fix `verify.py`, then run one supervised test call in dry run before any real use.
5. **Security before real data:** two-factor sign-in, encrypted off-site backups, access review routine, audit retention period, written breach process (BC PIPA), a decision on hosting location.
6. **Stable domain:** move from sslip.io to a clinic-owned domain. Update `Caddyfile`, `public_url.txt`, and the README.
7. **Reboot test** in a planned window, then `walnut-health`.
8. **Shred one-time passwords** after handout: `shred -u /root/HANDOFF.md /root/.walnut-initial-passwords.tsv`; delete the three `e2e_*` accounts if the owner agrees.
9. **Real SMS and voice provider** only after the templates are approved. Replace `adapters/messaging.py` dry-run classes behind the same interface.
10. **Real data load** only after step 5. Use the import API and the `calls` schema in `docs/API.md`.

---

## 9. Working method that worked (to keep)

- Plan first in `docs/PLAN.md`, then contracts (`docs/CONTRACT*.md`) so parallel builders stayed consistent.
- Subagents: Sonnet for code, Haiku for mechanical work, Opus for review, safety logic and anything that reads call text.
- Each milestone: run its checks, update `docs/PROGRESS.md`, commit.
- Before claiming done: show command output (test counts, health, leak scan).
- Verify UI claims in a real browser against the public URL, not only localhost.

---

## 10. Quick reference: common tasks

| Task | Command or place |
|---|---|
| Health | `walnut-health` |
| Restart app | `systemctl restart walnut-desk` |
| Restart web | `systemctl restart caddy` |
| App logs | `journalctl -u walnut-desk -f` |
| Backup now | `systemctl start walnut-backup` |
| Restore | `walnut-restore /var/lib/walnut-desk/backups/app-<stamp>.db.gz` |
| Run rules now | `systemctl start walnut-rules` |
| Add user | Admin > Users > Add user (one-time password shown) |
| Reset password | Admin > Users > Reset password |
| Deactivate user | Admin > Users > Deactivate |
| Banner off or clinic name | Admin > Settings, then Save settings |
| Update app | `docs/OPERATIONS.md`, "Updating the app" |
| Current URL | `cat /var/lib/walnut-desk/public_url.txt` |

---

## 11. Live journal and shared-work protocol

Two people (each with their own fresh Claude Code session) work in this repo. This section is the shared memory between them. Sections 0 to 10 are the stable reference; this section is what changes every session.

### 11.1 Protocol (every session, both people)

1. **Start:** read this whole file, then `git fetch && git status -sb && git log --oneline -10`. Compare with "Current state" below. If `origin/main` is ahead of the recorded commit, read the new commits and journal entries first.
2. **Claim work** in "Open work / who is doing what" before starting, so the other session does not duplicate or collide. Name the files or areas you will touch.
3. **While working:** append a journal entry (11.4) after each meaningful step: what changed, why, which files, how it was verified (paste test counts or health output), and anything half-finished.
4. **Before ending or handing over:** update "Current state", move finished items out of "Open work", record decisions in `docs/DECISIONS.md`, commit, and say clearly in the final journal entry what the next session should do first.
5. **Never put secrets here.** This repo is public (see the warning at the top). No passwords, keys, env values, transcript text or call references. Point to where a secret lives instead.
6. **Server is shared and live.** Both sessions edit `/opt/walnut-desk` on the same server unless told otherwise. Check `git status` before editing (the other session may have uncommitted changes), do not `git reset --hard` or `git checkout .` over work you did not make, and commit small and often. Pushing to the public repo needs the owner's go-ahead (rule 8 in section 0).
7. **Entry format:** newest entry at the top of 11.4. Use `YYYY-MM-DD HH:MM UTC`, the person or session label, and a short title. Get the time with `date -u '+%F %H:%M'`.

### 11.2 Current state (update at every handoff)

- **Last updated:** 2026-10-31 (session 3)
- **Branch / commit:** `main`. Everything through the Calendar, Knowledge, Phone, Campaigns and Reports frontend is committed (`git log --oneline -8`). Check `git status -sb` against `origin/main` before starting.
- **Deployed.** The owner ran the build, restarted `walnut-desk` (05:09 UTC, migration 0004 applied) and seeded the new demo tables (23 knowledge entries, 5 fees, 70 calls, 3 campaigns). `walnut-health` all green, new routes answer 401 when signed out. Note: restarting while people have the app open makes systemd wait about 90 s for the live-update (SSE) connections to close, during which the site returns 502; this is normal.
- **Tests last run:** backend 290 passed (this session); frontend `npm run build` zero type errors; new screens checked in a real browser (Chromium) against a throwaway local copy (owner and desk, desktop and phone width, no console errors except the normal pre-login 401). Not yet checked on the live URL.
- **Uncommitted work:** none after the session 3 handoff commit.

### 11.3 Open work / who is doing what

| Item | Owner (session) | Status | Files or area | Notes |
|---|---|---|---|---|
| Deploy the CRM build to the live server | owner | **done 2026-10-31** | server | Still to do: sign in on the live URL as owner and desk and click through each new screen (only checked on a local copy so far) |
| Feature 1: independent assistant test | session 2 | done, pushed (`a2004c0`) | `eval/`, `emergency.py` | Emergency detection still not good enough for autonomous client messaging |
| Features 2 to 4 (Retell signature, two-factor, encrypted backups) | not started | open | | Order set by the owner, see section 8 |
| CRM phase 6: AI assistant (Suggest stage) | not started | open | `backend/app/services/knowledge.py` has `propose()` ready for the model to call | Needs the owner's privacy decision and a fresh sealed evaluation first |
| CRM phase 9: real sending, calling, voice | not started | open | `services/sender.py` is the only send path | Each integration behind its own switch and a staff-only pilot |
| Wire Phone and Home to real TELUS call metadata | not started | open | `pipeline/telus/`, `phone_calls.source = 'telus'` | Caller identity must come from TELUS, not the transcripts (docs/CALL_ANALYSIS.md) |

Backlog lives in section 7 (known gaps) and section 8 (next steps).

### 11.4 Journal (newest first)

**2026-10-31 UTC, session 3 (latest): staff assistant with AI tools and confirm-to-change**
- `app/assistant/staff_agent.py`, routes `POST /api/assistant/chat`, `/api/assistant/proposals/{id}/confirm|cancel`; panel `frontend/src/components/Assistant.tsx`; switch "AI for staff" (`assistant_ai.staff_enabled`, off by default, needs the OpenRouter key). Tools are filtered by the person's permissions; the model can only propose changes (kept 15 minutes in memory, single worker); Confirm re-checks the permission. Fixed: a refused confirm by another person no longer deletes the proposal.
- Checked the screenshot alarm: the emergency reply answered "My dog ... not able to breathe properly" (correct), not the 24/7 question; the panel shows newest first.
- Still waiting on the owner: a valid OpenRouter key (the stored old one returns 401), approving knowledge, a read-only TELUS app for playback, the product file. Tests 393 backend, 284 pipeline.

**2026-10-31 UTC, session 3 (latest): booking by text, product suggestions, playback error explained**
- `app/assistant/booking.py` (bookable hours setting `booking_hours`, free times from those hours minus Calendar events booked or requested, offers 1/2/3, picks create a REQUESTED Calendar event plus a booking task), confirm endpoint `POST /api/calendar/events/{id}/confirm` (can text the client), Calendar panel "Booking by text" (OFF by default; pre-filled Mon-Fri 9:00-17:30, Sat 9:00-13:30, Sun closed from what staff say on calls; owner must check). Order in `replies.suggest`: emergency > medical > booking > AI > rules.
- `app/assistant/upsell.py`, `products` table, Knowledge & Fees > Products (CSV import, approve one by one, suggestions switch OFF by default): at most one approved pitch when its triggers fit, never in hard moments, not again within `days_between`. The owner said a product file would be provided; it has NOT been received yet.
- Playback error: the read helper refuses the all-permission TELUS login (by design). An override was blocked by the permission system as security weakening. Fix for the owner: create a TELUS app with only Read permissions and run `telus-setup --force` on the server.
- Tests 386 backend, 284 pipeline. Migration 0009 live.

**2026-10-31 UTC, session 3 (latest): AI-written replies through OpenRouter (pilot contact only)**
- `app/assistant/ai.py`, `app/api/admin_assistant.py`, `frontend/src/views/AssistantTab.tsx` (Admin > AI assistant). Model default `deepseek/deepseek-v4.1-flash-1017` (checked on the live OpenRouter model list: exists, supports structured outputs, $0.15 / $0.60 per million tokens). Request uses `response_format` json_schema strict, `provider.require_parameters` and `provider.data_collection: deny` (owner can turn the privacy option off). Only first name and pet names are sent.
- Order inside `replies.suggest`: emergency (fixed text) > medical (fixed text) > AI (only if on, key set, contact assistant on, number on the texting test list) > rules. AI replies are rejected unless they cite given ids, carry no number absent from the cited knowledge, and have no medicine wording; rejections fall back to the rules and log `ai_fallback` with a code.
- Live check: OpenRouter answered 401 with the key stored in the OLD `ai_config` (that key is invalid). The owner will add a key in Admin > AI assistant. Approved knowledge is still 0. The AI is OFF until the owner switches it on.

**2026-10-31 UTC, session 3 (latest): automatic replies for the test number, reply syncing, corrections, knowledge from the calls**
- Built and deployed: reply syncing (`telus-send-worker --inbound` hands incoming texts to `<bridge>/send/inbound/`; the app folds them into conversations every 5 s via `app/services/inbound.py`, STOP unsubscribes), automatic replies (`app/services/autoreply.py`: test numbers only even in live mode, assistant switch on for the person, signed "(Walnut Grove automated assistant)", at most 6 an hour per person, same rules as Suggest reply), "Correct this" on assistant texts (`POST /api/messages/{id}/correct`: owner corrections are approved at once, others wait), one-by-one review of pending knowledge, light word normalisation in matching. Tests: 354 backend, 284 pipeline.
- Live settings: texting test-numbers-only, automatic replies ON, assistant ON for the test contact. Knowledge: 22 pending entries (13 new ones written from staff answers on the recorded calls plus the answer sheet, two of those rewritten with what the calls show) and 6 pending fees. Nothing is approved yet, so every automatic reply is currently "a team member will get back to you" plus a task. The loader script was not committed (it carries clinic facts heard on calls).
- Added "Animal Emergency Clinic of the Fraser Valley" as an UNCONFIRMED hospital (staff refer to it on calls). It is never named to clients until the owner confirms it with phone and address in After-hours setup.
- **Owner action still needed:** install both helper services so syncing and sending run by themselves: `cp /opt/walnut-desk/deploy/systemd/walnut-telus-send.* /opt/walnut-desk/deploy/systemd/walnut-telus-inbound.* /etc/systemd/system/ && systemctl daemon-reload && systemctl enable --now walnut-telus-send.path walnut-telus-inbound.timer`. Until then the assistant runs them by hand.

**2026-10-31 UTC, session 3 (latest): real texting through TELUS works in test mode**
- Built a separate send helper: `pipeline/telus/send_client.py` (POST allowed to exactly the SMS and RingOut paths), `pipeline/telus/send_worker.py`, CLI `telus-send-setup`, `telus-send-check`, `telus-send-worker`; separate credentials file `/srv/walnut-private/telus/credentials-send.env`. App: `app/services/sms_mode.py` (modes practice / test_numbers_only / live; live needs the typed phrase), `send_to_contact` queues real texts through `<bridge>/send/requests`, delivery states queued/sent/failed (migration 0008), Admin > TELUS > Texting panel. Campaigns never send for real. The helper re-checks mode and allowlist, rate-limits 30 an hour, never retries a timed-out send, logs no number or text.
- Live: texting is set to **test numbers only** with the owner's test number. A test contact "Test line (owner phone)" was created for it (delete when done). One real test text was sent from the app at 07:33 UTC; TELUS accepted it (status Queued).
- **Not done, owner action needed:** (1) install the helper service: `cp /opt/walnut-desk/deploy/systemd/walnut-telus-send.* /etc/systemd/system/ && systemctl daemon-reload && systemctl enable --now walnut-telus-send.path` (blocked for the assistant as persistence). Until then texts stay "Sending" and turn "Not sent: the sending helper did not answer" after 5 minutes. (2) The TELUS app now has write permissions, so the read-only helper refuses it: playback, call sync and live Brief numbers fail until a separate read-only TELUS app is created and stored with `telus-setup --force`. (3) RingOut permission is missing on the TELUS app, so calls cannot be started from the app; the app button for it is not built. (4) Replies to texts are not synced into the app yet. (5) The owner's test number was briefly committed in tests (`4459901`) and replaced in `4ef02c7`; it remains in that commit's history unless history is rewritten with the owner's approval.

**2026-10-31 UTC, session 3 (latest): AI assistant "suggest a reply" (Stage A) and tel: calling; real SMS and in-app calling NOT built**
- Owner asked to activate the AI assistant with SMS replies and calling from the app. Built the safe part: `POST /api/contacts/{id}/suggest-reply` (`app/assistant/replies.py`, `app/api/assist.py`), a **Suggest reply** button in the composer, per-contact AI switch respected. Order of rules: emergency check first (clinic emergency rules, fixed text, hospital named only if confirmed, high-priority task); medical or dose questions never answered (holding reply plus task for the vet team); otherwise word-for-word from APPROVED knowledge and approved fees only; no match means holding reply plus a "missing answer" task. No outside AI service sees client text (the OpenRouter key exists but all three AI switches stay off). A person always reads and presses Send; nothing is sent automatically. 12 tests in `tests/test_assistant_replies.py`.
- `app/seed/answer_sheet.py --load` loads the answer sheet as PENDING knowledge and fees (caveat in the source fields, never in client text). The assistant knows nothing until the owner approves entries in Knowledge & Fees.
- Call button on a contact is now a `tel:` link when the contact has a phone (opens the device or softphone). Imported contacts have no phone numbers yet.
- **Not built, and why:** (1) real SMS sending and (2) click-to-call or a softphone inside the app both need a TELUS login with write scopes (SMS, RingOut or VoIP). The current login is deliberately read-only and the worker refuses write scopes. Needs a second TELUS app and credential, a separate send worker, per-send consent and quiet-hour checks, an outbox status beyond `would_send`, and the CASL review in `docs/PLAN_CLINIC_CRM.md` section 2. (3) Texting is also impossible today because no contact has a phone number or consent. (4) Autonomous AI replies (Stages B and C) need a fresh sealed emergency evaluation and owner sign-off (plan section 6).

**2026-10-31 UTC, session 3 (latest): TELUS comparison, direction fix and play button built, not yet deployed**
- Pulled the TELUS call log back to July 25 (read-only, 4,255 calls) and compared it with the owner's transcript export by start time: 3,529 of 3,637 recordings matched (97%), 108 did not (Jul 5, Aug 39, Sep 24, Oct 40), 48 recorded TELUS calls have no transcript. New counts-only command `python -m pipeline telus-match --csv FILE [--export PATH]` (docs/TELUS.md).
- Built: TELUS direction and result applied to the Phone log (`app_import --telus-meta`), `phone_calls.recording_id` (migration 0006), play button on the Phone page (owners only): `app/api/phone_audio.py`, worker mode `play` in `pipeline/telus/worker.py`, `frontend/src/views/Phone.tsx`. Tests: 314 backend, 243 pipeline, browser-checked locally with a simulated worker.
- **To deploy on the live server (in this order):** (1) `cd /opt/walnut-desk/frontend && npm run build`; (2) `systemctl restart walnut-desk` (migration 0006); (3) as the pipeline user `python -m pipeline telus-match --csv <the export> --export /var/lib/walnut-desk-telus/call_meta.json`; (4) as `walnut-app` `PYTHONPATH=backend .venv/bin/python -m pipeline.app_import --csv <the export> --telus-meta /var/lib/walnut-desk-telus/call_meta.json --no-contacts`, then delete `call_meta.json` and the temporary CSV copies; (5) an admin switches on **Admin > TELUS > Allow downloading call recordings** (default Off). The file-name number was verified to be the TELUS recording id (2026-10-31).
- Owner wanted no server storage used by playback: a recording fetched only to play is deleted from the private store right after the 15-minute playback copy is made (replays re-download, up to a minute). Bulk downloads are kept as before. A redirect to TELUS was rejected (it would expose a token).
- Play button went live and first failed silently: the worker's `chmod 2770` is forbidden by `RestrictSUIDSGID=true` in `walnut-telus.service`. Fixed (no setgid; group set explicitly with chown), the page now shows progress (asking, downloading, seconds counter) and fails fast, and one real recording was fetched end to end and read by the web app user, then deleted. Lesson: after changing worker code that touches the bridge, test as `walnut-pipeline` under the real unit, not only in unit tests.
- Open: the 108 unmatched recordings are unexplained; the play button needs TELUS recording downloads enabled and has not been tried against real TELUS.

**2026-10-31 UTC, session 3 (latest): real-data mode and contacts from transcripts built, not yet run on live**
- Owner asked for no mock data on the live site and contacts built from the transcripts. Built `app/seed/real_mode.py` (purge all demo rows, block reset), `pipeline/people.py` and `pipeline/app_contacts.py` (rule-based name and pet extraction, contacts with no phone or consent), wired into `pipeline/app_import.py` (contacts on by default, `--no-contacts` to skip). 302 backend tests pass. Details and limits: `docs/DECISIONS.md` (2026-10-31, second entry).
- Trial on a scratch database with the owner's real file: 2,629 calls imported, 171 contacts (137 inquiry, 34 patient), 212 calls linked, 46 pets, 0 demo rows left, staff accounts kept. Only counts were printed.
- **Not run on live** (production write). Order matters: (1) `systemctl restart walnut-desk` so migration 0005 and the new code are live, (2) purge: `cd /opt/walnut-desk/backend && sudo -u walnut-app bash -c 'set -a; . /etc/walnut-desk/app.env; set +a; ../.venv/bin/python -m app.seed.real_mode --purge --dry-run'` then without `--dry-run`, (3) import with contacts: the command in the entry above, without `--dry-run`. Then shred the temporary CSV.
- Known gaps: the Schedule page will be empty with no screen to add shifts; Home/Calendar/Tasks start empty; the Brief's example "missed calls" numbers go away; contact accuracy is unmeasured and staff must review them; a contact has no phone so it cannot be texted or matched to TELUS calls yet.

**2026-10-31 UTC, session 3 (later): real call import built**
- Owner asked to "add existing data" and chose to load full transcripts into the app. Built `pipeline/app_import.py` (migration 0005 adds `phone_calls.external_ref`, source `import`, direction `unknown`), tests (292 backend tests pass), UI handles unknown direction. Details and limits in `docs/DECISIONS.md` (2026-10-31).
- Dry run on the owner's file against a scratch database: 3,637 rows, 2,629 would be imported, 973 skipped as private or uncertain, 35 as staff or personal, 0 bad rows. Only counts were printed.
- **Not yet run on the live database** (production write; run by the owner). Command and removal in the Phone import section below. After it runs: the Phone page shows real calls (no caller numbers, so no contact matching), demo calls are gone, and "Reset demo data" keeps the real calls and does not re-add demo ones.
- Phone import command (run on the server): `cd /opt/walnut-desk && cp /path/to/all_transcripts.csv /tmp/calls.csv && chmod 644 /tmp/calls.csv && sudo -u walnut-app bash -c 'set -a; . /etc/walnut-desk/app.env; set +a; PYTHONPATH=backend .venv/bin/python -m pipeline.app_import --csv /tmp/calls.csv --dry-run'` then the same without `--dry-run`, then `shred -u /tmp/calls.csv`. Migration 0005 must be applied first (`systemctl restart walnut-desk`).

**2026-10-31 UTC, session 3: CRM phases 3 to 5, 7 and 8 built; call analysis; deploy blocked**
- Took over from the uncommitted CRM work found on the server (phases 1 and 2, TELUS live sync). Tests and build passed (261), leak scan clean, committed in two logical commits and pushed (`75c3049`, `25595fd`).
- Built backend (migration 0004, models `models/clinic.py`, services `knowledge.py` and `campaigns.py`, APIs `knowledge.py`, `phone.py`, `campaigns.py`, `reports.py`, seed `seed/clinic_seed.py`, 29 tests in `tests/test_clinic.py`) and frontend (`views/CalendarPage.tsx`, `Knowledge.tsx`, `Phone.tsx`, `Campaigns.tsx`, `Reports.tsx`, `crm2.css`). Nav gained Calendar and an Operations group.
- Design choices worth knowing: only the owner approves knowledge, fees and campaigns (`knowledge.decide`, `campaigns.approve`; open question 6 in the plan, change in `permissions.py`); a pending entry with the same title as an approved one becomes a replacement and shows a before/after diff; campaigns reuse the Contacts filters and the single `send_to_contact` path, so consent is enforced in one place (promo needs full consent, care accepts care-only, unsubscribed never); runs are blocked 9 pm to 8 am and unless approved; transcripts are owner-only and every open is written to `access_log`; erasing a contact now also clears campaign rows and blanks the phone log rows for them.
- Analysed the owner's uploaded transcript export (3,637 calls) outside the repo. Aggregate findings only in `docs/CALL_ANALYSIS.md`. Key finding: the export's `number` and `direction` columns cannot identify callers, so call-to-contact matching must come from TELUS metadata. No transcript text, names or numbers were printed or committed.
- Fixed a pre-existing sideways overflow of the top bar on phones (all CRM pages were about 60 px too wide).
- Deploy was blocked for the assistant by the permission system, so the owner ran the build, restart and seed by hand; it is now live.
- Known limits: Phone, Calendar events and campaigns all show invented demo data; the assistant does not yet read Knowledge (phase 6); `propose()` exists but nothing calls it; the Guest Card "Services" tab still shows visit types, not the new Fees; Calendar does not scroll to the current hour.
- Next session should do first: check the new screens on the live URL as each role, then show Dr. Garg the Knowledge pending queue and ask who approves knowledge and fees, then phase 6 planning.

**2026-10-28 22:27 UTC, session 2: Feature 1 started**
- Owner approved a one-feature-at-a-time plan (order above) and the push of this journal (`7483b4d`).
- Plan for feature 1: a fresh subagent writes 100 new scenarios from the written scope only (no access to the engine, keyword list or the earlier scenarios), 30 of them sealed. Run on a copy of the live DB, report the first-run numbers before any fix, then fix emergency misses and report the later numbers as no longer independent.
- Replaced one real call reference that appeared as an example in section 3.1 with a placeholder format.

**2026-10-28, session 1: Journal started**
- Added section 11 (protocol, current state, work table, journal) and corrected the stale "deployed commit" line in section 4.
- Confirmed live URL https://77-42-69-194.sslip.io answers `/healthz` with 200; both services active; repo clean at `2fc9119`.
- Note for next session: this file lives in a public repo; the edit is local and uncommitted until the owner approves a commit and push.
- Next: wait for the owner's first task for this session, then claim it in 11.3.
