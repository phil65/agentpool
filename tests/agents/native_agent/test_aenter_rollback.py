"""A failed agent setup undoes the steps that succeeded (issue #59.2f)."""

from __future__ import annotations

from typing import Any

import pytest

from agentpool import Agent
from agentpool.messaging.messagenode import MessageNode


pytestmark = pytest.mark.timeout(30)


@pytest.mark.parametrize("parallel", [True, False])
async def test_failed_history_init_exits_the_base_setup(
    monkeypatch: pytest.MonkeyPatch, parallel: bool
) -> None:
    agent = Agent(name="a", model="test", parallel_init=parallel)
    exits: list[Any] = []
    orig_exit = MessageNode.__aexit__

    async def counting_exit(self: Any, *args: Any) -> None:
        exits.append(args[0])
        await orig_exit(self, *args)

    async def failing() -> None:
        raise OSError("history unavailable")

    monkeypatch.setattr(MessageNode, "__aexit__", counting_exit)
    monkeypatch.setattr(agent.conversation, "get_initialization_tasks", lambda: [failing()])
    with pytest.raises(RuntimeError, match="Failed to initialize agent"):
        await agent.__aenter__()
    assert exits == [OSError]


async def test_failed_t3_setup_closes_its_client(monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import AsyncMock, MagicMock

    from agentpool.agents.t3_agent import T3Agent

    agent = T3Agent(project="/work/proj", url="http://localhost:1", enable_logging=False)
    client = MagicMock(__aenter__=AsyncMock(), aclose=AsyncMock())

    async def no_project(client: Any) -> str:
        raise LookupError("no such project")

    monkeypatch.setattr(agent, "_connect", lambda: client)
    monkeypatch.setattr(agent, "_resolve_project", no_project)
    with pytest.raises(LookupError):
        await agent.__aenter__()
    client.aclose.assert_awaited_once()
