"""python -m app.rules : load live call numbers (if the bridge file changed), then run the suggestion rules once."""
from __future__ import annotations

import sys

from app import config, db, migrate
from app.rules.engine import run_rules
from app.services import live_ingest


def main() -> int:
    s = config.get_settings()
    migrate.upgrade_head(s.db_path)
    db.init_engine(s.db_path)
    with db.get_sessionmaker()() as session:
        # Load fresh phone numbers first so the rules see them. Never raises, and keeps old numbers if the file is bad.
        res = live_ingest.ingest(session)
        print(f"live call numbers: {res.status}", file=sys.stderr)
        created = run_rules(session)
        session.commit()
    print(f"rules run: {len(created)} new suggestion(s)", file=sys.stderr)
    for c in created:
        print(f"{c.rule_key}\t{c.title}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
