"""Every RingCentral URL detail lives here, so unverified parts can be fixed in one place.

Nothing outside this module hard-codes a host or a path. The read-only client allows a GET only when the
path fully matches one of GET_ALLOWLIST, and a POST only to exactly TOKEN_PATH.
"""
from __future__ import annotations

import re

SERVER_URLS = {
    "production": "https://platform.ringcentral.com",
    "sandbox": "https://platform.devtest.ringcentral.com",
}

# The only hosts a recording content request may be redirected to (https, port 443, exact host match).
# The Authorization header is sent to these hosts and to no other.
REDIRECT_HOSTS = {
    "production": ("media.ringcentral.com", "platform.ringcentral.com"),
    "sandbox": ("media.devtest.ringcentral.com", "platform.devtest.ringcentral.com"),
}

TOKEN_PATH = "/restapi/oauth/token"
JWT_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:jwt-bearer"

ACCT = "/restapi/v1.0/account/~"
_ID = r"[A-Za-z0-9_~-]+"

CALL_LOG_PATH = f"{ACCT}/call-log"
EXT_CALL_LOG_PATH = f"{ACCT}/extension/~/call-log"
MESSAGE_STORE_PATH = f"{ACCT}/extension/~/message-store"
ACCOUNT_INFO_PATH = ACCT


def recording_path(recording_id: str) -> str:
    return f"{ACCT}/recording/{recording_id}"


def recording_content_path(recording_id: str) -> str:
    return f"{ACCT}/recording/{recording_id}/content"


# Fully matched (re.fullmatch) against the request path, with no query string.
GET_ALLOWLIST = [re.compile(p) for p in (
    re.escape(ACCT),                                                     # account info
    re.escape(CALL_LOG_PATH),                                            # account call log
    re.escape(EXT_CALL_LOG_PATH),                                        # extension call log
    re.escape(MESSAGE_STORE_PATH),                                       # voicemail and SMS list
    re.escape(MESSAGE_STORE_PATH) + f"/{_ID}",                           # one message
    re.escape(ACCT) + f"/recording/{_ID}",                               # recording metadata
    re.escape(ACCT) + f"/recording/{_ID}/content",                       # recording audio
)]

# Scopes the connector needs (names as RingCentral documents them).
REQUIRED_READ_SCOPES = ("ReadCallLog", "ReadCallRecording", "ReadMessages", "ReadAccounts")

# A scope is treated as write-capable if its name contains one of these words (case-insensitive) or
# if it does not start with "Read" at all (anything not provably read-only is refused).
WRITE_WORDS = ("edit", "write", "send", "manage", "admin", "create", "delete", "update", "modify", "add",
               "remove", "submit", "post", "control", "ringout", "sms", "fax", "voip", "internal", "glip",
               "webhook", "subscription", "messaging", "outbound")


def is_write_capable(scope: str) -> bool:
    low = scope.lower()
    if not scope.startswith("Read"):
        return True
    return any(w in low for w in WRITE_WORDS)


# ---------------------------------------------------------------------------- sending (separate client, separate credentials)
SMS_PATH = f"{ACCT}/extension/~/sms"
RINGOUT_PATH = f"{ACCT}/extension/~/ring-out"
PHONE_NUMBERS_PATH = f"{ACCT}/extension/~/phone-number"

# The ONLY two paths the send client may POST to (besides the token path). Exact match.
SEND_POST_PATHS = (SMS_PATH, RINGOUT_PATH)
SEND_GET_ALLOWLIST = [re.compile(p) for p in (
    re.escape(PHONE_NUMBERS_PATH),                       # find the extension's SMS-capable number
    re.escape(RINGOUT_PATH) + f"/{_ID}",                 # status of a call started with RingOut
    re.escape(MESSAGE_STORE_PATH) + f"/{_ID}",           # delivery status of one text
)]
