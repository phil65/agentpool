"""run_stream_with_commands runs the agent's own commands; everything else is the prompt."""

from __future__ import annotations

from typing import Any

import pytest
from slashed import Command

from agentpool import Agent
from agentpool.agents.events import CommandOutputEvent, StreamCompleteEvent


def _deploy() -> Command:
    async def run(ctx: Any, args: list[str], kwargs: dict[str, str]) -> None:
        await ctx.print(f"deployed {' '.join(args)}")

    return Command.from_raw(run, name="deploy", description="Deploy")


async def _run(agent: Agent[Any], prompt: str) -> tuple[list[str], str | None]:
    """The command output, and what the model replied (None: no turn)."""
    output: list[str] = []
    reply: str | None = None
    async for event in agent.run_stream_with_commands(prompt):
        match event:
            case CommandOutputEvent(output=text):
                output.append(text)
            case StreamCompleteEvent(message=message):
                reply = str(message.content)
    return output, reply


@pytest.fixture
def agent() -> Agent[Any]:
    agent = Agent.from_callback(name="echo", callback=lambda prompt: f"model saw: {prompt}")
    agent.command_store.register_command(_deploy())
    return agent


async def test_own_command_runs_locally(agent: Agent[Any]) -> None:
    output, reply = await _run(agent, "/deploy staging")
    assert "deployed staging" in "".join(output)
    assert reply is None


async def test_backend_command_is_the_prompt(agent: Agent[Any]) -> None:
    # E.g. a Claude Code CLI command: the backend runs it as a turn with tools
    agent.is_backend_command = lambda name: name == "deploy"  # type: ignore[method-assign]
    output, reply = await _run(agent, "/deploy staging")
    assert output == []
    assert reply == "model saw: /deploy staging"


async def test_unknown_command_is_the_prompt(agent: Agent[Any]) -> None:
    output, reply = await _run(agent, "/nonsense please")
    assert output == []
    assert reply == "model saw: /nonsense please"


async def test_closing_the_stream_cancels_the_running_command() -> None:
    import asyncio

    started, cancelled = asyncio.Event(), asyncio.Event()

    async def run(ctx: Any, args: list[str], kwargs: dict[str, str]) -> None:
        await ctx.print("working")
        started.set()
        try:
            await asyncio.Event().wait()  # Runs until cancelled
        except asyncio.CancelledError:
            cancelled.set()
            raise

    agent = Agent.from_callback(name="echo", callback=lambda prompt: prompt)
    agent.command_store.register_command(Command.from_raw(run, name="slow", description="Slow"))
    stream = agent.run_stream_with_commands("/slow")
    async for event in stream:
        if isinstance(event, CommandOutputEvent):
            break
    await stream.aclose()  # type: ignore[attr-defined]  # ty:ignore[unresolved-attribute]
    await asyncio.wait_for(cancelled.wait(), 2)
    assert started.is_set()
