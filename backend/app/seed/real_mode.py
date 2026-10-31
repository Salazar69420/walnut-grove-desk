"""Real-data mode: remove every invented row and stop "Reset demo data" from bringing them back.

    cd /opt/walnut-desk/backend && sudo -u walnut-app bash -c 'set -a; . /etc/walnut-desk/app.env; set +a; \
        ../.venv/bin/python -m app.seed.real_mode --purge [--dry-run]'

Removed: all rows marked is_demo (contacts, pets, visits, messages, notes, tasks, calendar events, knowledge, fees, phone calls,
campaigns ...), and the operational demo tables from the first prototype (callbacks, lab results, message drafts, roster shifts,
overnight conversations, suggestions, open slots, example missed-call history, practice outbox rows).
Kept: staff accounts, settings, visit types and the emergency hospital rows (configuration, still marked unconfirmed), the
aggregate call analytics derived from real calls, and the append-only event log (it cannot be edited).
Real rows (imported calls, contacts created from them, anything staff typed) are never touched.
"""
from __future__ import annotations

import argparse

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models import (
    AppointmentRequest, Callback, CallLogDemo, Conversation, Draft, EmergencyRouting, LabResult, Message, OpenSlot, Outbox,
    Shift, SimSession, Suggestion,
)
from app.seed import clinic_seed, crm_seed
from app.services import events as ev
from app.services import settings_store as ss

KEY = "real_data_mode"
# children first
OPERATIONAL = [EmergencyRouting, AppointmentRequest, SimSession, Callback, Draft, Shift, Conversation, LabResult, Suggestion, OpenSlot, CallLogDemo]


def is_real(db: Session) -> bool:
    return bool(ss.get_raw(db, KEY, False))


def counts(db: Session) -> dict[str, int]:
    out: dict[str, int] = {}
    for m in OPERATIONAL:
        out[m.__tablename__] = db.scalar(select(func.count()).select_from(m)) or 0
    for m in crm_seed.CRM_TABLES + clinic_seed.CLINIC_TABLES:
        out[m.__tablename__] = db.scalar(select(func.count()).select_from(m).where(m.is_demo.is_(True))) or 0
    return {k: v for k, v in out.items() if v}


def purge_demo(db: Session, actor="Admin") -> dict[str, int]:
    before = counts(db)
    clinic_seed.wipe_clinic_demo(db)
    crm_seed.wipe_demo(db)
    for m in OPERATIONAL:
        db.execute(delete(m))
    # practice-mode outbox rows whose message is gone, and every non-message outbox row (all were demo)
    live_msgs = {str(i) for i in db.scalars(select(Message.id)).all()}
    for o in db.scalars(select(Outbox)).all():
        if o.source_type != "message" or o.source_id not in live_msgs:
            db.delete(o)
    ss.put_raw(db, KEY, True, by="real_mode")
    ev.record(db, ev.ADMIN, "real_data_mode", "Demo data removed. The site now holds only real data.", actor, "setting", KEY, {"removed": sum(before.values())})
    db.flush()
    return before


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--purge", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--db")
    a = ap.parse_args()
    if not a.purge:
        ap.error("nothing to do: pass --purge")
    from app import config, db

    cfg = config.get_settings()
    db.init_engine(a.db or cfg.db_path)
    with db.get_sessionmaker()() as s:
        if a.dry_run:
            print("DRY RUN, would remove:", counts(s) or "nothing")
            return 0
        removed = purge_demo(s)
        s.commit()
        print("Removed:", removed or "nothing", "| real data mode is ON")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
