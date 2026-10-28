"""python -m app.pims_sync --dir samples/pims : load a folder of exports into the read-only pims_* tables."""
from __future__ import annotations

import argparse
import sys

from app import config, db, migrate
from app.adapters.pims import CsvExportAdapter
from app.services import importers as imp


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.pims_sync")
    ap.add_argument("--dir", required=True, help="folder with appointments.csv, clients.csv, patients.csv, lab_results.csv")
    args = ap.parse_args(argv)
    s = config.get_settings()
    migrate.upgrade_head(s.db_path)
    db.init_engine(s.db_path)
    ad = CsvExportAdapter(args.dir)
    with db.get_sessionmaker()() as session:
        total = 0
        for kind, rows in (("clients", ad.clients()), ("pets", ad.patients()), ("appointments", ad.appointments()),
                           ("lab_results", ad.lab_results())):
            if rows:
                n = imp.load(session, kind, rows, "ImproMed sync")
                total += n
                print(f"{kind}: {n}", file=sys.stderr)
        session.commit()
    print(f"synced {total} rows (read only)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
