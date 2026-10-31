"""Command line entry point: python -m pipeline <command>."""
from __future__ import annotations

import argparse
import os
import sys

from . import config


def _common() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(add_help=False)
    g = p.add_argument_group("paths")
    g.add_argument("--root", default=argparse.SUPPRESS, help=f"data root (default {config.DEFAULT_ROOT})")
    g.add_argument("--db", default=argparse.SUPPRESS, help="default <root>/private.db")
    g.add_argument("--salt", default=argparse.SUPPRESS, help="default <root>/salt.bin")
    g.add_argument("--csv", default=argparse.SUPPRESS, help="default <root>/inbox/all_transcripts.csv")
    g.add_argument("--out-dir", dest="out_dir", default=argparse.SUPPRESS, help="default <root>/out")
    g.add_argument("--gold-dir", dest="gold_dir", default=argparse.SUPPRESS, help="default <root>/gold")
    return p


def build_parser() -> argparse.ArgumentParser:
    common = _common()
    ap = argparse.ArgumentParser(prog="python -m pipeline", parents=[common],
                                 description="Private call-transcript pipeline (counts only output).")
    sub = ap.add_subparsers(dest="command", required=True)

    def add(name, help_):
        return sub.add_parser(name, parents=[common], help=help_)

    s = add("load", "parse the inbox CSV into private.db")
    s.add_argument("--force", action="store_true", help="reload even if calls table is not empty")

    s = add("audit", "counts-only data audit (markdown + JSON)")
    s.add_argument("--out", help="markdown path (default <root>/out/DATA_AUDIT.md); JSON beside it")

    s = add("label-rules", "run the deterministic rules labeler (rules-v1)")
    s.add_argument("--rules", default=config.DEFAULT_RULES, help="rules.yaml path")

    s = add("gold-sample", "stratified seeded sample of call_refs")
    s.add_argument("-n", type=int, default=100)
    s.add_argument("--seed", type=int, default=1234)
    s.add_argument("--min-chars", type=int, default=0, help="only calls with at least this much text")

    s = add("gold-candidates", "call_refs matching an FTS query")
    s.add_argument("--query", required=True)
    s.add_argument("--limit", type=int, default=20)

    s = add("gold-show", "show a masked chunk of one call's text")
    s.add_argument("call_ref")
    s.add_argument("--chars", type=int, default=700)
    s.add_argument("--offset", type=int, default=0)

    s = add("gold-label", "validate and store a gold label")
    s.add_argument("call_ref")
    s.add_argument("json")

    s = add("evaluate", "compare a labeler to gold")
    s.add_argument("--labeler", default="rules-v1")

    s = add("purge-private", "irreversibly purge text of private-flagged calls")
    s.add_argument("--conservative", action="store_true",
                   help="purge if ANY labeler (not just the latest) says private; gold still overrides")
    s.add_argument("--shred-csv", action="store_true", help="overwrite and delete the inbox CSV afterwards")

    s = add("analytics", "aggregate-only analytics JSON for the web app")
    s.add_argument("--labeler", default="rules-v1")
    s.add_argument("--out", help="default <root>/out/analytics.json")

    s = add("label-llm", "label calls with an OpenAI-compatible LLM (gated)")
    s.add_argument("--env-file", default=config.DEFAULT_LLM_ENV)
    s.add_argument("--limit", type=int)
    s.add_argument("--concurrency", type=int, default=4)

    s = add("verify-no-leak", "scan paths for numbers, hashes and transcript shingles")
    s.add_argument("--paths", nargs="+", required=True)
    s.add_argument("--ignore-regex", help="ignore phone-like matches that match this regex")
    s.add_argument("--list-files", action="store_true", help="also print matching file paths")
    s.add_argument("--seed", type=int, default=0)

    s = add("telus-setup", "store TELUS/RingCentral credentials (hidden prompts, mode 600)")
    s.add_argument("--force", action="store_true", help="replace an existing credentials file")

    add("telus-check", "log in read-only and print a permission summary (no numbers or names)")

    s = add("telus-pull-calllog", "pull call-log metadata (hashed numbers only) into private.db")
    s.add_argument("--since", help="ISO 8601 start (naive values are America/Vancouver local time)")
    s.add_argument("--days", type=int, help="pull the last N days")

    s = add("telus-worker", "process TELUS requests from the web app bridge folder, then exit")
    s.add_argument("--bridge", default="/var/lib/walnut-desk-telus", help="bridge folder shared with the app")

    s = add("telus-match", "compare a transcripts CSV with the TELUS call log, counts only (--csv)")
    s.add_argument("--export", help="also write direction and result per matched recording to this file (for the app importer)")

    s = add("telus-send-worker", "send the texts and start the calls the web app asked for (separate send credentials)")
    s.add_argument("--bridge", default="/var/lib/walnut-desk-telus", help="bridge folder shared with the app")
    s.add_argument("--inbound", action="store_true", help="first hand new incoming texts to the app (reply syncing)")
    s = add("telus-send-setup", "copy the current TELUS credentials to the separate send credentials file (shows nothing)")
    s.add_argument("--force", action="store_true")
    add("telus-send-check", "log in with the send credentials and say which send permissions it has (no numbers)")

    s = add("telus-messages-probe", "count SMS and voicemail in the TELUS message store (counts only)")
    s.add_argument("--days", type=int, default=120)

    s = add("telus-sync", "pull a window of the call log and write the live Brief numbers (analytics.json)")
    s.add_argument("--bridge", default="/var/lib/walnut-desk-telus", help="bridge folder shared with the app")
    s.add_argument("--days", type=int, default=35, help="call-log window to pull, 1 to 90 (default 35)")

    s = add("wipe-script", "write <root>/WIPE.sh")
    s.add_argument("--out", help="default <root>/WIPE.sh")
    return ap


def main(argv: list[str] | None = None) -> int:
    os.umask(0o077)
    args = build_parser().parse_args(argv)
    paths = config.resolve_paths(args)
    cmd = args.command
    if cmd == "load":
        from . import load
        return load.load(paths, force=args.force)
    if cmd == "audit":
        from . import audit
        return audit.run(paths, args.out)
    if cmd == "label-rules":
        from . import labeling
        return labeling.run(paths, args.rules)
    if cmd == "gold-sample":
        from . import gold
        return gold.sample(paths, args.n, args.seed, args.min_chars)
    if cmd == "gold-candidates":
        from . import gold
        return gold.candidates(paths, args.query, args.limit)
    if cmd == "gold-show":
        from . import gold
        return gold.show(paths, args.call_ref, args.chars, args.offset)
    if cmd == "gold-label":
        from . import gold
        return gold.label(paths, args.call_ref, args.json)
    if cmd == "evaluate":
        from . import evaluate
        return evaluate.run(paths, args.labeler)
    if cmd == "purge-private":
        from . import purge
        return purge.run(paths, args.conservative, args.shred_csv)
    if cmd == "analytics":
        from . import analytics
        return analytics.run(paths, args.labeler, args.out)
    if cmd == "label-llm":
        from . import llm
        return llm.run(paths, args.env_file, args.limit, args.concurrency)
    if cmd == "verify-no-leak":
        from . import leak
        return leak.run(paths, args.paths, args.ignore_regex, args.list_files, args.seed)
    if cmd == "telus-setup":
        from .telus import setup as telus_setup
        return telus_setup.run(paths.root, args.force)
    if cmd == "telus-match":
        from .telus import matchcheck
        return matchcheck.run(paths.db, paths.csv, getattr(args, "export", None))
    if cmd == "telus-worker":
        from .telus import worker
        return worker.run(paths.root, args.bridge, paths.db, paths.salt)
    if cmd == "telus-sync":
        from .telus import worker
        if not 1 <= args.days <= 90:
            print("error: --days must be between 1 and 90", file=sys.stderr)
            return 2
        return worker.Worker(paths.root, args.bridge, paths.db, paths.salt).run_sync_command(args.days)
    if cmd == "telus-send-worker":
        from .telus import send_worker
        return send_worker.run(paths.root, args.bridge, inbound=args.inbound)
    if cmd == "telus-send-setup":
        from .telus import config as tcfg
        try:
            dst = tcfg.copy_current_to_send(paths.root, args.force)
        except (tcfg.TelusConfigError, OSError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        print(f"send credentials stored at {dst} (mode 600). Nothing was displayed.")
        return 0
    if cmd == "telus-send-check":
        from .telus import config as tcfg
        from .telus import endpoints as ep
        from .telus.send_client import SendClient
        from .telus.client import TelusError
        try:
            creds = tcfg.load_send_credentials(paths.root)
            with SendClient(creds) as c:
                c.login()
                have = set(c.scopes)
                print(f"environment: {creds.env}")
                for need in ("SMS", "RingOut", "VoipCalling", "ReadAccounts"):
                    print(f"{need}: {'present' if need in have else 'missing'}")
                extra = sorted(x for x in have if x not in ("SMS", "RingOut", "ReadAccounts"))
                print(f"other_permissions_on_this_app: {len(extra)} (the sender cannot use them: its allowlist is two POST paths)")
                try:
                    body = c.get(ep.PHONE_NUMBERS_PATH, {"perPage": 100})
                    ok = any("SmsSender" in (r.get("features") or []) for r in body.get("records") or [])
                    print(f"sms_capable_number_found: {'yes' if ok else 'no'}")
                except TelusError:
                    print("sms_capable_number_found: could not check")
        except (tcfg.TelusConfigError, TelusError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        return 0
    if cmd == "telus-messages-probe":
        from .telus import config as tcfg
        from .telus import messages as tmsg
        try:
            creds = tcfg.load_credentials(paths.root)
        except tcfg.TelusConfigError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        return tmsg.run(creds, args.days)
    if cmd in ("telus-check", "telus-pull-calllog"):
        from .telus import config as tcfg
        try:
            creds = tcfg.load_credentials(paths.root)
        except tcfg.TelusConfigError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        if cmd == "telus-check":
            from .telus import check
            return check.run(creds)
        from .telus import calllog
        return calllog.run(paths, creds, args.since, args.days)
    if cmd == "wipe-script":
        from . import wipe
        return wipe.run(paths, args.out)
    return 2


if __name__ == "__main__":
    sys.exit(main())
