"""Slash commands run in the caller's session, with their own output stream (#59.2a/b)."""

import asyncio
from typing import Any

import pytest
from slashed import Command

from agentpool import Agent
from agentpool.agents.events import CommandOutputEvent
from agentpool.sessions import Session


pytestmark = pytest.mark.timeout(30)


async def _outputs(agent: Agent[Any], prompt: str, **kwargs: Any) -> list[str]:
    return [
        event.output
        async for event in agent.run_stream_with_commands(prompt, **kwargs)
        if isinstance(event, CommandOutputEvent)
    ]


async def test_commands_run_in_the_given_session() -> None:
    agent = Agent.from_callback(name="echo", callback=lambda prompt: prompt)
    seen: list[str] = []

    async def where(ctx: Any, args: list[str], kwargs: dict[str, str]) -> None:
        seen.append(agent.active_session.session_id)

    agent.command_store.register_command(Command.from_raw(where, name="where"))
    session = Session.create("echo")
    await _outputs(agent, "/where", session=session)
    assert seen == [session.session_id]


async def test_concurrent_commands_keep_their_own_output() -> None:
    agent = Agent.from_callback(name="echo", callback=lambda prompt: prompt)
    both_started = asyncio.Barrier(2)

    async def say(ctx: Any, args: list[str], kwargs: dict[str, str]) -> None:
        await both_started.wait()
        await ctx.print(args[0])

    agent.command_store.register_command(Command.from_raw(say, name="say"))
    first, second = await asyncio.gather(_outputs(agent, "/say one"), _outputs(agent, "/say two"))
    assert "".join(first) == "one"
    assert "".join(second) == "two"
