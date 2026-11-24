"""Every agent turn ends with a ``Turn``: on the final event, announced, from ``run_turn``."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from pydantic_ai import UsageLimitExceeded
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
import pytest

from agentpool import Agent, AgentPool
from agentpool.agents.events import StreamCompleteEvent
from agentpool.turns import (
    Turn,
    TurnCancelled,
    TurnCompleted,
    TurnFailed,
    TurnLimitReached,
)


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from pydantic_ai import ModelMessage


def _recording(agent: Agent[Any, Any]) -> list[Turn[Any]]:
    turns: list[Turn[Any]] = []

    async def on_turn(turn: Turn[Any]) -> None:
        turns.append(turn)

    agent.turn_completed.connect(on_turn)
    return turns


def _failing(error: Exception) -> FunctionModel:
    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        raise error
        yield ""

    return FunctionModel(stream_function=stream)


async def test_a_completed_turn() -> None:
    agent = Agent(name="a", model=TestModel(custom_output_text="done"))
    announced = _recording(agent)
    events = [e async for e in agent.run_stream("hi")]
    [complete] = [e for e in events if isinstance(e, StreamCompleteEvent)]
    turn = complete.turn
    assert turn is not None
    assert isinstance(turn.outcome, TurnCompleted)
    assert turn.result is complete.message
    assert turn.prompt.role == "user"
    assert turn.turn_id == turn.prompt.message_id == complete.message.parent_id
    assert (turn.node, turn.usage) == ("a", complete.message.usage)
    assert turn.duration >= 0
    assert announced == [turn]


async def test_run_turn_returns_the_turn() -> None:
    agent = Agent(name="a", model=TestModel(custom_output_text="done"))
    turn = await agent.run_turn("hi")
    assert turn.completed
    assert turn.result is not None
    assert turn.result.content == "done"


async def test_a_failed_turn_is_announced_and_returned() -> None:
    agent = Agent(name="a", model=_failing(RuntimeError("model down")))
    announced = _recording(agent)
    with pytest.raises(RuntimeError, match="model down"):
        await agent.run("hi")  # run() raises as before
    turn = await agent.run_turn("hi")
    assert isinstance(turn.outcome, TurnFailed)
    assert str(turn.outcome.error) == "model down"
    assert turn.result is None
    assert [type(t.outcome) for t in announced] == [TurnFailed, TurnFailed]


async def test_a_usage_limit_ends_the_turn_as_limit_reached() -> None:
    agent = Agent(name="a", model=_failing(UsageLimitExceeded("too many requests")))
    turn = await agent.run_turn("hi")
    assert isinstance(turn.outcome, TurnLimitReached)
    assert "too many requests" in turn.outcome.message


async def test_an_interrupted_turn_is_cancelled_with_its_partial_answer() -> None:
    started = asyncio.Event()

    async def slow(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        yield "part"
        started.set()
        await asyncio.sleep(30)
        yield " rest"

    agent = Agent(name="a", model=FunctionModel(stream_function=slow))
    running = asyncio.create_task(agent.run_turn("hi"))
    await asyncio.wait_for(started.wait(), 5)
    await agent.interrupt()
    turn = await asyncio.wait_for(running, 5)
    assert isinstance(turn.outcome, TurnCancelled)
    assert turn.result is not None  # The native agent answers with what it had


async def test_the_activity_feed_takes_idle_from_the_outcome() -> None:
    async with AgentPool() as pool:
        agent = Agent(name="a", model=_failing(RuntimeError("model down")))
        await pool.add_agent(agent)
        await agent.run_turn("hi")
        states = [
            e.data
            for e in pool.activity.since(0).entries
            if e.kind == "state_update" and e.data.get("state") == "idle"
        ]
    assert states[-1]["stop_reason"] == "error"


async def test_a_subagent_turn_is_a_child_of_the_calling_turn() -> None:
    import json

    from pydantic_ai.models.function import DeltaToolCall

    from agentpool_config.toolsets import SubagentToolsetConfig

    async def lead(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[Any]:
        if len(messages) == 1:
            args = {"agent_or_team": "helper", "prompt": "say hi", "description": "Greet"}
            yield {0: DeltaToolCall(name="task", json_args=json.dumps(args), tool_call_id="c1")}
        else:
            yield "delegated"

    async with AgentPool() as pool:
        helper = Agent(name="helper", model=TestModel(custom_output_text="hi"))
        await pool.add_agent(helper)
        orchestrator = Agent(
            name="lead",
            model=FunctionModel(stream_function=lead),
            toolsets=[SubagentToolsetConfig().get_provider()],
        )
        await pool.add_agent(orchestrator)
        helper_turns = _recording(helper)
        turn = await orchestrator.run_turn("go")
    [child] = helper_turns
    assert child.parent == turn.ref
    assert turn.children == [child]
    assert turn.parent is None


async def test_a_routed_turn_has_its_source_turn_as_parent() -> None:
    async with AgentPool() as pool:
        source = Agent(name="source", model=TestModel(custom_output_text="for you"))
        target = Agent(name="target", model=TestModel(custom_output_text="got it"))
        await pool.add_agent(source)
        await pool.add_agent(target)
        source.connect_to(target).wait_for_connections = True  # The run waits for it
        target_turns = _recording(target)
        turn = await source.run_turn("start")
    [routed] = target_turns
    assert routed.parent == turn.ref


async def test_the_answer_keeps_the_turns_outcome_and_parent() -> None:
    from agentpool.turns import TURN_METADATA_KEY

    agent = Agent(name="a", model=TestModel(custom_output_text="done"))
    turn = await agent.run_turn("hi")
    assert turn.result is not None
    assert turn.result.metadata[TURN_METADATA_KEY] == {"outcome": "completed", "parent": None}
