# Progress

| Milestone | Status | Evidence |
|---|---|---|
| M1 Skeleton and plan | Done | Server notes, plan, repo, Alembic, `/healthz`, systemd, Caddy and Let's Encrypt for 77-42-69-194.sslip.io. check-host.net nodes in CH, HK and SG got HTTP 200 |
| M2 Accounts and roles | Done | argon2id, sessions, CSRF, lockout, forced change, admin users, append-only events. Permission walker test over every route |
| M3 Core record and tools | Done | Callbacks, results, drafts, shifts, suggestions, events, SSE. E2E: desk claim, done, approve; nurse advance; two contexts live within 5 s |
| M4 Overnight and after-hours | Done | State machine, text and call modes, two-layer emergency (built-in rules are a floor), booking confirm then "Done in ImproMed", setup page. Engine tests: every keyword and YAML phrase routes to emergency, adversarial texts get no advice |
| M5 Brief and assistant | Done | Metrics in SQL, four rules with real effects, 14 assistant handlers from queries, AI wording with number check (test rejects a made-up figure) |
| M6 Retell, import, export | Done | Endpoints off by default and dry run, `docs/RETELL.md` curls run against a dev server, import for 6 kinds, CSV export, PimsAdapter with samples |
| M6b Real call analytics | Done, with honest gaps | Private user and dir, app user read denied, audit, 112-call gold set, rules labeler (private recall 0.57, below target, see LABELER_EVAL.md), 949 calls' text deleted, CSV shredded, 227 aggregate rows imported, drafts, 181-scenario eval (emergency recall 1.0, 0 advice), WIPE.sh tested on a copy, leak scan clean |
| M7 Hardening and ops | Done | Backups with retention and restore check, rules timer, journald cap, health script, pip-audit clean, kill -9 restart tested. Reboot not run (see OPERATIONS.md) |
| M8 Verification and handoff | Done | 136 backend tests, 30 pipeline tests, 15 e2e tests against the public URL, screenshots in `docs/screenshots/app/` next to `docs/screenshots/reference/`, handoff in `/root/HANDOFF.md` |
