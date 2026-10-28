# Decisions

Each entry gives what was chosen and why.

## 1. Hosting on sslip.io with Caddy

- **Chose:** Serve the app at `https://77-42-69-194.sslip.io` with Caddy as the TLS terminator and reverse proxy. Fallbacks, in order: nip.io, then a Cloudflare quick tunnel.
- **Why:** The VM has a public IP with ports 80 and 443 reachable from the internet, and no DuckDNS token was provided. sslip.io gives a hostname that resolves to the IP with no account or token. Caddy obtains the Let's Encrypt certificate automatically. The URL stays stable as long as the IP stays the same. nip.io and the Cloudflare quick tunnel are fallbacks if sslip.io stops working.

## 2. Python 3.12 via uv, not the system Python

- **Chose:** Run the app on Python 3.12 that uv manages in `/opt/uv-python`.
- **Why:** The system Python is 3.14, and the app's dependencies ship wheels for 3.12. Using uv keeps the interpreter pinned and separate from the OS.

## 3. SQLite in WAL mode, a single uvicorn worker, in-process SSE

- **Chose:** One SQLite database in WAL mode, one uvicorn worker, and server-sent events generated inside that process.
- **Why:** The app has one VM with 2 vCPUs and 3.7 GiB of RAM, and the clinic is a single small writer. One worker means one writer to the database and no cross-process state to sync. WAL lets readers keep working while a write is in progress, which suits a live feed. In-process SSE avoids running a separate message broker.

## 4. One events table for the shared record, the live feed and the audit log

- **Chose:** A single append-only `events` table is the shared clinic record. The live feed reads from it, and it doubles as the audit log.
- **Why:** Staff, the assistant and the audit trail all need the same facts. Keeping one table means the feed can never show something the record does not hold, and the audit log cannot drift from what the screens show.

## 5. Real sign-in replaces the role switcher; Reset demo moves to Admin

- **Chose:** The reference's "Viewing as" role switcher is replaced with real sign-in for each staff account. The "Reset demo" control moves to the Admin area.
- **Why:** A role switcher lets anyone see any role's data, which is not acceptable for clinic records. Real accounts give each person their own permissions and an audit trail. Reset demo is a data-wiping action, so it belongs behind admin rights rather than in front of every user.

## 6. Fonts self-hosted, so the Content Security Policy stays `self` only

- **Chose:** Serve the fonts from the app itself rather than loading them from Google Fonts.
- **Why:** The reference page loads Figtree and IBM Plex Mono from Google. Self-hosting lets the CSP allow only `self`, which blocks third-party scripts and styles, and it removes a request to a third party that sees visitors' IP addresses.

## 7. AI provider is DeepSeek, OpenRouter also supported, all three AI toggles off by default

- **Chose:** DeepSeek is the default AI provider. The owner adds the API key in Admin settings. OpenRouter is also supported as an option. All three AI toggles are off until someone turns them on.
- **Why:** The owner chose DeepSeek and will supply the key, so the app never needs a key in the code or in the repo. Starting with every AI feature off means nothing is sent to a model until the clinic decides to enable it.

## 8. The after-hours assistant speaks only from templates

- **Chose:** The after-hours assistant sends only text from fixed templates that the clinic wrote. A model never writes text that goes to a client.
- **Why:** A template cannot invent a diagnosis, reassure someone that a pet is fine, or give medical advice. The assistant can book appointments, take messages and route emergencies, and every client-facing sentence is one the clinic approved.

## 9. The results call script uses a placeholder, not a finding

- **Chose:** The reference script that says a level "is slightly high" is replaced with a placeholder for whatever the doctor wants said.
- **Why:** The app must not invent a clinical finding. Only the doctor knows the result and what should be told to the client, so the app leaves that sentence for the doctor to fill in.

## 10. Public references use a hashed `call_ref`, not raw call IDs

- **Chose:** Anywhere a call is referenced outside the private store, use a hashed `call_ref`.
- **Why:** Raw call IDs embed phone numbers, so showing them would expose client phone numbers in logs, links or screenshots.

## 11. Only the orchestrating model (Opus) reads transcript text

- **Chose:** Only the orchestrating model, Opus, reads transcript text. Subagents never receive it.
- **Why:** Transcripts contain client conversations. Limiting who sees them to one model keeps the number of places that handle client content as small as possible.

## 12. Private CSV upload copy was shredded only after it was moved and checksummed

- **Chose:** The temporary upload copy of the private CSV was shredded, and only after the file had been moved into `/srv/walnut-private` and its sha256 matched.
- **Why:** Client data should live only in the private location. The sha256 match confirms the moved copy is intact before the temporary copy is destroyed, so no data is lost and no copy lingers.

## 13. Reference "missed calls 41" figures stay as labeled example data

- **Chose:** The figures from the reference, including 41 missed calls last week, are kept as example data and labeled as such in the UI.
- **Why:** Real call logs do not exist yet. Showing example numbers as if they were measured would mislead staff, so they stay labeled until the call logger produces real data.
