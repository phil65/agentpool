"""Claude plan rate-limit windows -> UsageLimits."""

from datetime import UTC, datetime
import time
from typing import Any

from clawd_code_sdk.models import SDKControlGetUsageResponse
from clawd_code_sdk.models.messages import RateLimitInfo

from agentpool.agents.claude_code_agent import ClaudeCodeAgent
from agentpool.agents.claude_code_agent.usage_limits import (
    CLAUDE_USAGE_URL,
    apply_rate_limit_event,
    to_usage_limits,
)
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
            id="five_hour",
            label="Current session",
            period="session",
            percent=100,
            resets_at=reset,
            limited=True,
        ),
        UsageWindow(
            id="seven_day",
            label="Weekly (all models)",
            period="weekly",
            percent=35,
            resets_at=reset,
        ),
        UsageWindow(
            id="seven_day:Opus",
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
        ("seven_day:Fable", "weekly", 0),
        ("extra_usage", "monthly", 20),
    ]


def test_no_plan_limits() -> None:
    assert to_usage_limits(_usage(None, available=False)) is None


def test_monthly_spend_window() -> None:
    money = {"currency": "USD", "exponent": 2}
    spend = {
        "enabled": True,
        "used": {"amount_minor": 1250, **money},
        "limit": {"amount_minor": 5000, **money},
        "percent": 25,
    }
    limits = to_usage_limits(_usage({"limits": [_row("session", "session", 10)], "spend": spend}))
    assert limits is not None
    assert [(w.id, w.period, w.percent) for w in limits.windows] == [
        ("five_hour", "session", 10),
        ("monthly_spend", "monthly", 25),
    ]


def _event(**info: Any) -> RateLimitInfo:
    return RateLimitInfo.model_validate({"status": "allowed", **info})


def test_rate_limit_events_update_windows() -> None:
    limits = to_usage_limits(
        _usage({"limits": [_row("session", "session", 10), _row("weekly_all", "weekly", 30)]})
    )
    assert limits is not None
    unified = {
        "five_hour": {"utilization": 0.5, "resetsAt": 1795167600, "status": "allowed"},
        "seven_day": {"utilization": 1.0, "resetsAt": 1795352400, "status": "rejected"},
    }
    updated = apply_rate_limit_event(limits, _event(unifiedWindows=unified))
    session, weekly = updated.windows
    assert (session.percent, session.limited) == (50, False)
    assert session.resets_at == datetime.fromtimestamp(1795167600, UTC)
    assert (weekly.percent, weekly.limited) == (100, True)
    # Older events report only the window that changed
    single = apply_rate_limit_event(
        updated, _event(rateLimitType="five_hour", utilization=0.75, resetsAt=1795167600)
    )
    assert [w.percent for w in single.windows] == [75, 100]
    assert apply_rate_limit_event(limits, _event()) is limits


async def test_agent_caches_limits_and_applies_events() -> None:
    agent = ClaudeCodeAgent(name="cc")
    limits = to_usage_limits(_usage({"limits": [_row("session", "session", 10)]}))
    agent._usage_limits = (time.monotonic(), limits)
    agent.record_rate_limit(_event(rateLimitType="five_hour", utilization=0.2))
    cached = await agent.get_usage_limits()  # No CLI needed while the cache is fresh
    assert cached is not None
    assert cached.windows[0].percent == 20
