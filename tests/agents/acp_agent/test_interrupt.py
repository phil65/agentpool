"""Tests for interrupting an ACP agent's prompt turn."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from agentpool.agents.acp_agent import acp_agent as acp_agent_module
from agentpool.agents.acp_agent.acp_agent import ACPAgent


def _agent_with_prompt(prompt_task: asyncio.Task[object], cancel: AsyncMock) -> ACPAgent:
    agent = ACPAgent(command="true")
    agent._api = AsyncMock(cancel=cancel)
    agent._open_state("remote").prompt_task = prompt_task
    return agent


async def test_interrupt_waits_for_graceful_cancel() -> None:
    cancelled_by_agent = asyncio.Event()

    async def prompt() -> str:
        await cancelled_by_agent.wait()
        return "cancelled"

    task = asyncio.create_task(prompt())
    agent = _agent_with_prompt(task, AsyncMock(side_effect=lambda _sid: cancelled_by_agent.set()))
    await agent._interrupt()
    assert task.done()
    assert not task.cancelled()
    assert task.result() == "cancelled"


async def test_interrupt_forces_cancel_after_grace_period(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(acp_agent_module, "CANCEL_GRACE_PERIOD", 0.01)
    task = asyncio.create_task(asyncio.sleep(10))
    agent = _agent_with_prompt(task, AsyncMock())
    await agent._interrupt()
    with pytest.raises(asyncio.CancelledError):
        await task
