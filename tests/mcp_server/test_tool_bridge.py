"""Tests for ToolManagerBridge - exposing ToolManager tools to ACP agents via MCP."""

import shutil
from typing import TYPE_CHECKING, Any

from clawd_code_sdk import ClaudeAgentOptions, ClaudeSDKClient, tool
from clawd_code_sdk.models import McpHttpServerConfig, ResultMessage
import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
import pytest

from agentpool import AgentPool
from agentpool.agents.acp_agent import ACPAgent
from agentpool.agents.native_agent import Agent
from agentpool.mcp_server.tool_bridge import ToolManagerBridge
from agentpool.models.acp_agents.base import ACPAgentConfig
from agentpool_config.toolsets import SkillsToolsetConfig, SubagentToolsetConfig


if TYPE_CHECKING:
    from mcp.types import RequestParamsMeta

    from agentpool.agents.context import AgentContext


# Simple tool without context
def simple_add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b


# Tool that needs AgentContext
async def list_pool_agents(ctx: AgentContext) -> str:
    """List all agents in the pool."""
    if ctx.pool is None:
        return "No pool available"
    return ", ".join(ctx.pool.all_agents.keys())


async def test_bridge_lifecycle():
    """Test bridge start/stop lifecycle."""
    async with AgentPool() as pool:
        agent = Agent(name="test", model="test", system_prompt="Test")
        await pool.add_agent(agent)
        agent.tools.register_tool(simple_add)
        bridge = ToolManagerBridge(node=agent)
        # Not started yet
        assert bridge._mcp is None
        assert bridge._actual_port is None
        await bridge.start()
        assert bridge._mcp is not None
        assert bridge._actual_port is not None
        assert bridge.port > 0
        assert "http://" in bridge.url
        await bridge.stop()
        assert bridge._mcp is None
        assert bridge._actual_port is None


async def test_bridge_context_manager():
    """Test bridge as async context manager."""
    async with AgentPool() as pool:
        agent = Agent(name="test", model="test", system_prompt="Test")
        await pool.add_agent(agent)
        agent.tools.register_tool(simple_add)
        async with ToolManagerBridge(node=agent) as bridge:
            assert bridge.port > 0
            assert bridge._server is not None

        assert bridge._server is None  # After exit, should be stopped


async def test_bridge_registers_tools():
    """Test that tools are registered with FastMCP."""
    async with AgentPool() as pool:
        agent = Agent(name="test", model="test", system_prompt="Test")
        await pool.add_agent(agent)
        agent.tools.register_tool(simple_add)
        async with ToolManagerBridge(node=agent) as bridge:
            assert bridge._mcp is not None  # The MCP server should have our tool registered
            # FastMCP stores tools internally - we verify via the tool count
            tools = await agent.tools.get_tools()
            assert len(tools) == 1
            assert tools[0].name == "simple_add"


async def test_bridge_with_context_tools():
    """Test that tools requiring AgentContext work through bridge."""
    async with AgentPool() as pool:
        # Add an agent so there's somethin
        agent = Agent(name="helper", model="test", system_prompt="Helper")
        await pool.add_agent(agent)
        agent.tools.register_tool(simple_add)
        agent.tools.register_tool(list_pool_agents)
        async with ToolManagerBridge(node=agent):
            # Both tools should be registered
            tools = await agent.tools.get_tools()
            assert len(tools) == 2
            tool_names = {t.name for t in tools}
            assert "simple_add" in tool_names
            assert "list_pool_agents" in tool_names


async def test_acp_agent_toolsets_adds_providers():
    """Test that toolsets from config are added to ToolManager."""
    async with AgentPool() as pool:
        toolsets = [SubagentToolsetConfig(), SkillsToolsetConfig()]
        config = ACPAgentConfig(name="test_acp", command="claude-agent-acp", tools=toolsets)
        agent = ACPAgent.from_config(config, agent_pool=pool)
        await agent._setup_toolsets()
        # Check that providers were added
        # The tools should now include tools from both toolsets
        tools = await agent.tools.get_tools()
        tool_names = {t.name for t in tools}
        # SubagentTools provides: list_available_nodes, task
        assert "list_available_nodes" in tool_names
        assert "task" in tool_names
        # SkillsTools provides: list_skills, load_skill, run_command
        assert "list_skills" in tool_names
        await agent._cleanup()


async def test_tool_call_via_mcp_client():
    """Test calling a tool through the MCP bridge via an actual MCP client.

    This verifies that tool_call_id extraction works correctly when tools
    are called through MCP.
    """
    captured_tool_call_ids: list[str] = []

    # Tool that captures its context's tool_call_id
    async def capture_id_tool(ctx: AgentContext, message: str) -> str:
        """Tool that captures the tool_call_id from context."""
        captured_tool_call_ids.append(ctx.tool_call_id or "no-id")
        return f"Got message: {message}"

    async with AgentPool() as pool:
        agent = Agent(name="test", model="test", system_prompt="Test")
        await pool.add_agent(agent)
        agent.tools.register_tool(capture_id_tool)

        async with (
            ToolManagerBridge(node=agent) as bridge,
            httpx2.AsyncClient(headers=bridge.headers) as http,
            streamable_http_client(bridge.url, http_client=http) as (read_stream, write_stream),
            ClientSession(read_stream, write_stream) as session,
        ):
            await session.initialize()
            # List tools to verify registration
            tools_result = await session.list_tools()
            tool_names = [t.name for t in tools_result.tools]
            assert "capture_id_tool" in tool_names
            # Call the tool - without _meta, should generate fallback ID
            await session.call_tool("capture_id_tool", {"message": "hello"})
            assert len(captured_tool_call_ids) == 1
            # Should have generated a UUID-like fallback (36 chars)
            uuid_length = 36
            assert len(captured_tool_call_ids[0]) == uuid_length


async def test_tool_call_with_meta_tool_use_id():
    """Test that claudecode/toolUseId in _meta is extracted correctly.

    When Claude Code calls tools, it passes the tool_use_id in _meta.
    This test simulates that behavior by passing meta to call_tool.
    """
    captured_tool_call_ids: list[str] = []

    async def capture_id_tool(ctx: AgentContext, value: int) -> str:
        """Tool that captures the tool_call_id from context."""
        captured_tool_call_ids.append(ctx.tool_call_id or "no-id")
        return f"value={value}"

    async with AgentPool() as pool:
        agent = Agent(name="test", model="test")
        await pool.add_agent(agent)
        agent.tools.register_tool(capture_id_tool)

        async with (
            ToolManagerBridge(node=agent) as bridge,
            httpx2.AsyncClient(headers=bridge.headers) as http,
            streamable_http_client(bridge.url, http_client=http) as (read_stream, write_stream),
            ClientSession(read_stream, write_stream) as session,
        ):
            await session.initialize()
            # Call tool WITH meta containing claudecode/toolUseId
            # This simulates what Claude Code should send
            expected_id = "toolu_01ABC123XYZ"
            meta: RequestParamsMeta = {"claudecode/toolUseId": expected_id}
            await session.call_tool("capture_id_tool", {"value": 42}, meta=meta)

            assert len(captured_tool_call_ids) == 1
            captured_id = captured_tool_call_ids[0]
            # The captured ID should match what we sent in meta
            assert captured_id == expected_id, (
                f"Expected tool_call_id '{expected_id}', got '{captured_id}'"
            )


requires_claude_code = pytest.mark.skipif(
    shutil.which("claude") is None, reason="Claude Code CLI not found"
)


@requires_claude_code
async def test_claude_code_passes_tool_use_id_in_meta():
    """Integration test: verify Claude Code CLI passes claudecode/toolUseId in _meta.

    This test uses the actual Claude Code CLI via ClaudeSDKClient to verify
    that when Claude calls an MCP tool, it passes the tool_use_id in _meta.

    Run manually with: pytest -v -s -k test_claude_code_passes_tool_use_id_in_meta --run-skip
    """

    @tool(name="capture_meta", description="Captures the _meta field", input_schema={"value": int})
    async def capture_meta_tool(input_data: dict[str, Any]) -> dict[str, Any]:
        """Tool that captures what _meta was passed."""
        # The _meta is not directly accessible here in SDK tools
        # We need a different approach - use our MCP bridge
        return {"result": f"Got value: {input_data.get('value')}"}

    # For this test to work, we need to use our MCP bridge and check
    # what Claude Code actually sends. Let's modify the approach.
    # Placeholder - see test below


@pytest.mark.integration
@requires_claude_code
async def test_claude_code_mcp_bridge_integration():
    """Integration test using our MCP bridge with Claude Code.

    This test:
    1. Starts our MCP bridge with a tool that captures context
    2. Runs ClaudeSDKClient with that MCP server
    3. Asks Claude to use the tool
    4. Verifies the tool_call_id was extracted from meta
    """
    # Force SDK to use Claude Code CLI subscription instead of API
    captured_ids: list[str] = []

    async def capture_context_tool(ctx: AgentContext, number: int) -> str:
        """Tool that captures context info."""
        captured_ids.append(ctx.tool_call_id or "none")
        return f"Got number: {number}"

    async with AgentPool() as pool:
        agent = Agent(name="test", model="test")
        await pool.add_agent(agent)
        agent.tools.register_tool(capture_context_tool)

        async with ToolManagerBridge(node=agent) as bridge:
            options = ClaudeAgentOptions(
                mcp_servers={
                    "test_bridge": McpHttpServerConfig(url=bridge.url, headers=bridge.headers)
                },
                allowed_tools=["mcp__test_bridge__capture_context_tool"],
            )

            async with ClaudeSDKClient(options=options) as client:
                await client.query(
                    "You MUST use the capture_context_tool from test_bridge MCP server. "
                    "Call it with number=42. Do not explain, just call the tool."
                )

                # Stream messages until we get a result
                async for msg in client.receive_messages():
                    if isinstance(msg, ResultMessage):
                        break

                if not captured_ids:
                    pytest.skip("Tool was not called - Claude may have responded differently")

                tool_id = captured_ids[0]
                # Claude tool IDs typically start with "toolu_" when passed via _meta
                # Otherwise we'd see a fallback UUID
                assert tool_id.startswith("toolu_"), (
                    f"Expected Claude tool ID (toolu_*), got: {tool_id}. "
                    "Claude Code may not be passing claudecode/toolUseId for external MCP."
                )


if __name__ == "__main__":
    pytest.main(["-v", __file__])


async def test_tool_changes_reach_listening_clients() -> None:
    """A toolset change outside any request reaches 2026-07-28 clients' listen streams."""
    import asyncio

    from fastmcp import Client
    from mcp.shared.subscriptions import ToolsListChanged

    async with AgentPool() as pool:
        agent = Agent(name="test", model="test", system_prompt="Test")
        await pool.add_agent(agent)
        agent.tools.register_tool(simple_add)
        async with (
            ToolManagerBridge(node=agent) as bridge,
            Client(bridge.url, auth=bridge.token) as client,
        ):
            assert {t.name for t in await client.list_tools()} == {"simple_add"}
            async with client.listen(tools_list_changed=True) as subscription:
                provider = agent.tools.builtin_provider
                provider.register_tool(list_pool_agents)
                await provider.tools_changed.emit(provider.create_change_event("tools"))
                event = await asyncio.wait_for(anext(subscription), timeout=10)
            assert isinstance(event, ToolsListChanged)
            names = {t.name for t in await client.list_tools()}
        assert names == {"simple_add", "list_pool_agents"}
