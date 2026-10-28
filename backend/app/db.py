"""Engine and session handling. SQLite with WAL, foreign keys and a busy timeout."""
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app import config

engine: Engine | None = None
SessionLocal: sessionmaker[Session] | None = None


def make_engine(db_path: str) -> Engine:
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 5},
        pool_pre_ping=False,
    )

    @event.listens_for(eng, "connect")
    def _on_connect(dbapi_conn, _rec):  # pragma: no cover - trivial
        # Take over transaction control so we can use BEGIN IMMEDIATE (see below).
        dbapi_conn.isolation_level = None
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()

    @event.listens_for(eng, "begin")
    def _on_begin(conn):
        # Writers take the lock up front. Avoids SQLITE_BUSY_SNAPSHOT when a read
        # transaction later upgrades to a write while another writer committed.
        conn.exec_driver_sql("BEGIN IMMEDIATE")

    return eng


def init_engine(db_path: str | None = None) -> Engine:
    """(Re)create the global engine. Tests call this with a temp path."""
    global engine, SessionLocal
    if engine is not None:
        engine.dispose()
    path = db_path or config.get_settings().db_path
    engine = make_engine(path)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return engine


def get_sessionmaker() -> sessionmaker[Session]:
    if SessionLocal is None:
        init_engine()
    assert SessionLocal is not None
    return SessionLocal


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one session per request, rolled back if not committed."""
    db = get_sessionmaker()()
    try:
        yield db
    finally:
        db.close()
