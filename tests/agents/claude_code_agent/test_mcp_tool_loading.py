"""Tests for MCP tool loading options of Claude Code agents.

Covers ``always_load`` on MCP server configs, the tool bridge always loading
its tools, and the ``enable_tool_search`` passthrough.
"""

from __future__ import annotations

from clawd_code_sdk.models import McpHttpServerConfig, McpSSEServerConfig, McpStdioServerConfig
from pydantic import HttpUrl
import pytest

from agentpool.agents.claude_code_agent import ClaudeCodeAgent
from agentpool.agents.claude_code_agent.converters import convert_mcp_servers_to_sdk_format
from agentpool.models.claude_code_agents import ClaudeCodeAgentConfig
from agentpool_config.mcp_server import (
    SSEMCPServerConfig,
    StdioMCPServerConfig,
    StreamableHTTPMCPServerConfig,
)


def greet(name: str) -> str:
    """Greet someone by name."""
    return f"Hello, {name}!"


def test_always_load_is_mapped_for_every_transport() -> None:
    servers = [
        StdioMCPServerConfig(name="stdio", command="uvx", args=["srv"], always_load=True),
        SSEMCPServerConfig(name="sse", url=HttpUrl("http://localhost:1/sse"), always_load=True),
        StreamableHTTPMCPServerConfig(name="http", url=HttpUrl("http://localhost:2/mcp")),
    ]
    result = convert_mcp_servers_to_sdk_format(servers)

    stdio = result["stdio"]
    sse = result["sse"]
    http = result["http"]
    assert isinstance(stdio, McpStdioServerConfig)
    assert isinstance(sse, McpSSEServerConfig)
    assert isinstance(http, McpHttpServerConfig)
    assert stdio.alwaysLoad is True
    assert sse.alwaysLoad is True
    # Unset stays None, so the key is omitted from --mcp-config entirely
    assert http.alwaysLoad is None


def test_always_load_defaults_to_false() -> None:
    assert StdioMCPServerConfig(command="uvx").always_load is False


@pytest.mark.parametrize("value", [True, False, "auto", 20, None])
def test_enable_tool_search_reaches_sdk_options(value: bool | int | str | None) -> None:
    config = ClaudeCodeAgentConfig(name="cc", enable_tool_search=value)  # type: ignore[arg-type]
    agent = ClaudeCodeAgent.from_config(config)
    client = agent._get_client()
    assert client.options.enable_tool_search == value


async def test_tool_bridge_tools_are_always_loaded() -> None:
    config = ClaudeCodeAgentConfig(name="cc", tools=[f"{__name__}:greet"])
    agent = ClaudeCodeAgent.from_config(config)
    await agent._setup_toolsets()
    try:
        bridge_cfg = agent._mcp_servers[agent._tool_bridge.resolved_server_name]
        assert isinstance(bridge_cfg, McpHttpServerConfig)
        assert bridge_cfg.alwaysLoad is True
        assert bridge_cfg.url == agent._tool_bridge.url
    finally:
        await agent._tool_bridge.stop()
