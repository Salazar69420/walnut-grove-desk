"""python -m app.rules : run the suggestion rules once."""
from __future__ import annotations

import sys

from app import config, db, migrate
from app.rules.engine import run_rules


def main() -> int:
    s = config.get_settings()
    migrate.upgrade_head(s.db_path)
    db.init_engine(s.db_path)
    with db.get_sessionmaker()() as session:
        created = run_rules(session)
        session.commit()
    print(f"rules run: {len(created)} new suggestion(s)", file=sys.stderr)
    for c in created:
        print(f"{c.rule_key}\t{c.title}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
