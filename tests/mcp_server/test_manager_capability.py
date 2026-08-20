"""Tests for MCPManager.get_capabilities() and MCP migration coverage."""

from __future__ import annotations

import inspect
from typing import Any
from unittest.mock import AsyncMock, patch

from pydantic import HttpUrl
from pydantic_ai.mcp import MCPToolset
import pytest

from wolfharness.capabilities.mcp_server_cap import McpServerCap
from wolfharness.mcp_server.manager import MCPManager, _make_elicitation_handler
from wolfharness_config.mcp_server import (
    AcpMCPServerConfig,
    SSEMCPServerConfig,
    StdioMCPServerConfig,
    StreamableHTTPMCPServerConfig,
)


pytestmark = pytest.mark.unit


# =============================================================================
# get_capabilities() tests
# =============================================================================


async def test_resource_provider_registration_requires_negotiated_capability() -> None:
    """Tool providers remain connected when only some advertise resources."""
    resource_config = StdioMCPServerConfig(name="resource", command="resource-server")
    tools_only_config = StdioMCPServerConfig(name="tools", command="tools-server")
    resource_provider = McpServerCap(resource_config)
    tools_only_provider = McpServerCap(tools_only_config)
    manager = MCPManager(servers=[resource_config, tools_only_config])
    manager.providers.extend([tools_only_provider, resource_provider])

    with (
        patch.object(
            resource_provider,
            "supports_resources",
            new=AsyncMock(return_value=True),
        ),
        patch.object(
            tools_only_provider,
            "supports_resources",
            new=AsyncMock(return_value=False),
        ),
    ):
        resource_providers = await manager.get_resource_providers()

    assert resource_providers == [resource_provider]
    assert manager.get_mcp_providers() == [tools_only_provider, resource_provider]


async def test_resource_provider_with_empty_catalog_stays_registered() -> None:
    """Capability negotiation, not the current resource count, controls registration."""
    config = StdioMCPServerConfig(name="empty-resource-server", command="server")
    provider = McpServerCap(config)
    manager = MCPManager(servers=[config])
    manager.providers.append(provider)

    with patch.object(
        provider,
        "supports_resources",
        new=AsyncMock(return_value=True),
    ):
        resource_providers = await manager.get_resource_providers()

    assert resource_providers == [provider]


async def test_empty_servers_returns_empty_list() -> None:
    """An MCPManager with no servers should return an empty list."""
    manager = MCPManager(servers=[])
    caps = await manager.get_capabilities()
    assert caps == []


async def test_single_stdio_server() -> None:
    """A single stdio server should produce one MCP capability."""
    config = StdioMCPServerConfig(
        name="test_stdio",
        command="python",
        args=["-m", "my_server"],
        env={"FOO": "bar"},
        timeout=30.0,
    )
    manager = MCPManager(servers=[config])

    caps = await manager.get_capabilities()

    assert len(caps) == 1
    cap = caps[0]
    assert cap.url == "mcp://stdio/python_-m my_server"
    assert cap.id == "test_stdio"
    assert cap.allowed_tools is None
    assert isinstance(cap.local, MCPToolset)


async def test_single_sse_server() -> None:
    """A single SSE server should produce one MCP capability with the URL."""
    config = SSEMCPServerConfig(
        name="test_sse",
        url=HttpUrl("http://localhost:8080/sse"),
        headers={"Authorization": "Bearer token"},
        timeout=45.0,
    )
    manager = MCPManager(servers=[config])

    caps = await manager.get_capabilities()

    assert len(caps) == 1
    cap = caps[0]
    assert cap.url == "http://localhost:8080/sse"
    assert cap.id == "test_sse"
    assert isinstance(cap.local, MCPToolset)


async def test_single_streamable_http_server() -> None:
    """A single StreamableHTTP server should produce one MCP capability."""
    config = StreamableHTTPMCPServerConfig(
        name="test_http",
        url=HttpUrl("https://api.example.com/mcp"),
        headers={"X-Api-Key": "secret"},
        timeout=60.0,
    )
    manager = MCPManager(servers=[config])

    caps = await manager.get_capabilities()

    assert len(caps) == 1
    cap = caps[0]
    assert cap.url == "https://api.example.com/mcp"
    assert cap.id == "test_http"
    assert isinstance(cap.local, MCPToolset)


async def test_multiple_servers() -> None:
    """Multiple servers should produce multiple capabilities."""
    stdio_cfg = StdioMCPServerConfig(command="python", args=["server.py"])
    sse_cfg = SSEMCPServerConfig(url=HttpUrl("http://localhost:8080/sse"))
    manager = MCPManager(servers=[stdio_cfg, sse_cfg])

    caps = await manager.get_capabilities()

    assert len(caps) == 2
    urls = {c.url for c in caps}
    assert urls == {"mcp://stdio/python_server.py", "http://localhost:8080/sse"}


async def test_disabled_server_is_skipped() -> None:
    """Disabled servers should not produce capabilities."""
    enabled = StdioMCPServerConfig(command="python", args=["enabled.py"])
    disabled = StdioMCPServerConfig(command="python", args=["disabled.py"], enabled=False)
    manager = MCPManager(servers=[enabled, disabled])

    caps = await manager.get_capabilities()

    assert len(caps) == 1
    assert caps[0].id == "python_enabled.py"


async def test_acp_server_is_skipped() -> None:
    """ACP transport servers should be skipped (not supported by pydantic-ai)."""
    stdio = StdioMCPServerConfig(command="python", args=["server.py"])
    acp = AcpMCPServerConfig(acp_id="my-acp-server")
    manager = MCPManager(servers=[stdio, acp])

    caps = await manager.get_capabilities()

    assert len(caps) == 1
    assert caps[0].id == "python_server.py"


async def test_allowed_tools_passed_through() -> None:
    """enabled_tools from config should be passed to the capability."""
    config = StdioMCPServerConfig(
        command="python",
        args=["server.py"],
        enabled_tools=["read_file", "list_directory"],
    )
    manager = MCPManager(servers=[config])

    caps = await manager.get_capabilities()

    assert len(caps) == 1
    assert caps[0].allowed_tools == ["read_file", "list_directory"]


async def test_capability_is_abstract_capability() -> None:
    """Returned capabilities should be instances of AbstractCapability."""
    from pydantic_ai.capabilities import AbstractCapability

    config = StdioMCPServerConfig(command="echo", args=["hello"])
    manager = MCPManager(servers=[config])

    caps = await manager.get_capabilities()

    assert len(caps) == 1
    assert isinstance(caps[0], AbstractCapability)


async def test_server_without_name_uses_client_id() -> None:
    """When server name is not set, client_id should be used as capability id."""
    config = StdioMCPServerConfig(command="python", args=["server.py"])
    manager = MCPManager(servers=[config])

    caps = await manager.get_capabilities()

    assert len(caps) == 1
    assert caps[0].id == "python_server.py"


async def test_does_not_modify_manager_state() -> None:
    """get_capabilities() should be a pure read-only operation."""
    config = StdioMCPServerConfig(command="python", args=["server.py"])
    manager = MCPManager(servers=[config])

    caps1 = await manager.get_capabilities()
    caps2 = await manager.get_capabilities()

    assert len(caps1) == len(caps2) == 1
    assert caps1[0].url == caps2[0].url
    # MCP wrappers are distinct objects
    assert caps1[0] is not caps2[0]
    # Underlying MCPToolset is shared (cached by client_id)
    assert caps1[0].local is caps2[0].local
    # Manager state should be unchanged
    assert len(manager.servers) == 1
    assert len(manager.providers) == 0


# =============================================================================
# T9: Migration coverage tests
# =============================================================================


def test_to_transport_returns_correct_type() -> None:
    """to_transport() should return the correct transport type for each config."""
    from fastmcp.client.transports import (
        SSETransport,
        StdioTransport,
        StreamableHttpTransport,
    )

    stdio = StdioMCPServerConfig(command="python", args=["server.py"])
    sse = SSEMCPServerConfig(url=HttpUrl("http://localhost:8080/sse"))
    http = StreamableHTTPMCPServerConfig(url=HttpUrl("https://api.example.com/mcp"))

    assert isinstance(stdio.to_transport(), StdioTransport)
    assert isinstance(sse.to_transport(), SSETransport)
    assert isinstance(http.to_transport(), StreamableHttpTransport)


def test_to_transport_force_oauth_raises_for_stdio() -> None:
    """StdioMCPServerConfig.to_transport(force_oauth=True) should raise ValueError."""
    config = StdioMCPServerConfig(command="python", args=["server.py"])
    try:
        config.to_transport(force_oauth=True)
    except ValueError:
        pass
    else:
        msg = "Expected ValueError for force_oauth=True on stdio transport"
        raise AssertionError(msg)


def test_elicitation_handler_has_4_arg_signature() -> None:
    """_make_elicitation_handler() should return a callable with 4 parameters."""
    handler = _make_elicitation_handler()
    sig = inspect.signature(handler)
    params = list(sig.parameters.keys())
    assert len(params) == 4
    assert params[0] == "message"
    assert params[1] == "response_type"
    assert params[2] == "params"
    assert params[3] == "context"


async def test_include_instructions_is_true() -> None:
    """MCPToolset constructed by get_capabilities() should have include_instructions=True."""
    config = StdioMCPServerConfig(command="python", args=["server.py"])
    manager = MCPManager(servers=[config])

    caps = await manager.get_capabilities()

    assert len(caps) == 1
    toolset = caps[0].local
    assert isinstance(toolset, MCPToolset)
    assert toolset.include_instructions is True


def test_to_pydantic_ai_method_removed() -> None:
    """No config class should have a to_pydantic_ai attribute."""
    stdio = StdioMCPServerConfig(command="python", args=["server.py"])
    sse = SSEMCPServerConfig(url=HttpUrl("http://localhost:8080/sse"))
    http = StreamableHTTPMCPServerConfig(url=HttpUrl("https://api.example.com/mcp"))
    acp = AcpMCPServerConfig(acp_id="test-acp")

    for config in (stdio, sse, http, acp):
        assert "to_pydantic_ai" not in dir(config), (
            f"{type(config).__name__} should not have to_pydantic_ai"
        )


async def test_different_client_ids_produce_distinct_toolsets() -> None:
    """Two server configs with different client_ids should produce distinct MCPToolsets."""
    config_a = StdioMCPServerConfig(command="python", args=["server_a.py"])
    config_b = StdioMCPServerConfig(command="python", args=["server_b.py"])
    manager = MCPManager(servers=[config_a, config_b])

    caps = await manager.get_capabilities()

    assert len(caps) == 2
    assert caps[0].local is not caps[1].local
    assert isinstance(caps[0].local, MCPToolset)
    assert isinstance(caps[1].local, MCPToolset)


async def test_cache_cleanup_calls_aexit_on_cached_toolsets() -> None:
    """disconnect_all() should close cached MCPToolsets via __aexit__ before clearing."""
    from unittest.mock import AsyncMock, patch

    config = StdioMCPServerConfig(command="python", args=["server.py"])
    manager = MCPManager(servers=[config])

    await manager.get_capabilities()

    assert len(manager._toolset_cache) == 1
    cached_toolset = next(iter(manager._toolset_cache.values()))

    with patch.object(cached_toolset, "__aexit__", new_callable=AsyncMock) as mock_aexit:
        await manager.disconnect_all()
        mock_aexit.assert_awaited_once_with(None, None, None)

    assert len(manager._toolset_cache) == 0


async def test_cached_toolset_shared_across_tasks_without_error() -> None:
    """A cached MCPToolset shared across asyncio tasks must not raise CancelScope errors.

    Regression test: if someone re-adds _toolset_cache, this verifies that
    MCPToolset's reentrant design (running_count + anyio.Lock) handles
    cross-task usage without RuntimeError.
    """
    import asyncio

    config = StdioMCPServerConfig(command="python", args=["server.py"])
    manager = MCPManager(servers=[config])

    async def get_caps() -> list[Any]:
        return await manager.get_capabilities()

    task_a = asyncio.create_task(get_caps())
    caps_a = await task_a
    task_b = asyncio.create_task(get_caps())
    caps_b = await task_b

    assert len(caps_a) == 1
    assert len(caps_b) == 1
    # Cached toolset is shared across tasks
    assert caps_a[0].local is caps_b[0].local

    await manager.cleanup()
