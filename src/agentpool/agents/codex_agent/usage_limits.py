"""ChatGPT plan rate-limit windows of the Codex app-server as ``UsageLimits``.

Read with ``account/rateLimits/read``; the ``account/rateLimits/updated`` notifications of
each turn keep codexed's ``client.account.rate_limits`` current in between.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from agentpool.agents.usage_limits import UsageLimits, UsageWindow


if TYPE_CHECKING:
    from codexed.models import RateLimitSnapshot, RateLimitWindow

    from agentpool.agents.usage_limits import UsagePeriod


CODEX_USAGE_URL = "https://chatgpt.com/codex/settings/usage"
SESSION_MAX_MINS = 6 * 60
WEEK_MINS = 7 * 24 * 60


def _period(minutes: int | None) -> UsagePeriod:
    match minutes:
        case None:
            return "other"
        case _ if minutes <= SESSION_MAX_MINS:
            return "session"
        case _ if minutes <= WEEK_MINS:
            return "weekly"
        case _:
            return "monthly"


def _label(period: UsagePeriod, minutes: int | None) -> str:
    match period:
        case "session":
            return "Current session"
        case "weekly":
            return "Weekly"
        case "monthly":
            return "Monthly"
        case "other":
            return f"{minutes // 60}h window" if minutes else "Usage"


def _window(window_id: str, window: RateLimitWindow) -> UsageWindow:
    period = _period(window.window_duration_mins)
    resets = datetime.fromtimestamp(window.resets_at, UTC) if window.resets_at else None
    return UsageWindow(
        id=window_id,
        label=_label(period, window.window_duration_mins),
        period=period,
        percent=float(window.used_percent),
        resets_at=resets,
        limited=window.used_percent >= 100,  # noqa: PLR2004
    )


def to_usage_limits(snapshot: RateLimitSnapshot) -> UsageLimits:
    """The plan's windows: primary (usually 5 hours) and secondary (usually a week)."""
    windows = [
        _window(window_id, window)
        for window_id, window in (("primary", snapshot.primary), ("secondary", snapshot.secondary))
        if window is not None
    ]
    return UsageLimits(windows=windows, plan=snapshot.plan_type, usage_url=CODEX_USAGE_URL)
