"""Sessions of one Claude Code agent: a CLI process each, idle ones closed beyond a limit."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from agentpool.agents.claude_code_agent import ClaudeCodeAgent
from agentpool.agents.claude_code_agent.cli_process import CliProcess
from agentpool.agents.claude_code_agent.idle_watch import IdleWatcher
from agentpool.sessions import Session


def _process(agent: ClaudeCodeAgent[Any, Any], key: str, last_used: float) -> CliProcess:
    process = CliProcess(key, idle_watch=IdleWatcher(agent), last_used=last_used)
    process.client = AsyncMock()
    agent._processes[key] = process
    return process


async def test_idle_processes_beyond_the_limit_close_oldest_first() -> None:
    agent = ClaudeCodeAgent(name="cc", max_processes=2, enable_logging=False)
    default = _process(agent, "", 0)
    old, busy, recent, new = (
        _process(agent, key, used) for key, used in (("a", 1), ("b", 2), ("c", 3), ("d", 4))
    )
    busy.turns = 1
    await agent._close_idle_processes(keep=new)
    # Explicit: a, b, c, d (4, limit 2): the two oldest idle ones go (b runs a turn)
    assert set(agent._processes) == {"", "b", "d"}
    for closed in (old, recent):
        assert closed.client is None
    assert default.client is not None  # The default session's process always stays


async def test_a_connecting_process_is_not_idle() -> None:
    agent = ClaudeCodeAgent(name="cc", enable_logging=False)
    process = _process(agent, "a", 0)
    assert process.idle
    connected = asyncio.Event()
    process.connection_task = asyncio.create_task(connected.wait())
    assert not process.idle
    connected.set()
    await process.connection_task
    assert process.idle
    process.turns = 1
    assert not process.idle


async def test_each_session_acts_on_its_own_process_and_settings() -> None:
    agent = ClaudeCodeAgent(name="cc", model="opus", enable_logging=False)
    first, second = Session.create("cc"), Session.create("cc")
    for session in (first, second):
        with agent.use_session(session):
            client = AsyncMock()
            client.get_context_usage.return_value = SimpleNamespace(model="m", raw_max_tokens=0)
            agent._client = client
    with agent.use_session(first):
        await agent.set_model("haiku")
        agent._client.set_model.assert_awaited_once_with("haiku")  # type: ignore[union-attr]
        assert agent.model_name == "haiku"
    with agent.use_session(second):
        agent._client.set_model.assert_not_awaited()  # type: ignore[union-attr]
        assert agent.model_name == "opus"
    assert agent.model_name == "opus"  # The agent's own
    assert set(agent._processes) == {first.session_id, second.session_id}


async def test_steering_goes_to_the_session_named() -> None:
    from agentpool.agents.claude_code_agent.steering import RunSteering

    agent = ClaudeCodeAgent(name="cc", enable_logging=False)
    first, second = Session.create("cc"), Session.create("cc")
    steerings: dict[str, AsyncMock] = {}
    for session in (first, second):
        with agent.use_session(session):
            steering = AsyncMock(spec=RunSteering, closed=False)
            agent._steering = steering
            steerings[session.session_id] = steering
        agent.runtime(session)  # Its turn runs (a runtime exists)
    await agent.steer("use pytest", session_id=second.session_id)
    steerings[first.session_id].send.assert_not_awaited()
    steerings[second.session_id].send.assert_awaited_once_with("use pytest", priority="next")
