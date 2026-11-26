"""The ACP session offers the commands of its agent, not only its own."""

import asyncio
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

from aclientp import ClientCapabilities
from slashed import Command

from agentpool import Agent, AgentPool
from agentpool_server.acp_server.session import ACPSession, split_commands


if TYPE_CHECKING:
    from aclientp.schema import AvailableCommand


def _command(name: str, output: str) -> Command:
    async def run(ctx: Any, args: list[str], kwargs: dict[str, str]) -> None:
        await ctx.print(output)

    return Command.from_raw(run, name=name, description=f"{name} command")


def _session(*agent_names: str) -> tuple[ACPSession, dict[str, Agent[Any]], list[list[str]]]:
    """A session on the first of ``agent_names``, and the command lists sent to the client."""
    pool = AgentPool()
    agents: dict[str, Agent[Any]] = {}
    for name in agent_names:
        agents[name] = Agent.from_callback(name=name, callback=lambda m: m, agent_pool=pool)
        pool.register(name, agents[name])
    session = ACPSession(
        session_id="s",
        agent=agents[agent_names[0]],
        cwd="/tmp",
        client=AsyncMock(),
        acp_agent=AsyncMock(),
        client_capabilities=ClientCapabilities(fs=None, terminal=False),
    )
    sent: list[list[str]] = []

    async def capture(commands: list[AvailableCommand]) -> None:
        sent.append([c.name for c in commands])

    session.notifications.update_commands = capture  # type: ignore[method-assign]
    return session, agents, sent


async def test_agent_commands_are_listed_and_followed() -> None:
    session, agents, sent = _session("main")
    agent = agents["main"]
    assert session.agent is agent
    # A burst of changes (e.g. all CLI commands at connect) sends one update
    agent.command_store.register_command(_command("deploy", "deployed"))
    agent.command_store.register_command(_command("rollback", "rolled back"))
    await asyncio.sleep(0.01)
    assert len(sent) == 1
    assert {"deploy", "rollback"} <= set(sent[0])
    # The session's own commands are listed once, also when the agent has them too
    assert len(sent[0]) == len(set(sent[0]))


async def test_agent_command_runs_through_the_agent() -> None:
    session, agents, _ = _session("main")
    agents["main"].command_store.register_command(_command("deploy", "deployed"))
    output: list[str] = []

    async def capture_text(message: str, **_: Any) -> None:
        output.append(message)

    session.notifications.send_agent_text = capture_text  # type: ignore[method-assign]
    commands, rest = split_commands(
        ["/deploy now"], lambda name: session._command_store_for(name) is not None
    )
    assert (commands, rest) == (["/deploy now"], [])
    await session.execute_slash_command("/deploy now")
    assert "deployed" in "".join(output)


async def test_backend_commands_go_to_the_agent_as_prompt() -> None:
    session, agents, _ = _session("main")
    agent = agents["main"]
    agent.command_store.register_command(_command("context", "never printed here"))
    agent.is_backend_command = lambda name: name == "context"  # type: ignore[method-assign]

    commands, rest = split_commands(
        ["/context"], lambda name: session._command_store_for(name) is not None
    )

    assert (commands, rest) == ([], ["/context"])
    assert "context" in [c.name for c in session.list_commands()]  # Still listed


async def test_switching_agents_follows_the_new_agent() -> None:
    session, agents, sent = _session("first", "second")
    await session.switch_active_agent("second")
    await asyncio.sleep(0.01)
    sent.clear()

    agents["first"].command_store.register_command(_command("old", "x"))
    await asyncio.sleep(0.01)
    assert sent == []
    agents["second"].command_store.register_command(_command("new", "x"))
    await asyncio.sleep(0.01)
    assert len(sent) == 1
    assert "new" in sent[0]
    assert "old" not in sent[0]
