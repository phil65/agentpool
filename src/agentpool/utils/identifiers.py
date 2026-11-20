"""Identifier generation utilities.

Generates IDs that are lexicographically sortable by creation time.
Format: {prefix}_{hex_timestamp}{random_base62}

Compatible with OpenCode's identifier format.
"""

from __future__ import annotations

import secrets
import time
from typing import Literal


PrefixType = Literal[
    "session",
    "message",
    "permission",
    "user",
    "part",
    "pty",
    "call",
    "workspace",
    "event",
    "form",
    "shell",
]

PREFIXES: dict[PrefixType, str] = {
    "session": "ses",
    "message": "msg",
    "permission": "per",
    "user": "usr",
    "part": "prt",
    "pty": "pty",
    "call": "cal",
    "workspace": "wsp",
    "event": "evt",
    "form": "frm",
    "shell": "sh",
}

BASE62_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
ID_LENGTH = 26  # Characters after prefix (12 hex + 14 base62)

# State for monotonic ID generation
_last_timestamp = 0
_counter = 0


def _random_base62(length: int) -> str:
    """Generate random base62 string."""
    return "".join(secrets.choice(BASE62_CHARS) for _ in range(length))


def ascending(prefix: PrefixType, given: str | None = None) -> str:
    """Generate an ascending (chronologically sortable) ID.

    Args:
        prefix: The type prefix for the ID
        given: If provided, validate and return this ID instead of generating

    Returns:
        A sortable ID with the format {prefix}_{hex_timestamp}{random}

    Raises:
        ValueError: If given ID doesn't start with expected prefix
    """
    if given is None:
        return _create(prefix, descending=False)
    if not given.startswith(expected_prefix := PREFIXES[prefix]):
        raise ValueError(f"ID {given} does not start with {expected_prefix}")
    return given


def descending(prefix: PrefixType) -> str:
    """Generate a descending (reverse chronologically sortable) ID."""
    return _create(prefix, descending=True)


def _create(prefix: PrefixType, *, descending: bool = False) -> str:
    """Create a new ID with timestamp encoding.

    Args:
        prefix: The type prefix
        descending: If True, invert the timestamp for reverse sorting

    Returns:
        A new ID string
    """
    global _last_timestamp, _counter  # noqa: PLW0603

    current_timestamp = int(time.time() * 1000)  # milliseconds
    if current_timestamp != _last_timestamp:
        _last_timestamp = current_timestamp
        _counter = 0
    _counter += 1

    # Combine timestamp and counter
    now = current_timestamp * 0x1000 + _counter
    if descending:
        now = ~now & 0xFFFFFFFFFFFF  # Invert for descending order (48 bits)

    # Encode as 6 bytes (48 bits), big-endian
    time_bytes = bytearray(6)
    for i in range(6):
        time_bytes[i] = (now >> (40 - 8 * i)) & 0xFF

    time_hex = time_bytes.hex()
    # Add random suffix (14 chars for 26 total after prefix)
    random_suffix = _random_base62(ID_LENGTH - 12)
    return f"{PREFIXES[prefix]}_{time_hex}{random_suffix}"


def generate_session_id() -> str:
    """Generate a unique, chronologically sortable session ID ('ses_b71310fdf0...')."""
    return ascending("session")
