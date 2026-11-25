"""Server password and origin checks (shared by the HTTP middleware and the websockets)."""

from __future__ import annotations

import base64
import hmac
import re


AUTH_USERNAME = "opencode"
CORS_ORIGIN_REGEX = (
    r"^(https?://(localhost|127\.0\.0\.1)(:\d+)?"
    r"|https://([a-z0-9-]+\.)*opencode\.ai"
    r"|tauri://localhost|https?://tauri\.localhost|oc://renderer)$"
)
AUTH_TOKEN_QUERY = "auth_token"
"""Query parameter carrying base64 ``user:password`` (for clients that cannot set headers)."""


def _credential_ok(encoded: str, password: str) -> bool:
    try:
        decoded = base64.b64decode(encoded.strip(), validate=True).decode()
    except ValueError, UnicodeDecodeError:
        return False
    username, _, given = decoded.partition(":")
    return username == AUTH_USERNAME and hmac.compare_digest(given.encode(), password.encode())


def is_authorized(header: str | None, auth_token: str | None, password: str | None) -> bool:
    """Whether a request carries the server password (always true without a password).

    Like OpenCode, the credential is HTTP Basic, either in the ``Authorization`` header or
    base64-encoded in the ``auth_token`` query parameter.
    """
    if password is None:
        return True
    if auth_token:
        return _credential_ok(auth_token, password)
    if not header or not header.lower().startswith("basic "):
        return False
    return _credential_ok(header[6:], password)


def is_allowed_origin(origin: str | None, host: str | None) -> bool:
    """OpenCode's origin policy: no origin (non-browser), the server's own host, or a known app."""
    if not origin:
        return True
    if host and origin.partition("://")[2] == host:
        return True
    return re.match(CORS_ORIGIN_REGEX, origin) is not None
