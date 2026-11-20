"""Claude plan rate-limit windows (from Claude Code's ``get_usage``) as ``UsageLimits``."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from agentpool.agents.usage_limits import UsageLimits, UsageWindow


if TYPE_CHECKING:
    from clawd_code_sdk.models import (
        SDKControlGetUsageResponse,
        UsageRateLimits,
        UsageWindow as ClaudeUsageWindow,
    )
    from clawd_code_sdk.models.context_usage import UsageReportLimit

    from agentpool.agents.usage_limits import UsagePeriod


CLAUDE_USAGE_URL = "https://claude.ai/settings/usage"

_NAMED_WINDOWS: list[tuple[str, str, UsagePeriod]] = [
    ("five_hour", "Current session", "session"),
    ("seven_day", "Weekly (all models)", "weekly"),
    ("seven_day_opus", "Weekly (Opus)", "weekly"),
    ("seven_day_sonnet", "Weekly (Sonnet)", "weekly"),
    ("seven_day_oauth_apps", "Weekly (apps)", "weekly"),
]


def _timestamp(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _period(group: str) -> UsagePeriod:
    match group:
        case "session" | "weekly" | "monthly":
            return group
        case _:
            return "other"


def _row_window(row: UsageReportLimit) -> UsageWindow:
    """A server limit row (the CLI's canonical, server-ordered list)."""
    scope = row.scope
    scoped = (scope.model or scope.surface) if scope is not None else None
    period = _period(row.group)
    match row.kind, scoped:
        case "session", _:
            label = "Current session"
        case _, None:
            label = f"{period.capitalize()} (all models)" if period != "other" else row.kind
        case _, named:
            label = f"{period.capitalize()} ({named.display_name})"
    return UsageWindow(
        id=f"{row.kind}:{scoped.display_name}" if scoped else row.kind,
        label=label,
        period=period,
        percent=row.percent,
        resets_at=_timestamp(row.resets_at),
        limited=row.percent >= 100,  # noqa: PLR2004
    )


def _named_window(
    key: str, label: str, period: UsagePeriod, window: ClaudeUsageWindow
) -> UsageWindow:
    percent = window.utilization or 0.0
    return UsageWindow(
        id=key,
        label=label,
        period=period,
        percent=percent,
        resets_at=_timestamp(window.resets_at),
        limited=percent >= 100,  # noqa: PLR2004
    )


def _windows(limits: UsageRateLimits) -> list[UsageWindow]:
    if limits.limits:
        windows = [_row_window(row) for row in limits.limits]
    else:  # Servers that predate the limit rows
        named = {
            "five_hour": limits.five_hour,
            "seven_day": limits.seven_day,
            "seven_day_opus": limits.seven_day_opus,
            "seven_day_sonnet": limits.seven_day_sonnet,
            "seven_day_oauth_apps": limits.seven_day_oauth_apps,
        }
        windows = [
            _named_window(key, label, period, window)
            for key, label, period in _NAMED_WINDOWS
            if (window := named[key]) is not None
        ]
        windows.extend(
            _named_window(
                f"weekly_scoped:{w.display_name}", f"Weekly ({w.display_name})", "weekly", w
            )
            for w in limits.model_scoped or []
        )
    extra = limits.extra_usage
    if extra is not None and extra.is_enabled and extra.utilization is not None:
        windows.append(
            UsageWindow(
                id="extra_usage",
                label="Extra usage",
                period="monthly",
                percent=extra.utilization,
                limited=extra.utilization >= 100,  # noqa: PLR2004
            )
        )
    return windows


def to_usage_limits(usage: SDKControlGetUsageResponse) -> UsageLimits | None:
    """Plan limits, or None when they do not apply (API key, Bedrock, Vertex, ...)."""
    if not usage.rate_limits_available or usage.rate_limits is None:
        return None
    return UsageLimits(
        windows=_windows(usage.rate_limits),
        plan=usage.subscription_type,
        usage_url=CLAUDE_USAGE_URL,
    )
