"""Load the answer sheet draft (docs/ANSWER_SHEET_DRAFT.md) as PENDING knowledge and fees, so the owner can approve what is right.

    cd /opt/walnut-desk/backend && sudo -u walnut-app bash -c 'set -a; . /etc/walnut-desk/app.env; set +a; ../.venv/bin/python -m app.seed.answer_sheet --load'

Everything here was heard in recorded calls and is NOT confirmed. The text is written the way it would be said to a client; the caveat
lives in the source fields, never in the text, so approving an entry cannot send "heard in calls" to a client. Nothing is used by the
assistant until the owner approves it. Safe to run again (repeats are skipped).
"""
from __future__ import annotations

import argparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Fee, KnowledgeEntry
from app.services import crm
from app.services import knowledge as kn

REF = "Answer sheet draft (heard in recorded calls, not confirmed by the clinic)"
ENTRIES = [
    ("clinic_info", "Opening hours",
     "We are open Monday to Friday from 8:30 am to 7 pm, and Saturday and Sunday from 9 am to 5 pm.",
     "Staff said \"open until seven\" most often. Other calls said \"till five\" and \"nine to five\" on weekends. The clinic must set one set of hours."),
    ("patient_knowledge", "Before surgery: food and water",
     "Please do not feed your pet from midnight the night before surgery, and skip breakfast on the morning. Water is fine.",
     "Heard on calls. A nurse should confirm this for each procedure."),
    ("patient_knowledge", "Surgery drop-off",
     "Surgery drop-off is usually at 9:30 or 10 am, and your pet stays with us for the day.", "Heard on calls."),
    ("patient_knowledge", "Teeth pulled during a dental",
     "Your signed estimate has a section for extractions where you choose whether we call you before pulling any teeth.", "Heard on calls."),
    ("patient_communication", "Refills",
     "Some refills need a physical exam first, and the doctor decides. If an item is out of stock, we call you when it arrives, usually the next day.",
     "Heard on calls."),
    ("patient_communication", "Flea and tick products",
     "Yes, we sell topical and chewable flea and tick products at the front desk. Some need a prescription or a recent visit.", "Heard on calls."),
    ("patient_communication", "Lab results",
     "Results often arrive overnight. The doctor reviews them and then calls you, usually later that day. We cannot read results out before the doctor has reviewed them.",
     "Heard on calls."),
    ("patient_communication", "Emailing results",
     "Yes, we can email you the results after the doctor has spoken with you.", "Heard on calls."),
    ("patient_knowledge", "Records for another clinic",
     "To send records to another clinic, we look up the file and email the records. When we are busy we may ask the other clinic to email us the request.",
     "Heard on calls. The consent step still has to be confirmed by the clinic."),
]
FEES = [
    ("Office visit or exam", 4700, "per visit", "Plus tax. Other calls said 30 dollars and about 50. The clinic must publish one price."),
    ("Microchip", 5800, "", "Plus tax."),
    ("Nail trim", 2850, "", ""),
]


def load(db: Session) -> dict[str, int]:
    added = skipped = 0
    for cat, title, body, quote in ENTRIES:
        try:
            kn.propose(db, category=cat, title=title, body=body, proposed_by="import", source_kind="answer_sheet", source_ref=REF, source_quote=quote)
            added += 1
        except crm.CrmError:
            skipped += 1
    for service, cents, unit, note in FEES:
        if db.scalar(select(Fee.id).where(Fee.service == service)) is None:
            db.add(Fee(service=service, price_cents=cents, unit=unit, note=("Heard in calls, not confirmed. " + note).strip(), status="pending", proposed_by="import"))
            added += 1
        else:
            skipped += 1
    db.flush()
    return {"added": added, "skipped": skipped}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--load", action="store_true")
    ap.add_argument("--db")
    a = ap.parse_args()
    if not a.load:
        ap.error("pass --load")
    from app import config, db

    cfg = config.get_settings()
    db.init_engine(a.db or cfg.db_path)
    with db.get_sessionmaker()() as s:
        out = load(s)
        s.commit()
    print(f"answer sheet loaded as pending knowledge: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
