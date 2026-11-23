"""Agents reconnect, enable and disable their MCP servers (``/mcp``)."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any

from pydantic_ai.models.test import TestModel
import pytest
from slashed import CommandContext, CommandError

from agentpool import Agent
from agentpool.agents.claude_code_agent import ClaudeCodeAgent
from agentpool.agents.codex_agent import CodexAgent
from agentpool.agents.exceptions import UnsupportedOperationError
from agentpool.common_types import MCPServerStatus
from agentpool_config.mcp_server import StdioMCPServerConfig


SERVER = Path(__file__).parent.parent / "mcp_server" / "changing_server.py"


def _agent() -> Agent[None]:
    config = StdioMCPServerConfig(name="changing", command=sys.executable, args=[str(SERVER)])
    return Agent("worker", model=TestModel(), mcp_servers=[config])


async def _tool_names(agent: Agent[None]) -> set[str]:
    return {tool.name for tool in await agent.tools.get_tools()}


async def _only_server(agent: Agent[None]) -> tuple[str, MCPServerStatus]:
    [(name, status)] = (await agent.get_mcp_server_info()).items()
    return name, status


async def test_a_disabled_server_offers_nothing_until_enabled() -> None:
    async with _agent() as agent:
        name, status = await _only_server(agent)
        assert status.status == "connected"
        assert "grow" in await _tool_names(agent)

        await agent.set_mcp_server_enabled(name, False)
        assert (await _only_server(agent))[1].status == "disabled"
        assert "grow" not in await _tool_names(agent)

        await agent.set_mcp_server_enabled(name, True)
        assert (await _only_server(agent))[1].status == "connected"
        assert "grow" in await _tool_names(agent)


async def test_reconnecting_connects_a_fresh_client() -> None:
    async with _agent() as agent:
        name, _ = await _only_server(agent)
        provider = agent.tools.mcp_providers()[name]
        before = provider.client
        await agent.reconnect_mcp_server(name)
        assert provider.client is not before
        assert (await _only_server(agent))[1].status == "connected"
        assert "grow" in await _tool_names(agent)


async def test_an_unknown_server_is_refused() -> None:
    async with _agent() as agent:
        with pytest.raises(KeyError, match="known: "):
            await agent.reconnect_mcp_server("nope")


class _Output:
    def __init__(self) -> None:
        self.lines: list[str] = []

    async def print(self, message: str) -> None:
        self.lines.append(message)


async def _command(agent: Agent[None], line: str) -> str:
    output = _Output()
    ctx = CommandContext(output=output, data=agent.get_context(), command_store=agent.command_store)
    await agent.command_store.execute_command(line, ctx)
    return "\n".join(output.lines)


async def test_the_mcp_command_shows_and_switches_servers() -> None:
    async with _agent() as agent:
        name, _ = await _only_server(agent)
        assert "connected" in await _command(agent, "mcp")
        assert "disabled" in await _command(agent, f"mcp disable {name}")
        assert "grow" not in await _tool_names(agent)
        assert "now connected" in await _command(agent, f"mcp enable {name}")
        with pytest.raises(CommandError, match="No MCP server"):
            await _command(agent, "mcp reconnect nope")
        with pytest.raises(CommandError, match="Unknown action"):
            await _command(agent, f"mcp restart {name}")


class _FakeCLI:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, bool | None]] = []

    async def mcp_reconnect(self, server_name: str) -> None:
        self.calls.append(("reconnect", server_name, None))

    async def mcp_toggle(self, server_name: str, *, enabled: bool) -> None:
        self.calls.append(("toggle", server_name, enabled))


async def test_claude_code_switches_the_server_in_every_cli_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agentpool.agents.claude_code_agent.cli_process import CliProcess
    from agentpool.agents.claude_code_agent.idle_watch import IdleWatcher

    agent = ClaudeCodeAgent(name="cc")
    clis = [_FakeCLI(), _FakeCLI()]
    for key, cli in zip(["a", "b"], clis, strict=True):
        process = CliProcess(key, idle_watch=IdleWatcher(agent))
        process.client = cli  # type: ignore[assignment]
        agent._processes[key] = process

    async def servers() -> dict[str, MCPServerStatus]:
        return {"github": MCPServerStatus(name="github", status="connected")}

    monkeypatch.setattr(agent, "get_mcp_server_info", servers)
    await agent.set_mcp_server_enabled("github", False)
    await agent.reconnect_mcp_server("github")
    for cli in clis:
        assert cli.calls == [("toggle", "github", False), ("reconnect", "github", None)]
    with pytest.raises(KeyError):
        await agent.reconnect_mcp_server("gitlab")


async def test_codex_reloads_its_servers_but_cannot_switch_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reloads: list[bool] = []

    class _Client:
        async def mcp_server_refresh(self) -> None:
            reloads.append(True)

    agent = CodexAgent(name="cx")

    async def servers() -> dict[str, Any]:
        return {"github": MCPServerStatus(name="github", status="connected")}

    monkeypatch.setattr(agent, "_client", _Client())
    monkeypatch.setattr(agent, "get_mcp_server_info", servers)
    await agent.reconnect_mcp_server("github")
    assert reloads == [True]
    with pytest.raises(UnsupportedOperationError):
        await agent.set_mcp_server_enabled("github", False)
