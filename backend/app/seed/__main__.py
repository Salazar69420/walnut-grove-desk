"""python -m app.seed --reset-demo | --create-users"""
from __future__ import annotations

import argparse
import sys

from app import config, db, migrate
from app.seed import create_users, reset_demo


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.seed", description="Walnut Grove Desk seed")
    ap.add_argument("--reset-demo", action="store_true", help="reseed the operational demo tables")
    ap.add_argument("--create-users", action="store_true", help="create the 6 accounts, print username<TAB>password once")
    args = ap.parse_args(argv)
    if not (args.reset_demo or args.create_users):
        ap.print_help(sys.stderr)
        return 2

    s = config.get_settings()
    migrate.upgrade_head(s.db_path)
    db.init_engine(s.db_path)
    with db.get_sessionmaker()() as session:
        if args.reset_demo:
            counts = reset_demo(session, None)
            session.commit()
            print("demo data reset: " + ", ".join(f"{k}={v}" for k, v in counts.items()), file=sys.stderr)
        if args.create_users:
            try:
                created = create_users(session)
            except RuntimeError as e:
                session.rollback()
                print(f"error: {e}", file=sys.stderr)
                return 1
            session.commit()
            for username, password in created:
                print(f"{username}\t{password}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
