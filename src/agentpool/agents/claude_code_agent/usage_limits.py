"""Claude plan rate-limit windows as ``UsageLimits``.

Read with Claude Code's ``get_usage`` control request and kept current during turns from
the ``rate_limit_event`` messages of the stream (as t3code does). Window IDs are stable
across both sources: ``five_hour``, ``seven_day``, ``seven_day:<model>``, ...
"""

from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from agentpool.agents.usage_limits import UsageLimits, UsageWindow


if TYPE_CHECKING:
    from clawd_code_sdk.models import (
        SDKControlGetUsageResponse,
        UsageRateLimits,
        UsageWindow as ClaudeUsageWindow,
    )
    from clawd_code_sdk.models.context_usage import UsageReportLimit
    from clawd_code_sdk.models.messages import RateLimitInfo

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


_ROW_IDS = {"session": "five_hour", "weekly_all": "seven_day"}
"""Server row kinds -> the window IDs rate limit events use."""


def _row_window(row: UsageReportLimit) -> UsageWindow:
    """A server limit row (the CLI's canonical, server-ordered list)."""
    scope = row.scope
    scoped = (scope.model or scope.surface) if scope is not None else None
    period = _period(row.group)
    base_id = _ROW_IDS.get(row.kind, row.kind)
    match row.kind, scoped:
        case "session", _:
            label = "Current session"
        case _, None:
            label = f"{period.capitalize()} (all models)" if period != "other" else row.kind
        case _, named:
            label = f"{period.capitalize()} ({named.display_name})"
    return UsageWindow(
        id=f"seven_day:{scoped.display_name}" if scoped else base_id,
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
            _named_window(f"seven_day:{w.display_name}", f"Weekly ({w.display_name})", "weekly", w)
            for w in limits.model_scoped or []
        )
    spend = limits.spend
    extra = limits.extra_usage
    if spend is not None and spend.enabled and spend.percent is not None:
        windows.append(
            UsageWindow(
                id="monthly_spend",
                label="Monthly spend",
                period="monthly",
                percent=spend.percent,
                limited=spend.percent >= 100,  # noqa: PLR2004
            )
        )
    elif extra is not None and extra.is_enabled and extra.utilization is not None:
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


def _event_windows(info: RateLimitInfo) -> dict[str, tuple[float, datetime | None, bool]]:
    """Window ID -> (percent 0-100, reset, rejected) reported by a rate limit event.

    ``unified_windows`` carries every window; older events only the one that changed.
    Event utilization is 0-1, reset times are epoch seconds.
    """
    reported: dict[str, tuple[float, datetime | None, bool]] = {}
    windows: dict[str, Any] = info.unified_windows or {}
    for window_id, raw in windows.items():
        if not isinstance(raw, dict) or not isinstance(
            utilization := raw.get("utilization"), int | float
        ):
            continue
        resets = raw.get("resetsAt")
        reset = datetime.fromtimestamp(resets, UTC) if isinstance(resets, int | float) else None
        reported[window_id] = (utilization * 100, reset, raw.get("status") == "rejected")
    if not reported and info.rate_limit_type and info.utilization is not None:
        reset = datetime.fromtimestamp(info.resets_at, UTC) if info.resets_at else None
        reported[info.rate_limit_type] = (info.utilization * 100, reset, info.status == "rejected")
    return reported


def apply_rate_limit_event(limits: UsageLimits, info: RateLimitInfo) -> UsageLimits:
    """Update the windows a ``rate_limit_event`` reports (other windows are kept)."""
    reported = _event_windows(info)
    if not reported:
        return limits
    windows = []
    for window in limits.windows:
        if (update := reported.get(window.id)) is None:
            windows.append(window)
            continue
        percent, reset, rejected = update
        changed = replace(
            window,
            percent=percent,
            resets_at=reset or window.resets_at,
            limited=rejected or percent >= 100,  # noqa: PLR2004
        )
        windows.append(changed)
    return replace(limits, windows=windows)
