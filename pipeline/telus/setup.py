"""telus-setup: interactive, hidden input, atomic mode-600 write. No non-interactive mode on purpose."""
from __future__ import annotations

import getpass
import os
import sys

from . import config as tc


def run(root: str, force: bool, send: bool = False) -> int:
    """send=True stores the TEXTING login (the app allowed to send texts) instead of the read-only one."""
    path = tc.send_credentials_path(root) if send else tc.credentials_path(root)
    if os.path.lexists(path) and not force:
        print("error: credentials file already exists (use --force to replace it)", file=sys.stderr)
        return 2
    try:
        env = getpass.getpass("TELUS_ENV (sandbox or production): ").strip()
        if env not in tc.ENVS:
            raise tc.TelusConfigError("TELUS_ENV must be sandbox or production")
        url = getpass.getpass(f"TELUS_SERVER_URL (blank for {tc.default_server_url(env)}): ").strip()
        url = tc.validate_server_url(url or tc.default_server_url(env))
        values = {"TELUS_ENV": env, "TELUS_SERVER_URL": url}
        for key in ("TELUS_CLIENT_ID", "TELUS_CLIENT_SECRET", "TELUS_JWT"):
            values[key] = tc.check_value(key, getpass.getpass(f"{key}: ").strip())
        tc.write_credentials(root, values, force=force, send=send)
    except tc.TelusConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except (EOFError, KeyboardInterrupt):
        print("error: cancelled, nothing saved", file=sys.stderr)
        return 2
    print("saved as the texting login (the app that can send texts)" if send else "saved as the read-only login (calls, recordings, live sync)")
    print("keys stored: " + ", ".join(tc.KEYS))
    return 0
