"""A turn cancelled from outside tells the agent's backend to stop, once."""

import asyncio
from contextlib import suppress
from typing import TYPE_CHECKING, Any

from pydantic_ai.models.function import AgentInfo, FunctionModel
import pytest

from agentpool import Agent
from agentpool.agents.events import StreamCompleteEvent
from agentpool.messaging import ChatMessage


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from pydantic_ai import ModelMessage, UserContent

    from agentpool.agents.events import RichAgentStreamEvent
    from agentpool.messaging import MessageHistory
    from agentpool.ui.base import InputProvider


async def _never(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
    await asyncio.Event().wait()
    yield "unreachable"


class ExternalAgent(Agent[None, str]):
    """Stands in for an agent whose turn runs in a backend (CLI, app-server, remote).

    The turn waits until the backend is told to stop (`_interrupt`). On a cancelled task
    it either lets the cancellation through (Codex, OpenCode, T3, ACP) or, with
    ``swallow``, ends the turn with its partial answer (Claude Code).
    """

    def __init__(self, *, swallow: bool) -> None:
        super().__init__(name="external", model=FunctionModel(stream_function=_never))
        self.swallow = swallow
        self.interrupts = 0
        self.started = asyncio.Event()
        self.backend_stopped = asyncio.Event()

    async def _interrupt(self) -> None:
        self.interrupts += 1
        self.backend_stopped.set()

    async def _stream_events(
        self,
        prompts: list[UserContent],
        *,
        user_msg: ChatMessage[Any],
        message_history: MessageHistory,
        effective_parent_id: str | None,
        message_id: str | None = None,
        session_id: str | None = None,
        parent_id: str | None = None,
        input_provider: InputProvider | None = None,
        deps: None = None,
        store_history: bool = True,
    ) -> AsyncIterator[RichAgentStreamEvent[str]]:
        self.started.set()
        try:
            await self.backend_stopped.wait()
        except asyncio.CancelledError:
            if not self.swallow:
                raise
        partial = ChatMessage[str](
            content="partial",
            role="assistant",
            name=self.name,
            session_id=session_id,
            parent_id=user_msg.message_id,
        )
        yield StreamCompleteEvent(message=partial)


async def _start(agent: Agent[None, str], started: asyncio.Event) -> asyncio.Task[Any]:
    task = asyncio.create_task(agent.run("hi"))
    await asyncio.wait_for(started.wait(), timeout=10)
    return task


@pytest.mark.parametrize("swallow", [False, True], ids=["propagates", "swallows"])
async def test_cancelling_a_turn_stops_the_backend(swallow: bool) -> None:
    async with ExternalAgent(swallow=swallow) as agent:
        task = await _start(agent, agent.started)
        task.cancel()
        with suppress(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=10)
        assert agent.interrupts == 1


@pytest.mark.parametrize("swallow", [False, True], ids=["propagates", "swallows"])
async def test_interrupting_a_turn_stops_the_backend_once(swallow: bool) -> None:
    async with ExternalAgent(swallow=swallow) as agent:
        task = await _start(agent, agent.started)
        await agent.interrupt()
        result = await asyncio.wait_for(task, timeout=10)
        assert result.content == "partial"
        assert agent.interrupts == 1


async def test_a_turn_after_a_cancelled_one_runs() -> None:
    async with ExternalAgent(swallow=False) as agent:
        task = await _start(agent, agent.started)
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        agent.started.clear()
        result = await asyncio.wait_for(agent.run("again"), timeout=10)
        assert result.content == "partial"  # The backend was stopped: the turn ends at once
        assert agent.interrupts == 1


class CountingNativeAgent(Agent[None, str]):
    """A native agent counting `_interrupt` calls (it cancels its own turn task)."""

    interrupts = 0

    async def _interrupt(self) -> None:
        self.interrupts += 1
        await super()._interrupt()


async def test_cancelling_a_native_turn_needs_no_backend_stop() -> None:
    started = asyncio.Event()

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        started.set()
        await asyncio.Event().wait()
        yield "unreachable"

    agent = CountingNativeAgent(name="native", model=FunctionModel(stream_function=stream))
    async with agent:
        task = await _start(agent, started)
        task.cancel()
        with suppress(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=10)
        assert agent.interrupts == 0
