"""A Codex agent whose start fails closes its client (issue #50.3b)."""

from typing import Any, Self

import pytest

from agentpool.agents.codex_agent import CodexAgent


class _FailingClient:
    instances: list[_FailingClient] = []  # noqa: RUF012

    def __init__(self, **kwargs: Any) -> None:
        self.closed = False
        _FailingClient.instances.append(self)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        self.closed = True

    async def thread_start(self, **kwargs: Any) -> Any:
        raise RuntimeError("thread/start failed")


async def test_a_failed_thread_start_closes_the_client(monkeypatch: pytest.MonkeyPatch) -> None:
    import codexed

    monkeypatch.setattr(codexed, "CodexClient", _FailingClient)
    agent = CodexAgent(name="cx")
    with pytest.raises(RuntimeError, match="thread/start failed"):
        await agent.__aenter__()
    [client] = _FailingClient.instances
    assert client.closed
    assert agent._client is None
