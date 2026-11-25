"""A Claude Code turn cancelled before the CLI answered (issue #50.1c)."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentpool.agents.claude_code_agent import ClaudeCodeAgent
from agentpool.agents.claude_code_agent.cli_process import CliProcess
from agentpool.agents.claude_code_agent.idle_watch import IdleWatcher
from agentpool.turns import TurnCancelled


pytestmark = pytest.mark.timeout(30)


async def test_cancel_before_the_first_message_ends_the_turn() -> None:
    agent = ClaudeCodeAgent(name="cc", enable_logging=False)
    process = CliProcess("", idle_watch=IdleWatcher(agent))
    queried = asyncio.Event()

    async def query(*args: Any, **kwargs: Any) -> None:
        queried.set()
        await asyncio.Event().wait()  # The CLI never answers

    client = MagicMock()
    client.query = query
    client.session_state = "running"
    client.interrupt = AsyncMock()
    process.client = client
    process.idle_watch = MagicMock(stop=AsyncMock())
    agent._processes[""] = process
    task = asyncio.create_task(agent.run_turn("hi"))
    await asyncio.wait_for(queried.wait(), 10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):  # Not UnboundLocalError
        await asyncio.wait_for(task, 10)
    turn = agent.runtime().last_turn
    assert turn is not None
    assert isinstance(turn.outcome, TurnCancelled)
