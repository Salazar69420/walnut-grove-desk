# Server notes

Facts about the host that Walnut Grove Desk runs on. Recorded during setup.

## Host

- Type: Hetzner KVM virtual machine, hostname `Veto`
- OS: Ubuntu 26.04 LTS
- Kernel: 7.0.0-29-generic
- Not a container: no `/.dockerenv`, cgroup is `0::/init.scope`
- systemd is PID 1 and running
- Running as root. `sudo` is not needed.

## Resources

- CPU: 2 vCPU
- RAM: 3.7 GiB
- Swap: 2 GiB swapfile at `/swapfile`, added during setup
- Disk: 38 GB total, about 34 GB free

## Network

- Public IPv4: `77.42.69.194`
- Public IPv6: `2a01:4f9:c015:2c41::1`
- Outbound HTTPS works to: PyPI, the npm registry, GitHub, ngrok, DuckDNS, Cloudflare, OpenRouter, NodeSource, and Caddy's servers.
- Inbound ports 80 and 443 are reachable from the internet. Evidence:
  - A Let's Encrypt certificate for `77-42-69-194.sslip.io` was issued via the tls-alpn-01 challenge.
  - check-host.net nodes in Switzerland, Hong Kong and Singapore returned HTTP 200.

## Listening ports and firewall

- Before setup, only these were listening: sshd on port 22, and systemd-resolved on 127.0.0.53.
- ufw was inactive. It is now enabled with default deny for incoming and allow for 22, 80 and 443.

## Installed software

| Software | Version | Source |
|---|---|---|
| Caddy | v2.11.7 | Official Cloudsmith apt repository |
| Node.js | v22.23.4 | NodeSource (Node 22 line) |
| npm | 10.9.9 | Bundled with Node |
| uv | 0.9.5 | astral.sh installer |
| Python 3.12 | 3.12.12 | Managed by uv, in `/opt/uv-python/cpython-3.12.12-linux-x86_64-gnu/` |
| System Python | 3.14.4 | Ubuntu package (`python3`) |
| sqlite3 | 3.46.1 | Ubuntu package |
| git | 2.53.0 | Ubuntu package |
| ufw | 0.36.2 | Ubuntu package |
| Playwright (Python) | 1.49.1 | pip package in the venv at `/opt/walnut-tools/.venv` |
| Chromium (for Playwright) | 131.0.6778.33 (build v1148) | Downloaded by `playwright install --with-deps chromium` |

Notes:

- The system Python is 3.14. The app runs on 3.12 for wheel compatibility, so it uses the uv-managed 3.12 build. Use `/opt/uv-python/cpython-3.12.12-linux-x86_64-gnu/bin/python3.12` for the app.
- Playwright was not preinstalled, despite what the environment notes said. It was installed into a separate tools venv at `/opt/walnut-tools/.venv` (Python 3.12) so it stays apart from the app.
