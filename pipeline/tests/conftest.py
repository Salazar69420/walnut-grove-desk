import os
import shutil
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # /opt/walnut-desk
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pipeline import cli, config  # noqa: E402
import make_fixture  # noqa: E402


@pytest.fixture(autouse=True)
def _never_touch_real_root(monkeypatch, tmp_path_factory):
    """Any command run without --root must not reach the real private root."""
    monkeypatch.setattr(config, "DEFAULT_ROOT", str(tmp_path_factory.mktemp("bogus_default_root")))


@pytest.fixture(scope="session")
def base_env(tmp_path_factory):
    """Fixture root with CSV loaded and rules-labeled. Treated as read-only; use `env` for mutation."""
    root = tmp_path_factory.mktemp("base") / "walnut"
    (root / "inbox").mkdir(parents=True)
    info = make_fixture.make_fixture(str(root / "inbox" / "all_transcripts.csv"))
    assert cli.main(["load", "--root", str(root)]) == 0
    assert cli.main(["label-rules", "--root", str(root)]) == 0
    return {"root": str(root), "info": info}


@pytest.fixture
def env(base_env, tmp_path):
    root = tmp_path / "walnut"
    shutil.copytree(base_env["root"], root)
    return {"root": str(root), "info": base_env["info"]}


def run(argv, capsys=None):
    code = cli.main(argv)
    out = capsys.readouterr() if capsys else None
    return code, out
