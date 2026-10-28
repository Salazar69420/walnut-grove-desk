from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

# Must be set before the app package reads its settings.
os.environ["WALNUT_ENV"] = "test"
os.environ.setdefault("WALNUT_DB_PATH", str(Path(tempfile.gettempdir()) / "walnut-test-unused.db"))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import config, db, migrate
from app.main import create_app
from app.models import User
from app.security.passwords import hash_password
from app.seed import create_users, reset_demo
from app.services import timeutil
from app.services.broadcaster import broadcaster

PASSWORD = "correct horse battery staple"


@pytest.fixture(scope="session")
def template_db(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("tpl") / "template.db"
    migrate.upgrade_head(str(path))
    db.init_engine(str(path))
    with db.get_sessionmaker()() as s:
        reset_demo(s, None)
        create_users(s)
        pw = hash_password(PASSWORD)
        for u in s.scalars(select(User)).all():
            u.password_hash = pw
            u.must_change_password = False
        s.add(User(username="newbie", display_name="Newbie", title="Front desk", initials="N", role="desk",
                   is_admin=False, password_hash=pw, must_change_password=True, active=True))
        s.add(User(username="gone", display_name="Gone", title="Former", initials="G", role="desk",
                   is_admin=False, password_hash=pw, must_change_password=False, active=False))
        s.commit()
    db.engine.dispose()
    # fold the WAL into the main file so a plain file copy is a complete database
    con = sqlite3.connect(path)
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    con.close()
    return path


class Api:
    """A TestClient that remembers its CSRF token and sends it on writes."""

    def __init__(self, client: TestClient):
        self.c = client
        self.csrf: str | None = None

    def login(self, username: str, password: str = PASSWORD, **kw):
        r = self.c.post("/api/auth/login", json={"username": username, "password": password}, **kw)
        if r.status_code == 200:
            self.csrf = r.json()["csrf_token"]
        return r

    def _h(self, extra=None):
        h = {}
        if self.csrf:
            h["X-CSRF-Token"] = self.csrf
        if extra:
            h.update(extra)
        return h

    def get(self, url, **kw):
        return self.c.get(url, **kw)

    def post(self, url, json=None, **kw):
        return self.c.post(url, json=json, headers=self._h(kw.pop("headers", None)), **kw)

    def patch(self, url, json=None, **kw):
        return self.c.patch(url, json=json, headers=self._h(kw.pop("headers", None)), **kw)

    def put(self, url, json=None, **kw):
        return self.c.put(url, json=json, headers=self._h(kw.pop("headers", None)), **kw)

    def delete(self, url, **kw):
        return self.c.delete(url, headers=self._h(kw.pop("headers", None)), **kw)


class Env:
    def __init__(self, app, path, settings, first: TestClient):
        self.app = app
        self.path = path
        self.settings = settings
        self._first = first
        self._used_first = False

    def client(self) -> Api:
        if not self._used_first:
            self._used_first = True
            return Api(self._first)
        return Api(TestClient(self.app))

    def session(self):
        return db.get_sessionmaker()()


@pytest.fixture()
def env(template_db, tmp_path):
    """Fresh copy of the template database and an app bound to it."""
    path = tmp_path / "app.db"
    shutil.copy(template_db, path)
    settings = config.Settings(env="test", db_path=str(path), skip_migrate=True, frontend_dist=str(tmp_path / "no-dist"))
    app = create_app(settings)
    timeutil.set_clock(None)
    broadcaster.reset()
    with TestClient(app) as first:
        yield Env(app, path, settings, first)
    timeutil.set_clock(None)
    broadcaster.reset()


@pytest.fixture()
def anon(env) -> Api:
    return env.client()


def signed_in(env, username: str) -> Api:
    api = env.client()
    r = api.login(username)
    assert r.status_code == 200, r.text
    return api


@pytest.fixture()
def priya(env) -> Api:
    return signed_in(env, "priya")


@pytest.fixture()
def marcus(env) -> Api:
    return signed_in(env, "marcus")


@pytest.fixture()
def anita(env) -> Api:
    return signed_in(env, "anita")


@pytest.fixture()
def garg(env) -> Api:
    return signed_in(env, "garg")
