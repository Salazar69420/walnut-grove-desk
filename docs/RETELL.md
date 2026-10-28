# Retell voice agent hook

The hook lets a Retell voice agent use the after-hours assistant's rules by calling five small functions, and send a webhook when a call ends. It is **off by default** (every route returns 404), runs in **dry run** by default (rows are marked as test), and never places a call or sends a text. All wording the caller hears comes from `backend/app/aftercare/templates.py`; the model on the Retell side only says what the functions return in `say`.

Every example below was run against a local dev server at `http://127.0.0.1:8000` (seeded demo data). IDs, dates and the secret will differ on your machine.

## Security, read this first

1. **Shared secret (primary protection).** Every call must send `X-Walnut-Secret: <secret>`. The secret is 64 characters, shown once when generated, and stored only as a SHA-256 hash. Compared in constant time. Missing or wrong: `401 {"detail":"Unauthorized"}`.
2. **Retell signature (second check, only when a Retell API key is stored).** If an API key is saved in Admin settings, the `x-retell-signature` header is also checked (`backend/app/retell/verify.py`).
   **This must be checked against the current Retell documentation before going live.** What is implemented is my best understanding of the scheme (Retell's SDK exposes it as `Retell.verify(body, api_key, signature)`): `HMAC-SHA256(key = Retell API key, message = raw body + timestamp)`, sent as `v=<timestamp in ms>,d=<hex digest>`, rejected if older than 5 minutes; the older plain form `hex(HMAC-SHA256(api_key, body))` is also accepted. I could not verify this against Retell's live documentation from this build environment. If the check does not match what Retell sends, do not store a Retell API key (the shared secret still protects the hook) and fix `verify.py` only.
3. Retell routes are exempt from the session cookie and CSRF token (they use the secret instead). They return 404 while disabled, so their existence is not revealed.
4. Keep the hook in `dry_run` until the clinic has tested it. Dry run only marks created rows `is_test`; nothing is ever sent either way.

## Enable it (admin session cookie plus CSRF token)

Sign in as an admin, keep the cookie, and read the CSRF token from `/api/auth/me`:

```
B=http://127.0.0.1:8000
curl -s -c jar -H 'Content-Type: application/json' -d '{"username":"garg","password":"<password>"}' $B/api/auth/login > /dev/null
CS=$(curl -s -b jar $B/api/auth/me | python3 -c "import sys,json;print(json.load(sys.stdin)['csrf_token'])")
```

Generate the secret and turn the hook on (the secret is returned once, in this response only):

```
curl -s -b jar -X PUT $B/api/admin/settings/retell -H "X-CSRF-Token: $CS" -H 'Content-Type: application/json' \
  -d '{"regenerate_secret":true,"enabled":true,"dry_run":true}'
```
```
{"enabled":true,"dry_run":true,"secret_set":true,"api_key_set":false,"api_key_last4":null,"secret":"6QWLmRwEFzYOEdPlJsSjrIwaPyJqju47_d8KbsxpjksJiUzxiRIGrSQcRhvu69nF"}
```

Regenerating (`"regenerate_secret":true` again) invalidates the old secret at once. `GET /api/admin/settings/retell` returns the same shape without `secret`. `{"retell_api_key":"..."}` stores the Retell key encrypted (never returned, only the last 4 characters); `""` clears it. `{"enabled":false}` turns the hook off again (404).

Set shell variables for the examples:

```
S=<the secret from above>
H="-H X-Walnut-Secret:$S -H Content-Type:application/json"
```

Wrong or missing secret:

```
curl -s -i -X POST $B/api/retell/functions/check_emergency -H 'X-Walnut-Secret: nope' -H 'Content-Type: application/json' -d '{"args":{"text":"hi"}}'
```
```
HTTP/1.1 401 Unauthorized
{"detail":"Unauthorized"}
```

## Request shape

`POST /api/retell/functions/{name}` where `name` is one of `check_emergency`, `get_open_slots`, `create_appointment_request`, `create_callback`, `get_emergency_hospital`. Body (Retell custom function style; the plain internal form `{"call_id":"...","name":"...","args":{...}}` also works):

```
{"call": {"call_id": "call_demo_1"}, "name": "<function>", "args": { ... }}
```

The function name in the URL wins. Response: `{"result": {...}}`, small JSON the agent can read; the agent should speak `say` word for word. Bad arguments: `400 {"detail":"Bad arguments: ..."}`. Unknown function: 404.

## Functions

### check_emergency `{text}`

```
curl -s -X POST $B/api/retell/functions/check_emergency $H -d '{"call":{"call_id":"call_demo_1"},"name":"check_emergency","args":{"text":"my dog is gasping and has blue gums"}}'
```
```
{"result":{"emergency":true,"matched":["gasping","blue gums"],"say":"That sounds like it needs a vet now. Please contact the nearest emergency veterinarian right away. The clinic will follow up with you in the morning."}}
```
```
curl -s -X POST $B/api/retell/functions/check_emergency $H -d '{"call":{"call_id":"call_demo_1"},"name":"check_emergency","args":{"text":"I would like to book a check-up"}}'
```
```
{"result":{"emergency":false,"matched":[],"say":""}}
```

Uses layer 1 (the emergency word list) and, only when an AI provider is saved and "Second emergency check" is on, layer 2. Unsure or a model error counts as an emergency. `matched` shows `pattern match` for regex entries. The agent should call this for every caller sentence that might be about a sick pet.

### get_emergency_hospital `{}`

```
curl -s -X POST $B/api/retell/functions/get_emergency_hospital $H -d '{"call":{"call_id":"call_demo_1"},"args":{}}'
```
```
{"result":{"confirmed":false,"say":"That sounds like it needs a vet now. Please contact the nearest emergency veterinarian right away. The clinic will follow up with you in the morning."}}
```

Only a hospital the owner marked confirmed (and not a placeholder) is ever named. Phone and address are spoken only when filled in. Once confirmed, `confirmed` is true and `say` names it.

### get_open_slots `{visit_type?, after?}`

```
curl -s -X POST $B/api/retell/functions/get_open_slots $H -d '{"call":{"call_id":"call_demo_1"},"args":{"visit_type":"Vaccines"}}'
```
```
{"result":{"slots":[{"id":1,"label":"Wed Oct 28, 9:30 am"},{"id":5,"label":"Thu Oct 29, 9:30 am"},{"id":9,"label":"Fri Oct 30, 9:30 am"}],"bookable":true}}
```

At most 3, future and untaken only, spread over different days when possible. `after` is an ISO date or time. A visit type switched to "front desk only" (or the "take appointment requests" toggle off) returns `{"slots":[],"bookable":false}`. Unknown visit type: 400.

### create_appointment_request `{client_name, pet_name, visit_type, slot_id, phone_last4?}`

```
curl -s -X POST $B/api/retell/functions/create_appointment_request $H -d '{"call":{"call_id":"call_demo_1"},"args":{"client_name":"Sam Example","pet_name":"Juno","visit_type":"Vaccines","slot_id":5,"phone_last4":"0199"}}'
```
```
{"result":{"ok":true,"say":"I have asked for Thu Oct 29, 9:30 am for Juno. The front desk will confirm after 8 am.","conversation_id":6,"kind":"booking"}}
```

Creates a conversation (source `retell`, channel Call) plus an appointment request that appears in Overnight for staff to confirm. A visit type that is front desk only becomes a callback (`"kind":"callback"`). A slot that is past, taken or unknown returns `400`:

```
{"detail":"That opening is not available. Ask for the open slots again."}
```

If the pet or visit text contains an emergency word the reply is the emergency wording instead (`"emergency":true`) and an emergency routing is recorded.

### create_callback `{client_name, pet_name?, reason}`

```
curl -s -X POST $B/api/retell/functions/create_callback $H -d '{"call":{"call_id":"call_demo_1"},"args":{"client_name":"Sam Example","pet_name":"Juno","reason":"Would like a call about a food order"}}'
```
```
{"result":{"ok":true,"say":"Thanks. I have passed that to the front desk and they will call you after 8 am.","conversation_id":7}}
```

Creates an Overnight conversation and a callback card ("Taken overnight by the assistant."). If the "take messages" toggle is off the caller is told to call back after 8 am, and the callback is still logged. A reason that contains an emergency word is routed as an emergency:

```
curl -s -X POST $B/api/retell/functions/create_callback $H -d '{"call":{"call_id":"call_demo_2"},"args":{"client_name":"Sam Example","reason":"She is having a seizure"}}'
```
```
{"result":{"ok":true,"emergency":true,"say":"That sounds like it needs a vet now. Please contact the nearest emergency veterinarian right away. The clinic will follow up with you in the morning."}}
```

## Webhook: `POST /api/retell/webhook`

```
curl -s -X POST $B/api/retell/webhook $H -d '{"event":"call_ended","call":{"call_id":"call_demo_9","transcript":"Agent: Hi, this is the automated assistant for Walnut Grove Animal Hospital.\nUser: I need a refill for Luna.\nAgent: Thanks, I will pass that on.","call_analysis":{"call_summary":"Caller wants a refill for Luna."},"start_timestamp":1793000000000,"end_timestamp":1793000060000}}'
```
```
{"ok":true,"conversation_id":9}
```

`call_ended` stores an Overnight conversation: channel Call, source `retell`, summary from `call_analysis.call_summary`, messages parsed from the transcript lines `Agent:` (assistant) and `User:` (client). If the caller's lines contain an emergency word it is stored as an emergency routing, otherwise as a callback-type item. Sending the same `call_id` again is ignored (`204`). Any other event returns:

```
curl -s -i -X POST $B/api/retell/webhook $H -d '{"event":"call_started","call":{"call_id":"call_demo_9"}}'
```
```
HTTP/1.1 204 No Content
```

## Signature example (only when a Retell API key is stored)

```
curl -s -b jar -X PUT $B/api/admin/settings/retell -H "X-CSRF-Token: $CS" -H 'Content-Type: application/json' -d '{"retell_api_key":"key_example_1234567890"}'
```
```
{"enabled":true,"dry_run":true,"secret_set":true,"api_key_set":true,"api_key_last4":"7890"}
```

Without the header the call is refused:

```
{"detail":"Unauthorized"}
```

With a signature made as described above (this one was generated with `openssl dgst -sha256 -hmac`):

```
BODY='{"call":{"call_id":"call_demo_1"},"args":{"text":"hello"}}'
TS=$(python3 -c 'import time;print(int(time.time()*1000))')
D=$(printf '%s' "$BODY$TS" | openssl dgst -sha256 -hmac key_example_1234567890 | awk '{print $2}')
curl -s -X POST $B/api/retell/functions/check_emergency $H -H "x-retell-signature: v=$TS,d=$D" -d "$BODY"
```
```
{"result":{"emergency":false,"matched":[],"say":""}}
```

## Turning it off

```
curl -s -b jar -X PUT $B/api/admin/settings/retell -H "X-CSRF-Token: $CS" -H 'Content-Type: application/json' -d '{"enabled":false}'
```
```
{"enabled":false,"dry_run":true,"secret_set":true,"api_key_set":false,"api_key_last4":null}
```

Afterwards every `/api/retell/*` call returns `404 {"detail":"Not found"}`.
