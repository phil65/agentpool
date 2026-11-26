"""Concurrent runs on one session (#59.1c): queue, steer, interrupt + run, nested self-calls."""

import asyncio
import json
from typing import TYPE_CHECKING, Any

from pydantic_ai import ModelRequest, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel
import pytest

from agentpool import Agent, AgentContext, AgentPool
from agentpool.turns import TurnCancelled


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from pydantic_ai import ModelMessage


def _user_texts(messages: list[ModelMessage]) -> list[str]:
    return [
        part.content if isinstance(part.content, str) else "".join(map(str, part.content))
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart)
    ]


async def test_a_second_run_queues_until_the_running_turn_finished() -> None:
    """Default ``mode="queue"``: the second run starts once the first one ended."""
    release = asyncio.Event()
    log: list[str] = []

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        prompt = _user_texts(messages)[-1]
        log.append(f"start {prompt}")
        if prompt == "first":
            await release.wait()
        log.append(f"end {prompt}")
        yield " | ".join(_user_texts(messages))

    async with AgentPool() as pool:
        agent = Agent(name="a", model=FunctionModel(stream_function=model))
        await pool.add_agent(agent)
        session = pool.sessions.create("a")
        first = asyncio.create_task(agent.run("first", session=session))
        while not log:
            await asyncio.sleep(0.01)
        second = asyncio.create_task(agent.run("second", session=session))
        await asyncio.sleep(0.1)
        assert log == ["start first"]  # The second run waits
        assert agent.runtime(session).waiting == 1
        release.set()
        first_answer, second_answer = await asyncio.wait_for(asyncio.gather(first, second), 5)
    assert log == ["start first", "end first", "start second", "end second"]
    assert first_answer.content == "first"
    assert second_answer.content == "first | second"  # A new turn after the first one


async def test_steer_injects_into_the_running_turn_at_the_next_step() -> None:
    """``mode="steer"``: the message reaches the model with the running tool call's result."""
    tool_started = asyncio.Event()
    release = asyncio.Event()
    requests: list[list[ModelMessage]] = []

    async def wait_a_bit() -> str:
        """Wait for the test."""
        tool_started.set()
        await release.wait()
        return "waited"

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[Any]:
        requests.append(list(messages))
        if len(requests) == 1:
            yield {0: DeltaToolCall(name="wait_a_bit", json_args="{}", tool_call_id="c1")}
        else:
            yield "done"

    agent = Agent(name="a", model=FunctionModel(stream_function=model), tools=[wait_a_bit])
    running = asyncio.create_task(agent.run("work"))
    await asyncio.wait_for(tool_started.wait(), 5)
    steered = asyncio.create_task(agent.run("also check the tests", mode="steer"))
    await asyncio.sleep(0.05)
    release.set()
    answer, steered_answer = await asyncio.wait_for(asyncio.gather(running, steered), 5)
    assert len(requests) == 2  # One turn: the steered message went into it
    second_request = requests[1][-1]
    assert isinstance(second_request, ModelRequest)
    assert any(isinstance(p, ToolReturnPart) for p in second_request.parts)
    assert any("also check the tests" in t for t in _user_texts([second_request]))
    assert steered_answer is answer  # The answer of the turn that took the message in


async def test_steer_falls_back_to_queue_for_non_text_prompts() -> None:
    from pydantic_ai import BinaryContent

    release = asyncio.Event()
    calls: list[list[str]] = []

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        calls.append(_user_texts(messages))
        if len(calls) == 1:
            await release.wait()
        yield "ok"

    agent = Agent(name="a", model=FunctionModel(stream_function=model))
    running = asyncio.create_task(agent.run("first"))
    while not calls:
        await asyncio.sleep(0.01)
    image = BinaryContent(data=b"\x89PNG", media_type="image/png")
    queued = asyncio.create_task(agent.run(image, mode="steer"))
    await asyncio.sleep(0.05)
    assert agent.runtime().waiting == 1  # Not steerable: queued
    release.set()
    await asyncio.wait_for(asyncio.gather(running, queued), 5)
    assert len(calls) == 2


async def test_interrupt_then_run_replaces_the_running_turn() -> None:
    started = asyncio.Event()

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        if _user_texts(messages)[-1] == "slow":
            started.set()
            await asyncio.sleep(30)
        yield "fast answer"

    agent = Agent(name="a", model=FunctionModel(stream_function=model))
    running = asyncio.create_task(agent.run_turn("slow"))
    await asyncio.wait_for(started.wait(), 5)
    await agent.interrupt()
    replacement = await asyncio.wait_for(agent.run("fast"), 5)
    turn = await asyncio.wait_for(running, 5)
    assert isinstance(turn.outcome, TurnCancelled)
    assert replacement.content == "fast answer"


async def test_a_nested_self_call_from_the_running_turn_does_not_wait() -> None:
    """A tool running its own agent on the same session runs nested (no deadlock)."""

    async def ask_myself(ctx: AgentContext, question: str) -> str:
        """Ask the agent itself."""
        answer = await ctx.agent.run(question, store_history=False)
        return str(answer.content)

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[Any]:
        texts = _user_texts(messages)
        if texts[-1] == "outer" and len(messages) == 1:
            args = json.dumps({"question": "inner"})
            yield {0: DeltaToolCall(name="ask_myself", json_args=args, tool_call_id="c1")}
        elif texts[-1] == "inner":
            yield "inner answer"
        else:
            yield "outer answer"

    agent = Agent(name="a", model=FunctionModel(stream_function=model), tools=[ask_myself])
    answer = await asyncio.wait_for(agent.run("outer"), 5)
    assert answer.content == "outer answer"


@pytest.mark.parametrize("mode", ["queue", "steer"])
async def test_runs_of_different_sessions_do_not_wait_for_each_other(mode: str) -> None:
    release = asyncio.Event()
    started: list[str] = []

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        started.append(_user_texts(messages)[-1])
        await release.wait()
        yield "ok"

    async with AgentPool() as pool:
        agent = Agent(name="a", model=FunctionModel(stream_function=model))
        await pool.add_agent(agent)
        one, two = pool.sessions.create("a"), pool.sessions.create("a")
        runs = [
            asyncio.create_task(agent.run("one", session=one)),
            asyncio.create_task(agent.run("two", session=two, mode=mode)),  # type: ignore[arg-type]
        ]
        for _ in range(100):
            if len(started) == 2:
                break
            await asyncio.sleep(0.01)
        assert sorted(started) == ["one", "two"]
        release.set()
        await asyncio.wait_for(asyncio.gather(*runs), 5)


async def test_a_nested_self_call_keeps_the_outer_turns_state() -> None:
    """The nested run runs its own prompt only; the outer turn keeps its queue and task."""
    seen: list[list[str]] = []
    state: dict[str, Any] = {}

    async def ask_myself(ctx: AgentContext, question: str) -> str:
        """Ask the agent itself."""
        runtime = ctx.agent.runtime()
        ctx.agent.queue_prompt("follow-up")
        task, message_id = runtime.task, runtime.turn_message_id
        answer = await ctx.agent.run(question, store_history=False)
        state["task kept"] = runtime.task is task is not None
        state["message id kept"] = runtime.turn_message_id == message_id is not None
        return str(answer.content)

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[Any]:
        texts = _user_texts(messages)
        seen.append(texts)
        if texts[-1] == "outer" and len(messages) == 1:
            args = json.dumps({"question": "inner"})
            yield {0: DeltaToolCall(name="ask_myself", json_args=args, tool_call_id="c1")}
        else:
            yield f"answer to {texts[-1]}"

    agent = Agent(name="a", model=FunctionModel(stream_function=model), tools=[ask_myself])
    await asyncio.wait_for(agent.run("outer"), 5)
    assert seen == [["outer"], ["inner"], ["outer"], ["outer", "follow-up"]]
    assert state == {"task kept": True, "message id kept": True}
