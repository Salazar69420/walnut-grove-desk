# walnut-desk private call pipeline

Turns the clinic phone system's transcript CSV into a private SQLite database, labels calls, lets a human
build a gold set, purges private conversations, and exports aggregate-only analytics. Every command prints
counts only. Nothing here reads or writes outside `--root` (default `/srv/walnut-private`) except the code
directory itself.

Python 3.12, standard library plus `pydantic`, `pyyaml` (and `httpx` for `label-llm`, `pytest` for tests).
The venv lives in `/opt/walnut-desk/pipeline/.venv` (pinned versions in `requirements.txt`):

```
UV_PYTHON_INSTALL_DIR=/opt/uv-python uv venv --python 3.12 /opt/walnut-desk/pipeline/.venv
uv pip install --python /opt/walnut-desk/pipeline/.venv/bin/python -r /opt/walnut-desk/pipeline/requirements.txt
```

## Running

Always run from `/opt/walnut-desk` as the dedicated user. Shorthand used below:

```
cd /opt/walnut-desk
P="sudo -u walnut-pipeline /opt/walnut-desk/pipeline/.venv/bin/python -m pipeline"
```

Path options (all optional, accepted before or after the command): `--root` (default `/srv/walnut-private`),
`--db` (`<root>/private.db`), `--salt` (`<root>/salt.bin`), `--csv` (`<root>/inbox/all_transcripts.csv`),
`--out-dir` (`<root>/out`), `--gold-dir` (`<root>/gold`). Created files are mode 600, directories 700.

| Step | Command |
|---|---|
| 1 | `$P load` (add `--force` to reload; refuses if `calls` is not empty) |
| 2 | `$P audit [--out PATH.md]` writes `DATA_AUDIT.md` and `DATA_AUDIT.json` (counts only) |
| 3 | `$P label-rules [--rules pipeline/rules.yaml]` stores labeler `rules-v1` |
| 4 | `$P gold-sample -n 100 --seed 1234` then `$P gold-candidates --query "chocolate OR xylitol" --limit 20` |
| 5 | `$P gold-show <call_ref> --chars 700 --offset 0` (digits are shown as `#`) |
| 6 | `$P gold-label <call_ref> '<json>'` validates against the schema, stores in `gold` |
| 7 | `$P evaluate [--labeler rules-v1]` writes `EVALUATION_<labeler>.md/.json` |
| 8 | `$P purge-private [--conservative] [--shred-csv]` |
| 9 | `$P analytics [--labeler rules-v1] [--out PATH]` writes `<root>/out/analytics.json` |
| 10 | `$P label-llm [--env-file /var/lib/walnut-desk-llm/llm.env] [--limit N] [--concurrency 4]` |
| 11 | `$P verify-no-leak --paths <file-or-dir> ... [--ignore-regex RX] [--list-files]` (exit 1 if anything found) |
| 12 | `$P wipe-script` writes `<root>/WIPE.sh`; run it as root: `sudo bash /srv/walnut-private/WIPE.sh` |

Typical order: `load`, `audit`, `label-rules`, `gold-sample`, hand-label with `gold-show`/`gold-label`,
tune `rules.yaml`, `label-rules`, `evaluate`, then `purge-private --shred-csv`, `analytics`,
`verify-no-leak --paths <root>/out <web export dir>`.

## Data model (private.db)

* `calls(call_id PK, call_ref UNIQUE, ts, date, hour, weekday, ext, number_hash, has_number, seconds,
  language, transcript, plain_text, private_purged)`. `call_id` (the file name without `.mp3`, so it embeds the
  caller number) is the ONLY place a raw number or file name exists; keep this DB private.
* `call_ref = YYYYMMDD-HHMMSS-<ext>-<6 hex of HMAC(salt, call_id)>`, safe to print. `number_hash` is
  `HMAC-SHA256(salt, digits)` (digits only, a leading country code 1 on 11 digit numbers is dropped).
  The salt is `<root>/salt.bin`, created on first load. Deleting it makes hashes and refs unrecomputable.
* `calls_fts(call_ref UNINDEXED, plain_text)` is a regular FTS5 table whose rowid equals `calls.rowid`.
* `labels(call_ref, labeler, json, created_at)` with primary key `(call_ref, labeler)` so several labelers
  can coexist. `gold(call_ref PK, json, created_at)`. `load_meta(key, value)` holds load counters
  (rows read, duplicates skipped, ...).
* Duplicate ids in the CSV keep the row with the longest `plain_text`; the duplicate count is in `load_meta`.
* `direction` in the CSV is ignored (junk). The call time comes from the file name (local America/Vancouver).

## Labels

`pipeline/schema.py` defines the strict `CallLabel` model (extra fields forbidden). `red_flags` and
`followup_text` come from fixed vocabularies in `schema.py` (`RED_FLAG_VOCAB`, `FOLLOWUP_VOCAB`); a label
never contains text copied from a call. "Chosen label" for purge and analytics is: gold if present, else the
selected/latest labeler.

### Tuning the rules

Everything is in `pipeline/rules.yaml`: thresholds, fuzzy clinic-name patterns (`{CLINIC}` expands to the union),
voicemail/outbound/inbound markers, caller-type keyword groups, intent groups and tie-break order, symptom
groups (with ASR misspellings), `soon` phrases, work signals and private markers. Patterns are lowercase
regexes matched against whitespace-collapsed lowercase text; scoring counts distinct patterns that match.
Symptom names must exist in `RED_FLAG_VOCAB` and follow-up names in `FOLLOWUP_VOCAB` (validated on load).
Decision rule: `contains_private_content` is true if caller type is `private_personal`, or a private marker
fires (one strong marker, or `weak_private_min_hits` weak ones outnumbering work signals), or there is no
clinic-work signal at all and the call is longer than `private_min_seconds` and not automated.
Symptom phrases preceded by a negation in the same clause ("no", "not", ...) are ignored.

## Purge

`purge-private` selects calls whose chosen label has `contains_private_content` true (`--conservative` also
purges when any labeler says so; gold always overrides). For those rows it sets `PRAGMA secure_delete=ON`,
overwrites `transcript`/`plain_text` with same-length `x` strings, then NULL, sets `private_purged=1`,
deletes them from `calls_fts`, rebuilds and optimizes the FTS index, runs `VACUUM` and
`PRAGMA wal_checkpoint(TRUNCATE)`. Calls with no label are kept (their count is printed). `--shred-csv`
overwrites the inbox CSV with random bytes, fsyncs and unlinks it. Purged calls keep their metadata, labels
and `call_id`.

## label-llm

Reads `BASE_URL`, `API_KEY`, `MODEL`, `ALLOW_TRANSCRIPT_LABELING` from a `KEY=VALUE` env file. Refuses unless
`ALLOW_TRANSCRIPT_LABELING=1` and a key exist. Only unpurged calls whose `rules-v1` label is not private are
sent (`plain_text` truncated to 6000 chars). Invalid JSON is retried once. Results are stored as labeler
`llm-<MODEL>`; reruns skip calls already labeled. Request bodies are never logged.

## WIPE.sh

`wipe-script` writes a bash script (header comment lists exactly what it deletes). Environment:
`WIPE_ROOT` (default `/srv/walnut-private`), `WIPE_APP_DB` (`/var/lib/walnut-desk/app.db`),
`WIPE_CLAUDE_DIR` (`/root/.claude/projects`), `DRY_RUN=1`. It shreds every file under the root and removes
it, deletes `call_analytics` rows with `source='derived_from_real_calls'` and all `imported_calls` rows with
`secure_delete` plus `VACUUM`, shreds Claude Code `*.jsonl` and `tool-results` files, and removes the
`walnut-pipeline` user when the root is the default. It must run as root.

## Tests

```
cd /opt/walnut-desk && pipeline/.venv/bin/python -m pytest pipeline/tests -q
```

Tests use only the synthetic fixture generated by `pipeline/tests/make_fixture.py` in pytest temp dirs. The
default root is monkeypatched in `conftest.py` so a test can never touch `/srv/walnut-private`.

## Known limitations

* Rules are English-centric; Hindi, Punjabi and French have only a few seed keywords, so other-language calls
  without a match are conservatively flagged private (no clinic-work signal).
* Analytics suppresses cells with n < 5, but totals plus unsuppressed siblings can still allow differencing.
* `shred` cannot guarantee erasure on copy-on-write filesystems, SSDs, snapshots or backups.
* `verify-no-leak` checks a random 300 transcripts' 8-word shingles, so it detects verbatim copying, not
  paraphrase. Its 10+ digit rule also flags long numeric ids; use `--ignore-regex` for known benign ones.
* Purged rows keep `call_id`, which embeds the caller number; delete `private.db` itself (WIPE.sh) when done.
