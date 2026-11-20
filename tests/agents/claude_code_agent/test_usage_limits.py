"""Claude plan rate-limit windows -> UsageLimits."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from clawd_code_sdk.models import SDKControlGetUsageResponse

from agentpool.agents.claude_code_agent.usage_limits import CLAUDE_USAGE_URL, to_usage_limits
from agentpool.agents.usage_limits import UsageWindow


RESET = "2026-11-22T13:00:00+00:00"
SESSION = {
    "total_cost_usd": 0,
    "total_api_duration_ms": 0,
    "total_duration_ms": 0,
    "total_lines_added": 0,
    "total_lines_removed": 0,
    "model_usage": {},
}


def _usage(rate_limits: dict[str, Any] | None, *, available: bool = True) -> Any:
    return SDKControlGetUsageResponse.model_validate({
        "session": SESSION,
        "subscription_type": "max",
        "rate_limits_available": available,
        "rate_limits": rate_limits,
    })


def _row(kind: str, group: str, percent: float, model: str | None = None) -> dict[str, Any]:
    scope = {"model": {"display_name": model}} if model else None
    return {
        "kind": kind,
        "group": group,
        "percent": percent,
        "resets_at": RESET,
        "severity": "normal",
        "is_active": False,
        "scope": scope,
    }


def test_server_limit_rows_are_used_in_order() -> None:
    limits = to_usage_limits(
        _usage({
            "five_hour": {"utilization": 1, "resets_at": RESET},
            "limits": [
                _row("session", "session", 100),
                _row("weekly_all", "weekly", 35),
                _row("weekly_scoped", "weekly", 5, model="Opus"),
            ],
        })
    )
    assert limits is not None
    assert (limits.plan, limits.usage_url) == ("max", CLAUDE_USAGE_URL)
    reset = datetime(2026, 11, 22, 13, tzinfo=UTC)
    assert limits.windows == [
        UsageWindow(
            id="session",
            label="Current session",
            period="session",
            percent=100,
            resets_at=reset,
            limited=True,
        ),
        UsageWindow(
            id="weekly_all",
            label="Weekly (all models)",
            period="weekly",
            percent=35,
            resets_at=reset,
        ),
        UsageWindow(
            id="weekly_scoped:Opus",
            label="Weekly (Opus)",
            period="weekly",
            percent=5,
            resets_at=reset,
        ),
    ]


def test_named_windows_without_rows_and_extra_usage() -> None:
    extra = {
        "is_enabled": True,
        "monthly_limit": 50,
        "used_credits": 10,
        "utilization": 20,
        "currency": "USD",
    }
    limits = to_usage_limits(
        _usage({
            "five_hour": {"utilization": 66, "resets_at": RESET},
            "seven_day": {"utilization": 35, "resets_at": None},
            "seven_day_opus": None,
            "model_scoped": [{"display_name": "Fable", "utilization": 0, "resets_at": RESET}],
            "extra_usage": extra,
        })
    )
    assert limits is not None
    assert [(w.id, w.period, w.percent) for w in limits.windows] == [
        ("five_hour", "session", 66),
        ("seven_day", "weekly", 35),
        ("weekly_scoped:Fable", "weekly", 0),
        ("extra_usage", "monthly", 20),
    ]


def test_no_plan_limits() -> None:
    assert to_usage_limits(_usage(None, available=False)) is None
