"""Give contacts found in call recordings their phone number, from `telus-numbers --export` (owner request).

    cd /opt/walnut-desk
    sudo -u walnut-app bash -c 'set -a; . /etc/walnut-desk/app.env; set +a; \
        .venv/bin/python -m pipeline.app_numbers --numbers /var/lib/walnut-desk-telus/numbers.json [--dry-run]'

* Each imported call with a recording id gets the caller's number.
* A contact gets a number only when it has none yet, ALL of its calls agree on one number, and no other contact has or would get
  that number (a shared family or business line stays empty, so a text never reaches the wrong person).
* Consent is NOT changed: they stay "not opted in" until they text the clinic and reply Y (app/services/inbound.py).
* The export file is deleted after reading (also on a dry run, unless --keep). Only counts are printed.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

E164 = re.compile(r"^\+1[2-9][0-9]{2}[2-9][0-9]{6}$")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--numbers", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--keep", action="store_true", help="do not delete the export file")
    ap.add_argument("--db", help="app database path (default: from the app environment)")
    a = ap.parse_args(argv)

    from sqlalchemy import select

    from app import config, db
    from app.models import Contact, PhoneCall
    from app.services import events as ev

    with open(a.numbers, encoding="utf-8") as fh:
        raw = (json.load(fh) or {}).get("numbers") or {}
    if not a.keep:
        try:
            os.unlink(a.numbers)
        except OSError:
            pass
    numbers = {str(k): v for k, v in raw.items() if isinstance(v, str) and E164.match(v) and re.fullmatch(r"[0-9]{1,24}", str(k))}
    db.init_engine(a.db or config.get_settings().db_path)
    stats = {"numbers_in_file": len(numbers), "calls_given_a_number": 0, "contacts_given_a_number": 0, "contacts_with_several_numbers": 0,
             "numbers_shared_by_several_contacts": 0, "contacts_already_had_a_number": 0}
    with db.get_sessionmaker()() as s:
        calls = s.scalars(select(PhoneCall).where(PhoneCall.recording_id.is_not(None))).all()
        per_contact: dict[int, set[str]] = defaultdict(set)
        for c in calls:
            n = numbers.get(c.recording_id)
            if not n:
                continue
            if c.phone != n:
                c.phone = n
                stats["calls_given_a_number"] += 1
            if c.contact_id:
                per_contact[c.contact_id].add(n)
        taken = {p for (p,) in s.execute(select(Contact.phone).where(Contact.phone.is_not(None), Contact.erased_at.is_(None))).all()}
        want: dict[str, list[int]] = defaultdict(list)
        for cid, nums in per_contact.items():
            if len(nums) == 1:
                want[next(iter(nums))].append(cid)
            else:
                stats["contacts_with_several_numbers"] += 1
        for n, cids in want.items():
            if len(cids) > 1 or n in taken:
                stats["numbers_shared_by_several_contacts"] += 1
                continue
            ct = s.get(Contact, cids[0])
            if ct is None or ct.erased_at is not None:
                continue
            if ct.phone:
                stats["contacts_already_had_a_number"] += 1
                continue
            ct.phone = n  # consent stays as it is ("none"): no texts until they text us and reply Y
            stats["contacts_given_a_number"] += 1
        if a.dry_run:
            s.rollback()
        else:
            ev.record(s, ev.CONTACTS, "numbers_from_calls", f"Added phone numbers from the TELUS call log to {stats['contacts_given_a_number']} contacts",
                      "Admin import", "contact", None, {k: v for k, v in stats.items()})
            s.commit()
    print(("DRY RUN, nothing written. " if a.dry_run else "Done. ") + ", ".join(f"{k}={v}" for k, v in stats.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
