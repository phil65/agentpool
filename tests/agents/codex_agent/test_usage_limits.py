"""ChatGPT plan rate-limit windows of the Codex app-server."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from codexed.exceptions import CodexRequestError
from codexed.models import RateLimitSnapshot

from agentpool.agents.codex_agent import CodexAgent
from agentpool.agents.codex_agent.usage_limits import CODEX_USAGE_URL, to_usage_limits


SNAPSHOT = RateLimitSnapshot.model_validate({
    "planType": "pro",
    "primary": {"usedPercent": 42, "windowDurationMins": 300, "resetsAt": 1795170000},
    "secondary": {"usedPercent": 100, "windowDurationMins": 10080, "resetsAt": 1795700000},
})


def test_snapshot_to_usage_limits() -> None:
    limits = to_usage_limits(SNAPSHOT)
    assert (limits.plan, limits.usage_url) == ("pro", CODEX_USAGE_URL)
    session, weekly = limits.windows
    assert (session.id, session.label, session.period, session.percent) == (
        "primary",
        "Current session",
        "session",
        42,
    )
    assert session.resets_at == datetime.fromtimestamp(1795170000, UTC)
    assert (weekly.period, weekly.limited) == ("weekly", True)


class _Account:
    def __init__(self, error: Exception | None = None) -> None:
        self.rate_limits: RateLimitSnapshot | None = None
        self.reads = 0
        self._error = error

    async def rate_limits_read(self) -> Any:
        self.reads += 1
        if self._error is not None:
            raise self._error
        self.rate_limits = SNAPSHOT


class _Client:
    def __init__(self, account: _Account) -> None:
        self.account = account


async def test_agent_reads_limits_once_then_uses_updates() -> None:
    agent = CodexAgent(name="codex")
    account = _Account()
    agent._client = _Client(account)  # type: ignore[assignment]  # ty:ignore[invalid-assignment]
    first = await agent.get_usage_limits()
    second = await agent.get_usage_limits()
    assert first == second
    assert first is not None
    assert account.reads == 1  # Notifications keep the snapshot current in between


async def test_api_key_accounts_have_no_plan_limits() -> None:
    agent = CodexAgent(name="codex")
    error = CodexRequestError(-32600, "chatgpt authentication required to read rate limits")
    agent._client = _Client(_Account(error))  # type: ignore[assignment]  # ty:ignore[invalid-assignment]
    assert await agent.get_usage_limits() is None
