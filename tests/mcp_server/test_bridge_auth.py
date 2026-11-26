"""The tool bridge only serves callers holding its token; every backend config carries it."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fastmcp import Client
import httpx2
from pydantic_ai.models.test import TestModel
import pytest

from agentpool import Agent
from agentpool.mcp_server.tool_bridge import ToolManagerBridge


ran: list[str] = []


def dangerous(cmd: str) -> str:
    """Run a command (stands in for bash / file writes)."""
    ran.append(cmd)
    return f"ran {cmd}"


_INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "intruder", "version": "0"},
    },
}
_ACCEPT = {"Accept": "application/json, text/event-stream"}


@pytest.mark.parametrize("auth", [None, "Bearer wrong", "Basic abc"])
async def test_requests_without_the_token_are_refused(auth: str | None) -> None:
    ran.clear()
    agent = Agent(name="bridged", model=TestModel(), tools=[dangerous])
    async with agent, ToolManagerBridge(node=agent) as bridge:
        headers = _ACCEPT | ({"Authorization": auth} if auth else {})
        async with httpx2.AsyncClient() as http:
            response = await http.post(bridge.url, json=_INIT, headers=headers)
        assert response.status_code == 401
        with pytest.raises(Exception):  # noqa: B017, PT011
            async with Client(bridge.url) as client:
                await client.call_tool("dangerous", {"cmd": "rm -rf ~"})
    assert not ran


async def test_the_token_opens_the_bridge() -> None:
    ran.clear()
    agent = Agent(name="bridged", model=TestModel(), tools=[dangerous])
    async with agent, ToolManagerBridge(node=agent) as bridge:
        assert bridge.headers == {"Authorization": f"Bearer {bridge.token}"}
        async with Client(bridge.url, auth=bridge.token) as client:
            await client.call_tool("dangerous", {"cmd": "ls"})
    assert ran == ["ls"]


async def test_each_bridge_has_its_own_token() -> None:
    agent = Agent(name="bridged", model=TestModel())
    assert ToolManagerBridge(node=agent).token != ToolManagerBridge(node=agent).token


async def test_bridge_binds_its_port_before_reporting_it() -> None:
    """The port it reports is the one uvicorn serves on (no bind-close-rebind gap)."""
    agent = Agent(name="bridged", model=TestModel())
    async with agent, ToolManagerBridge(node=agent) as bridge:
        assert bridge._server is not None
        assert bridge._server.started
        bound = {s.getsockname()[1] for srv in bridge._server.servers for s in srv.sockets}
        assert bound == {bridge.port}


# --- The configs handed to the backends carry the token -----------------------------


def _bearer(bridge: ToolManagerBridge) -> str:
    return f"Bearer {bridge.token}"


async def test_claude_code_config_carries_the_token() -> None:
    from clawd_code_sdk.models import McpHttpServerConfig

    from agentpool.agents.claude_code_agent import ClaudeCodeAgent

    agent = ClaudeCodeAgent(name="cc")
    async with agent._tool_bridge as bridge:
        cfg = agent._process_mcp_servers()[bridge.resolved_server_name]
        assert isinstance(cfg, McpHttpServerConfig)
        assert cfg.headers == {"Authorization": _bearer(bridge)}


async def test_codex_config_carries_the_token() -> None:
    from agentpool.agents.codex_agent import CodexAgent

    agent = CodexAgent(name="cx", enable_logging=False)
    async with agent._tool_bridge as bridge:
        cfg = agent._bridge_server("")
        assert cfg.http_headers == {"Authorization": _bearer(bridge)}


async def test_acp_config_carries_the_token() -> None:
    from aclientp.schema import AgentCapabilities, HttpMcpServer, McpCapabilities

    from agentpool.agents.acp_agent import ACPAgent
    from agentpool.models.acp_agents.base import ACPAgentConfig

    agent = ACPAgent.from_config(ACPAgentConfig(name="acp", command="true"))
    agent._caps = AgentCapabilities(mcp_capabilities=McpCapabilities(http=True))
    async with agent._tool_bridge as bridge:
        [cfg] = [s for s in agent._session_mcp_servers() if isinstance(s, HttpMcpServer)]
        assert [(h.name, h.value) for h in cfg.headers] == [("Authorization", _bearer(bridge))]


async def test_antigravity_config_carries_the_token() -> None:
    pytest.importorskip("google.antigravity")
    from agentpool.agents.antigravity_agent.antigravity_agent import AntigravityAgent

    agent = AntigravityAgent(name="ag")
    async with agent._tool_bridge as bridge:
        agent._bridge_started = True
        [cfg] = agent._build_mcp_servers()
        assert cfg.headers == {"Authorization": _bearer(bridge)}


@dataclass
class _FakeOpenCode:
    added: dict[str, Any] = field(default_factory=dict)

    async def add_mcp_server(self, name: str, cfg: Any) -> None:
        self.added[name] = cfg

    async def connect_mcp_server(self, name: str) -> None:
        pass


async def test_opencode_config_carries_the_token() -> None:
    from agentpool.agents.opencode_v2_agent.opencode_v2_agent import OpenCodeV2Agent
    from agentpool.resource_providers import StaticResourceProvider

    agent = OpenCodeV2Agent(name="oc", toolsets=[StaticResourceProvider(name="t")])
    client = _FakeOpenCode()
    try:
        await agent._register_mcp_servers(client)  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]
        [cfg] = client.added.values()
        assert cfg.headers == {"Authorization": _bearer(agent._tool_bridge)}
    finally:
        await agent._tool_bridge.stop()


# --- Hardening (second-opinion review of 7a1a0b1b3) ---------------------------------


async def test_bridge_socket_is_not_inherited_by_child_processes() -> None:
    agent = Agent(name="bridged", model=TestModel())
    async with agent, ToolManagerBridge(node=agent) as bridge:
        assert bridge._socket is not None
        assert not bridge._socket.get_inheritable()


def test_token_is_left_out_of_the_repr() -> None:
    bridge = ToolManagerBridge(node=Agent(name="bridged", model=TestModel()))
    assert bridge.token not in repr(bridge)


async def test_a_failed_startup_closes_the_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    import uvicorn

    async def broken_serve(self: uvicorn.Server, sockets: Any = None) -> None:
        raise RuntimeError("startup failed")

    monkeypatch.setattr(uvicorn.Server, "serve", broken_serve)
    agent = Agent(name="bridged", model=TestModel())
    bridge = ToolManagerBridge(node=agent)
    async with agent:
        with pytest.raises(RuntimeError, match="startup failed"):
            await bridge.start()
    assert bridge._socket is None
    assert bridge._server is None
    assert bridge._server_task is None


async def test_a_failing_app_setup_closes_the_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    import uvicorn

    def broken_config(*args: Any, **kwargs: Any) -> None:
        raise ValueError("bad config")

    monkeypatch.setattr(uvicorn, "Config", broken_config)
    agent = Agent(name="bridged", model=TestModel())
    bridge = ToolManagerBridge(node=agent)
    async with agent:
        with pytest.raises(ValueError, match="bad config"):
            await bridge.start()
    assert bridge._socket is None


async def test_websocket_without_the_token_is_closed_not_answered_with_http() -> None:
    from agentpool.utils.network import BearerAuth

    sent: list[dict[str, Any]] = []
    reached: list[bool] = []

    async def app(scope: Any, receive: Any, send: Any) -> None:
        reached.append(True)

    async def receive() -> dict[str, Any]:
        return {"type": "websocket.connect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    scope = {"type": "websocket", "headers": [], "path": "/mcp"}
    await BearerAuth(app, "secret")(scope, receive, send)  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]
    assert not reached
    assert [m["type"] for m in sent] == ["websocket.close"]
