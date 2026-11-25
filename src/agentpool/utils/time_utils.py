"""Date and time utilities."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import re
import time
from typing import Literal


TimeZoneMode = Literal["utc", "local"]


# Time units with their patterns
_WEEKS = r"(?P<weeks>[\d.]+)\s*(?:w|wks?|weeks?)"
_DAYS = r"(?P<days>[\d.]+)\s*(?:d|dys?|days?)"
_HOURS = r"(?P<hours>[\d.]+)\s*(?:h|hrs?|hours?)"
_MINS = r"(?P<mins>[\d.]+)\s*(?:m|mins?|minutes?)"
_SECS = r"(?P<secs>[\d.]+)\s*(?:s|secs?|seconds?)"

# Separators between units
_SEPARATORS = r"[,/]"


# Optional patterns with separators
def _OPT(x: str) -> str:  # noqa: N802
    return f"(?:{x})?"


def _OPTSEP(x: str) -> str:  # noqa: N802
    return f"(?:{x}\\s*(?:{_SEPARATORS}\\s*)?)?"


# All supported time formats
_TIME_FORMAT = f"{_OPTSEP(_WEEKS)}{_OPTSEP(_DAYS)}{_OPTSEP(_HOURS)}{_OPTSEP(_MINS)}{_OPT(_SECS)}"

# Time unit multipliers in seconds
_MULTIPLIERS = {
    "weeks": 60 * 60 * 24 * 7,
    "days": 60 * 60 * 24,
    "hours": 60 * 60,
    "mins": 60,
    "secs": 1,
}

# Compile patterns
_SIGN_PATTERN = re.compile(r"\s*(?P<sign>[+|-])?\s*(?P<unsigned>.*$)")
_TIME_PATTERN = re.compile(rf"\s*{_TIME_FORMAT}\s*$", re.IGNORECASE)


def get_now(tz_mode: TimeZoneMode = "utc") -> datetime:
    """Get current datetime in UTC or local timezone."""
    now = datetime.now(UTC)
    return now.astimezone() if tz_mode == "local" else now


def now_ms() -> int:
    """Return current time in milliseconds as integer."""
    return int(time.time() * 1000)


def ms_to_datetime(ms: int) -> datetime:
    """Convert milliseconds timestamp to datetime (UTC)."""
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


def datetime_to_ms(dt: datetime) -> int:
    """Convert datetime to milliseconds timestamp."""
    return int(dt.timestamp() * 1000)


def parse_iso_timestamp(value: str, *, fallback: datetime | None = None) -> datetime:
    """Parse an ISO 8601 timestamp string, handling 'Z' suffix.

    Falls back to the provided fallback or current UTC time on parse failure.

    Args:
        value: ISO timestamp string (may use 'Z' instead of '+00:00')
        fallback: Datetime to return on parse failure (defaults to current UTC time)

    Returns:
        Parsed timezone-aware datetime, or fallback on failure.
    """
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError, AttributeError:
        return fallback if fallback is not None else get_now()


def parse_time_period(period: str) -> timedelta:
    """Parse a time expression into a timedelta.

    Examples:
        - Simple format: 1h, 2d, 1w
        - Full words: 1 hour, 2 days, 1 week
        - Combined: 1 week 2 days 3 hours
        - With separators: 1h, 30m
        - Signed: -1h, +2d
        - Decimal values: 1.5h

    Args:
        period: Time period string to parse

    Raises:
        ValueError: If the time format is invalid

    Returns:
        Parsed time period as timedelta
    """
    # Handle sign
    sign_match = _SIGN_PATTERN.match(period)
    if not sign_match:
        raise ValueError(f"Invalid time format: {period}")

    sign = -1 if sign_match.group("sign") == "-" else 1
    unsigned = sign_match.group("unsigned")

    # Match time pattern
    if match := _TIME_PATTERN.match(unsigned):
        dct = match.groupdict()
        matches = {k: v for k, v in dct.items() if v is not None}
        try:
            secs = sum(_MULTIPLIERS[unit] * float(val) for unit, val in matches.items())
            return timedelta(seconds=sign * secs)
        except (ValueError, KeyError) as e:
            raise ValueError(f"Invalid time value in: {period}") from e

    raise ValueError(f"Unsupported time format: {period}")
