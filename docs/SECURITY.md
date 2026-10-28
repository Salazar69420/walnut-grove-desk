# Security

## Before any real clinic data goes in

**Do not load real operational data (clients, pets, appointments, lab results, real call logs) until Dr. Garg has reviewed hosting, backups, consent and privacy obligations.** The clinic is in British Columbia, so BC's Personal Information Protection Act (PIPA) applies, along with the CVBC's rules on veterinary records. Items to decide first:

1. **Hosting location.** The server is a Hetzner VM. Where it is located is not recorded here; check the Hetzner console. Decide whether data held outside Canada is acceptable, and tell clients if so.
2. **Backups.** Daily backups are kept on the same machine, unencrypted (mode 600, app user only). Before real data: encrypt them (for example with `age` to a key kept offline) and copy them off the server.
3. **Consent and recording.** Callers hear "This call is being recorded". Using those recordings for analysis and testing needs a stated purpose and a retention period.
4. **AI provider.** DeepSeek processes data on servers outside Canada (China). All three AI switches are off by default. "Allow call transcripts to be sent for labeling" should stay off unless Dr. Garg decides this is acceptable and clients have been told.
5. **Access reviews.** Review the user list monthly in Admin > Users and deactivate leavers the same day.
6. **Audit retention.** The audit log (events table) is append-only and kept forever. Decide a retention period, for example 7 years to match record keeping.
7. **Breach process.** Write down who to call, how to rotate secrets (see below), and the PIPA duty to notify affected people and the Commissioner when there is a real risk of significant harm.

## Threats and what is in place

| Threat | Protection |
|---|---|
| Stolen or guessed password | argon2id hashes, 12+ character passwords, common-password check, forced change at first sign-in, lockout after 5 failures for 15 minutes, 20 attempts per IP per 10 minutes, generic error text |
| Session theft | `__Host-wgd` cookie: Secure, HttpOnly, SameSite=Lax, 12 h idle and 7 day maximum. HTTPS only, HSTS one year |
| Cross-site request forgery | Per-session token required in `X-CSRF-Token` on every change |
| Staff seeing more than their role allows | Server-side permission check on every route. A test walks every API route and checks that desk and nurse get 403 where they should |
| Injection, XSS | SQLAlchemy parameterised queries only, pydantic validation with length limits, React escaping, no raw HTML rendering, CSP `default-src 'self'` with no third-party scripts or fonts, CSV exports neutralise formulas |
| Clickjacking, sniffing | `frame-ancestors 'none'`, `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy: same-origin` |
| Exposed internals | App and Caddy admin bound to 127.0.0.1. ufw allows only 22, 80, 443. No `/docs` or `/openapi.json` in production |
| Server compromise via the app | App runs as `walnut-app` (no shell) under systemd with `ProtectSystem=strict`, `NoNewPrivileges`, and `/srv/walnut-private` made inaccessible |
| Real transcripts leaking | They live only in `/srv/walnut-private` (mode 700, user `walnut-pipeline`). The app user cannot read it, which has been tested. The app only holds aggregate counts. A leak scanner checked the repo, web root, app DB and logs for phone numbers and transcript text, with zero matches |
| Assistant giving medical advice | Client-facing text comes only from fixed templates; a model never writes to clients. Advice and dose requests get a fixed refusal. 0 advice replies in 181 test scenarios |
| Fake hospital details | The assistant names only hospitals the clinic has marked confirmed, and gives a phone or address only when those fields are filled. They ship empty |
| Retell hook abuse | Off by default (404). When on, a 64-character shared secret header is required (stored hashed, constant-time compare) plus an optional signature check. Dry run by default. Nothing ever places a call or sends a text |

## What is not protected

- **No two-factor sign-in.** Add it before real data.
- **Single server.** If the disk fails, data since the last off-site copy is lost, and there are no off-site copies yet.
- **The Retell signature check** in `backend/app/retell/verify.py` is written from memory of Retell's docs and has not been checked against them. The shared secret is the real protection.
- **`shred` is best effort** on SSDs and journaling filesystems, and does nothing about provider snapshots.
- **Root on this VM can read everything**, including the private transcripts. Anyone with the SSH key is fully trusted.
- **Claude Code session logs** under `/root/.claude/projects` contain the transcript snippets the build model read while labeling. `WIPE.sh` removes them.

## Rotating secrets

- App keys: edit `/etc/walnut-desk/app.env` (`WALNUT_SECRET_KEY` signs nothing long-lived, but changing it is harmless). Changing `WALNUT_FERNET_KEY` makes stored AI and Retell keys unreadable, so enter them again in Admin > Settings afterwards. Then run `systemctl restart walnut-desk`.
- Sign everyone out: `sqlite3 /var/lib/walnut-desk/app.db "delete from sessions"`.
- Retell secret: Admin > Settings > Retell > Regenerate secret.

## Self-checks run (2026-10-28)

- `pip-audit` on backend and pipeline requirements: no known vulnerabilities.
- `npm audit --omit=dev`: 0 vulnerabilities in what is shipped. `npm audit` including dev tools reports 2 issues in esbuild and Vite's development server, which never runs in production.
- Backend tests: 136 passed, including the permission walker, CSRF, lockout and session expiry. Pipeline tests: 30 passed.
- End-to-end suite against the public URL: 15 passed.
