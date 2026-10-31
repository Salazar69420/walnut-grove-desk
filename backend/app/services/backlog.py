"""Analyse every older call that already has a transcript (the imported recordings), once.

    cd /opt/walnut-desk/backend
    sudo -u walnut-app bash -c 'set -a; . /etc/walnut-desk/app.env; set +a; ../.venv/bin/python -m app.services.backlog [--limit N]'

Same analysis as new calls (services/transcribe.py) in historical mode: summary, reason, patient or inquiry, call notes for Knowledge
(pending, capped), contacts the system created get their full name, pets and patient/inquiry; a number with a name and no contact
becomes a prospect (not opted in). No tasks and no Calendar requests for old calls. The model is called with no database
transaction open, several at a time; each result is saved in its own short transaction. Prints counts only. Safe to run again.
"""
from __future__ import annotations

import argparse
import logging
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import func, select

from app import config, db
from app.assistant import ai
from app.models import PhoneCall
from app.services import timeutil, transcribe

log = logging.getLogger("walnut.backlog")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--limit", type=int, default=5000)
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args(argv)
    db.init_engine(config.get_settings().db_path)
    S = db.get_sessionmaker()
    with S() as s:
        cfg = ai.get_config(s)
        jobs = [(c.id, c.transcript, timeutil.local(c.started_at).strftime("%A %Y-%m-%d %H:%M"))
                for c in s.scalars(select(PhoneCall).where(PhoneCall.transcript.is_not(None), PhoneCall.intent.is_(None))
                                   .order_by(PhoneCall.started_at.desc()).limit(a.limit)).all()]
        s.commit()
    if not cfg.get("key_enc"):
        print("No OpenRouter key is set.")
        return 1
    counts = {"calls": len(jobs), "done": 0, "failed": 0}

    def work(job):
        cid, text, when = job
        try:
            return cid, text, transcribe.analyse(cfg, text, when)
        except Exception as e:  # noqa: BLE001 - nothing from the call is logged
            return cid, text, e

    with ThreadPoolExecutor(max_workers=max(1, min(a.workers, 10))) as pool:
        for i, (cid, text, res) in enumerate(pool.map(work, jobs), 1):
            if isinstance(res, Exception):
                counts["failed"] += 1
            else:
                with S() as s:
                    try:
                        c = s.get(PhoneCall, cid)
                        if c is not None and c.intent is None:
                            transcribe.file_call(s, c, text, res, historical=True)
                            s.commit()
                            counts["done"] += 1
                    except Exception as e:  # noqa: BLE001
                        s.rollback()
                        counts["failed"] += 1
                        log.warning("could not save one call (%s)", type(e).__name__)
            if i % 100 == 0:
                print(f"progress {i}/{len(jobs)} done={counts['done']} failed={counts['failed']}", flush=True)
    with S() as s:
        counts["patient_calls"] = s.scalar(select(func.count()).select_from(PhoneCall).where(PhoneCall.call_type == "patient")) or 0
        counts["inquiry_calls"] = s.scalar(select(func.count()).select_from(PhoneCall).where(PhoneCall.call_type == "inquiry")) or 0
        s.commit()
    print("Done. " + ", ".join(f"{k}={v}" for k, v in counts.items()), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
