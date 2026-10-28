"""wipe-script: writes <root>/WIPE.sh from the packaged template."""
from __future__ import annotations

import os

from .config import PACKAGE_DIR


def run(paths, out: str | None = None) -> int:
    with open(os.path.join(PACKAGE_DIR, "wipe_template.sh"), "r", encoding="utf-8") as f:
        body = f.read()
    out = out or os.path.join(paths.root, "WIPE.sh")
    os.makedirs(os.path.dirname(os.path.abspath(out)), mode=0o700, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(body)
    os.chmod(out, 0o700)
    print("wrote WIPE.sh")
    return 0
