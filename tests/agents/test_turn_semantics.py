"""Turns cancelled from outside raise, and a turn is stored before its end is yielded."""

import asyncio
from typing import TYPE_CHECKING, Any

from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
import pytest

from agentpool import Agent
from agentpool.agents.events import StreamCompleteEvent
from agentpool.turns import TurnCancelled


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from pydantic_ai import ModelMessage


pytestmark = pytest.mark.timeout(30)


def _slow(started: asyncio.Event) -> FunctionModel:
    async def slow(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        yield "part"
        started.set()
        await asyncio.sleep(30)
        yield " rest"

    return FunctionModel(stream_function=slow)


async def test_cancelling_the_task_of_run_turn_raises() -> None:
    started = asyncio.Event()
    agent = Agent(name="a", model=_slow(started))
    task = asyncio.create_task(agent.run_turn("hi"))
    await asyncio.wait_for(started.wait(), 10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 10)
    assert task.cancelled()
    # The partial turn is still stored
    assert len(agent.conversation.get_history()) == 2


async def test_wait_for_timeout_of_run_turn_raises() -> None:
    agent = Agent(name="a", model=_slow(asyncio.Event()))
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(agent.run_turn("hi"), 0.5)


async def test_cancelling_the_task_of_run_raises() -> None:
    started = asyncio.Event()
    agent = Agent(name="a", model=_slow(started))
    task = asyncio.create_task(agent.run("hi"))
    await asyncio.wait_for(started.wait(), 10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 10)


async def test_interrupt_still_returns_the_turn() -> None:
    started = asyncio.Event()
    agent = Agent(name="a", model=_slow(started))
    task = asyncio.create_task(agent.run_turn("hi"))
    await asyncio.wait_for(started.wait(), 10)
    await agent.interrupt()
    turn = await asyncio.wait_for(task, 10)
    assert isinstance(turn.outcome, TurnCancelled)
    assert not task.cancelled()


async def test_break_on_stream_complete_keeps_the_turn() -> None:
    agent = Agent(name="a", model=TestModel(custom_output_text="done"))
    sent: list[Any] = []
    turns: list[Any] = []
    agent.message_sent.connect(sent.append)
    agent.turn_completed.connect(turns.append)
    async for event in agent.run_stream("hi"):
        if isinstance(event, StreamCompleteEvent):
            assert len(agent.conversation.get_history()) == 2
            break
    assert len(agent.conversation.get_history()) == 2
    assert len(sent) == 1
    assert len(turns) == 1


async def test_queued_groups_get_their_own_reply_ids() -> None:
    agent = Agent(name="a", model=TestModel(custom_output_text="done"))
    ids: list[str] = []

    async def queue_once(message: Any) -> None:
        if len(ids) == 0:
            agent.queue_prompt("second")
        ids.append(message.message_id)

    agent.message_received.connect(queue_once)
    replies = [
        e.message.message_id
        async for e in agent.run_stream("hi", message_id="reply-1")
        if isinstance(e, StreamCompleteEvent)
    ]
    assert len(replies) == 2
    assert replies[0] == "reply-1"
    assert replies[1] != "reply-1"
