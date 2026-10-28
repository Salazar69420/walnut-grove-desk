# Operations

Public URL: https://77-42-69-194.sslip.io (also in `/var/lib/walnut-desk/public_url.txt`). It stays the same as long as the server keeps IP 77.42.69.194. sslip.io turns the name into the IP, and Caddy renews the Let's Encrypt certificate by itself.

## Everyday commands (as root)

| Task | Command |
|---|---|
| Health check | `walnut-health` |
| Restart the app | `systemctl restart walnut-desk` |
| Restart the web server | `systemctl restart caddy` |
| App logs | `journalctl -u walnut-desk -f` |
| Run a backup now | `systemctl start walnut-backup` (daily at 10:17 UTC, kept 14 days, in `/var/lib/walnut-desk/backups/`) |
| Restore a backup | `walnut-restore /var/lib/walnut-desk/backups/app-YYYYMMDD-HHMMSS.db.gz` (stops the app, keeps the old DB as `app.db.before-restore.*`, checks integrity, starts the app) |
| Run suggestion rules now | `systemctl start walnut-rules` (every 15 minutes anyway) |
| Reset the demo cards | Admin > Settings > Reset demo data, or `cd /opt/walnut-desk/backend && sudo -u walnut-app env $(cat /etc/walnut-desk/app.env) ../.venv/bin/python -m app.seed --reset-demo` |

## Users

- Add a user: Admin > Users > Add user. The page shows a one-time password; the person must change it at first sign-in.
- Reset a password: Admin > Users > Reset password (shows a new one-time password).
- Remove access: Admin > Users > Deactivate. Their sessions stop working at once.

## Settings

- Prototype banner: Admin > Settings > "Show the \"Prototype, example data\" label", set it to Off, then Save.
- Clinic name, rule thresholds, AI provider (DeepSeek key), Retell hook: Admin > Settings.

## Updating the app

```
cd /opt/walnut-desk
git pull                                   # or copy new code in
uv pip install --python .venv/bin/python -r backend/requirements.txt
(cd frontend && npm ci && npm run build)
cd backend && WALNUT_ENV=test ../.venv/bin/pytest -q && cd ..
systemctl restart walnut-desk              # runs database migrations on start
walnut-health
```

## If the URL stops working

1. `walnut-health` shows which part is down.
2. If the server's IP changed, the sslip.io name changes with it: edit `/etc/caddy/Caddyfile` and `/var/lib/walnut-desk/public_url.txt` to the new `A-B-C-D.sslip.io`, then `systemctl reload caddy`.
3. Fallback if ports 80 and 443 are ever blocked: install `cloudflared` and run `cloudflared tunnel --url http://localhost:8000` as a service. Its URL changes on every restart; write it to `public_url.txt`.

## Restart behaviour (tested 2026-10-28)

- `kill -9` of the app process: systemd restarted it within about 3 seconds (`NRestarts=1`), and the public health check returned ok.
- `systemctl restart caddy`: the public URL was back within seconds.
- All units (`walnut-desk`, `caddy`, `walnut-backup.timer`, `walnut-rules.timer`, `ufw`) are enabled at boot. A full reboot was not run, because the build agent runs on this same VM; run `reboot` and then `walnut-health` to confirm.

## Real call data

See `docs/SECURITY.md` and the handoff. One command removes it all: `sudo bash /srv/walnut-private/WIPE.sh` (try `DRY_RUN=1` first).
