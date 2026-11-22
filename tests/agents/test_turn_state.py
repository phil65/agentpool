"""Turns of different sessions on one native agent instance don't interfere."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from pydantic_ai import ModelRequest, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from agentpool import Agent
from agentpool.agents.events import StreamCompleteEvent
from agentpool.sessions import Session


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from pydantic_ai import ModelMessage


TIMEOUT = 5.0


def _prompt_of(messages: list[ModelMessage]) -> str:
    request = messages[-1]
    assert isinstance(request, ModelRequest)
    texts: list[str] = []
    for part in request.parts:
        if isinstance(part, UserPromptPart):
            content = part.content
            texts.extend([content] if isinstance(content, str) else [str(c) for c in content])
    return " ".join(texts)


async def _final(agent: Agent[None, str], session: Session, prompt: str) -> str:
    async for event in agent.run_stream(prompt, session=session):
        if isinstance(event, StreamCompleteEvent):
            return str(event.message.content)
    return ""


async def test_interrupting_one_session_leaves_the_other_running() -> None:
    started: list[str] = []
    both_started = asyncio.Event()
    release = asyncio.Event()

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        started.append(_prompt_of(messages))
        if len(started) == 2:
            both_started.set()
        await release.wait()
        yield "done"

    agent = Agent(name="shared", model=FunctionModel(stream_function=stream))
    first, second = Session.create("shared"), Session.create("shared")
    async with agent:
        turn_a = asyncio.create_task(_final(agent, first, "a"))
        turn_b = asyncio.create_task(_final(agent, second, "b"))
        await asyncio.wait_for(both_started.wait(), TIMEOUT)
        await agent.interrupt(session_id=first.session_id)
        release.set()
        assert await asyncio.wait_for(turn_b, TIMEOUT) == "done"
        # The interrupted turn ends gracefully, as an interrupted answer
        assert await asyncio.wait_for(turn_a, TIMEOUT) == "[Request interrupted by user]"
    assert sorted(started) == ["a", "b"]


async def test_staged_content_goes_to_its_own_session() -> None:
    prompts: dict[str, str] = {}

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        text = _prompt_of(messages)
        prompts[text.split()[0]] = text
        yield "ok"

    agent = Agent(name="stager", model=FunctionModel(stream_function=stream))
    first, second = Session.create("stager"), Session.create("stager")
    async with agent:
        with agent.use_session(first):
            agent.staged_content.add_text("FOR-FIRST")
        await _final(agent, second, "second turn")
        await _final(agent, first, "first turn")
    assert "FOR-FIRST" not in prompts["second"]
    assert "FOR-FIRST" in prompts["first"]


async def test_active_session_is_the_turns_own_with_parallel_turns() -> None:
    seen: dict[str, str] = {}
    both_inside = asyncio.Event()
    agent_ref: list[Agent[None, str]] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        prompt = _prompt_of(messages)
        seen[prompt] = agent_ref[0].active_session.session_id
        if len(seen) == 2:
            both_inside.set()
        await asyncio.wait_for(both_inside.wait(), TIMEOUT)
        yield "ok"

    agent = Agent(name="ctx", model=FunctionModel(stream_function=stream))
    agent_ref.append(agent)
    first, second = Session.create("ctx"), Session.create("ctx")
    async with agent:
        await asyncio.gather(_final(agent, first, "a"), _final(agent, second, "b"))
        assert agent.active_session is agent.session  # Outside any turn: the default
    assert seen == {"a": first.session_id, "b": second.session_id}


async def test_native_config_options_are_kept_per_session() -> None:
    """Set within a session, tool confirmation and model are that session's."""
    agent = Agent(name="opts", model="test", tool_confirmation_mode="per_tool")
    first, second = Session.create("opts"), Session.create("opts")
    async with agent:
        own_model = agent.model_name
        with agent.use_session(first):
            await agent.set_mode("always", category_id="mode")
            await agent.set_model("openai:gpt-5-mini")
        with agent.use_session(first):
            assert agent.tool_confirmation_mode == "always"
            assert agent.model_name == "openai:gpt-5-mini"
        with agent.use_session(second):
            assert agent.tool_confirmation_mode == "per_tool"
            assert agent.model_name == own_model
        # The agent's own (default session) values are untouched
        assert (agent.tool_confirmation_mode, agent.model_name) == ("per_tool", own_model)
        assert first.overrides.modes == {"mode": "always"}
        assert first.overrides.model == "openai:gpt-5-mini"
        # Without a session, options change the agent, as before sessions
        await agent.set_mode("never", category_id="mode")
        assert agent.tool_confirmation_mode == "never"
        with agent.use_session(first):
            assert agent.tool_confirmation_mode == "always"
