"""Nested self-calls (a tool running its own agent on the same session) leave the turn alone."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

from pydantic_ai import ModelRequest, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from agentpool import Agent, AgentContext, AgentPool


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


def _first_prompt(messages: list[ModelMessage]) -> str:
    return _user_texts(messages)[0]


async def test_concurrent_nested_calls_keep_the_turns_message_id() -> None:
    """Two nested calls overlapping (A ends first) don't leave a nested ID on the turn."""
    a_started, a_done = asyncio.Event(), asyncio.Event()
    seen: dict[str, str | None] = {}

    async def ask_myself(ctx: AgentContext, question: str) -> str:
        """Ask the agent itself."""
        runtime = ctx.agent.runtime()
        seen[f"{question} before"] = runtime.turn_message_id
        answer = await ctx.agent.run(question, store_history=False)
        if question == "a":
            a_done.set()
        seen[f"{question} after"] = runtime.turn_message_id
        return str(answer.content)

    def check(ctx: AgentContext) -> str:
        """Look at the turn's state."""
        seen["check"] = ctx.agent.runtime().turn_message_id
        return "ok"

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[Any]:
        match _first_prompt(messages):
            case "a":
                a_started.set()
                await asyncio.sleep(0.05)  # B starts meanwhile
                yield "answer a"
            case "b":
                await a_started.wait()
                await a_done.wait()  # A ends first
                yield "answer b"
            case _:
                returns = [
                    p
                    for m in messages
                    if isinstance(m, ModelRequest)
                    for p in m.parts
                    if isinstance(p, ToolReturnPart)
                ]
                if not returns:
                    yield {
                        0: DeltaToolCall(
                            name="ask_myself", json_args=json.dumps({"question": "a"})
                        ),
                        1: DeltaToolCall(
                            name="ask_myself", json_args=json.dumps({"question": "b"})
                        ),
                    }
                elif len(returns) == 2:
                    yield {0: DeltaToolCall(name="check", json_args="{}")}
                else:
                    yield "done"

    agent = Agent(name="a", model=FunctionModel(stream_function=model), tools=[ask_myself, check])
    result = await asyncio.wait_for(agent.run("outer"), 5)
    assert result.content == "done"
    outer = seen["a before"]
    assert outer is not None
    assert seen == {
        "a before": outer,
        "b before": outer,  # Not A's (a sibling sees the turn's own ID)
        "a after": outer,
        "b after": outer,
        "check": outer,
    }


async def test_a_nested_run_turn_returns_the_nested_turn() -> None:
    seen: list[str] = []

    async def ask_myself(ctx: AgentContext) -> str:
        """Ask the agent itself."""
        turn = await ctx.agent.run_turn("inner", store_history=False)
        assert turn.result is not None
        seen.append(str(turn.result.content))
        return str(turn.result.content)

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[Any]:
        if _first_prompt(messages) == "outer" and len(messages) == 1:
            yield {0: DeltaToolCall(name="ask_myself", json_args="{}")}
        else:
            yield f"answer to {_first_prompt(messages)}"

    agent = Agent(name="a", model=FunctionModel(stream_function=model), tools=[ask_myself])
    turn = await asyncio.wait_for(agent.run_turn("outer"), 5)
    assert seen == ["answer to inner"]
    assert turn.result is not None
    assert turn.result.content == "answer to outer"


async def test_steering_during_a_nested_call_reaches_the_outer_turn() -> None:
    nested_tool_running, release = asyncio.Event(), asyncio.Event()
    requests: dict[str, list[list[ModelMessage]]] = {"outer": [], "inner": []}

    async def wait_a_bit() -> str:
        """Wait for the test."""
        nested_tool_running.set()
        await release.wait()
        return "waited"

    async def ask_myself(ctx: AgentContext) -> str:
        """Ask the agent itself."""
        answer = await ctx.agent.run("inner", store_history=False)
        return str(answer.content)

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[Any]:
        kind = _first_prompt(messages)
        requests[kind].append(list(messages))
        if len(messages) == 1:
            name = "ask_myself" if kind == "outer" else "wait_a_bit"
            yield {0: DeltaToolCall(name=name, json_args="{}")}
        else:
            yield f"{kind} done"

    agent = Agent(
        name="a", model=FunctionModel(stream_function=model), tools=[ask_myself, wait_a_bit]
    )
    running = asyncio.create_task(agent.run("outer"))
    await asyncio.wait_for(nested_tool_running.wait(), 5)
    await agent.steer("also check the tests")
    release.set()
    await asyncio.wait_for(running, 5)
    inner_texts = [t for r in requests["inner"] for t in _user_texts(r)]
    outer_texts = [t for r in requests["outer"] for t in _user_texts(r)]
    tool_returns = [
        p.model_response_str()
        for r in requests["outer"]
        for m in r
        if isinstance(m, ModelRequest)
        for p in m.parts
        if isinstance(p, ToolReturnPart)
    ]
    assert not any("also check the tests" in t for t in inner_texts)
    assert any("also check the tests" in t for t in [*outer_texts, *tool_returns])


async def test_nested_usage_counts_for_the_turn() -> None:
    inner_usage: list[int] = []

    async def ask_myself(ctx: AgentContext) -> str:
        """Ask the agent itself."""
        answer = await ctx.agent.run("inner", store_history=False)
        inner_usage.append(answer.usage.output_tokens)
        return str(answer.content)

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[Any]:
        if _first_prompt(messages) == "outer" and len(messages) == 1:
            yield {0: DeltaToolCall(name="ask_myself", json_args="{}")}
        else:
            yield "a long answer " * 50

    async with AgentPool() as pool:
        agent = Agent(name="a", model=FunctionModel(stream_function=model), tools=[ask_myself])
        await pool.add_agent(agent)
        outer = await asyncio.wait_for(agent.run("outer"), 5)
        idle = [
            e.data
            for e in pool.activity.since(0).entries
            if e.kind == "state_update" and e.data.get("state") == "idle"
        ]
    assert inner_usage
    assert inner_usage[0] > 0
    assert idle[-1]["usage"]["output_tokens"] == outer.usage.output_tokens + inner_usage[0]
