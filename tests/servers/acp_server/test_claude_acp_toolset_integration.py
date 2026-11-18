"""Integration test for Claude ACP agent with toolsets exposed via MCP bridge.

This test creates an AgentPool with a Claude ACP agent configured with
the Subagent toolset, which gets exposed via an internal MCP server bridge.
The Claude agent can then use our internal tools through MCP.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import shutil

import pytest

from agentpool import AgentPool
from agentpool.agents.acp_agent import ACPAgent
from agentpool.models.acp_agents.base import ACPAgentConfig
from agentpool.models.manifest import AgentsManifest
from agentpool_config.toolsets import SubagentToolsetConfig


if not shutil.which("claude-agent-acp"):
    pytest.skip("claude-agent-acp CLI not available", allow_module_level=True)

pytestmark = [pytest.mark.integration]


@pytest.fixture
def claude_config_with_subagent() -> ACPAgentConfig:
    """Create Claude ACP config with Subagent toolset."""
    return ACPAgentConfig(
        name="claude_orchestrator",
        command="claude-agent-acp",
        tools=[SubagentToolsetConfig()],
        env_vars={"ANTHROPIC_API_KEY": ""},  # Use subscription, not direct API key
    )


@pytest.fixture
def manifest_with_claude(claude_config_with_subagent: ACPAgentConfig) -> AgentsManifest:
    """Create manifest with Claude ACP agent."""
    return AgentsManifest(agents={"claude_orchestrator": claude_config_with_subagent})


async def test_claude_acp_with_subagent_toolset_setup(manifest_with_claude: AgentsManifest):
    """Test that Claude ACP agent with Subagent toolset initializes correctly."""
    async with AgentPool(manifest=manifest_with_claude) as pool:
        # Verify ACP agent was created
        assert "claude_orchestrator" in pool.get_agents(ACPAgent)
        agent = pool.get_agents(ACPAgent)["claude_orchestrator"]
        # Verify toolset bridge was set up
        assert agent._tool_bridge is not None
        # Verify the MCP server is running
        assert agent._tool_bridge.port > 0
        assert "mcp" in agent._tool_bridge.url
        # Verify tools are registered (SubagentToolset always has tools)
        tools = await agent.tools.get_tools()
        assert len(tools) > 0
        tool_names = {t.name for t in tools}
        # SubagentTools provides: list_available_nodes, task
        assert "list_available_nodes" in tool_names or "task" in tool_names


async def test_claude_acp_subagent_invocation(manifest_with_claude: AgentsManifest):
    """Test invoking subagent tools through Claude ACP agent.

    Note: This test requires:
    - claude-agent-acp to be installed and accessible
    - Valid API credentials for Claude
    """
    async with AgentPool(manifest=manifest_with_claude) as pool:
        agent = pool.get_agents(ACPAgent)["claude_orchestrator"]
        # Ask the agent to list available nodes - it should have access via MCP
        prompt = "Use the list_available_nodes tool to show me available agents"
        result = await asyncio.wait_for(agent.run(prompt), timeout=45.0)
        assert result is not None
        assert result.content is not None


async def test_claude_acp_tool_bridge_mcp_config(claude_config_with_subagent: ACPAgentConfig):
    """Test that tool bridge MCP config is properly passed to session."""
    async with AgentPool() as pool:  # noqa: SIM117
        async with ACPAgent.from_config(claude_config_with_subagent, agent_pool=pool) as agent:
            # Verify extra MCP servers include our bridge
            assert len(agent._extra_mcp_servers) > 0
            # Find our toolset bridge server
            bridge_server = next(
                (s for s in agent._extra_mcp_servers if "tools" in s.name),
                None,
            )
            assert bridge_server is not None


async def test_claude_acp_multiple_toolsets():
    """Test Claude ACP agent with multiple toolsets."""
    from agentpool_config.toolsets import DebugToolsetConfig

    tools = [SubagentToolsetConfig(), DebugToolsetConfig()]
    config = ACPAgentConfig(
        name="claude_multi", command="claude-agent-acp", cwd=str(Path.cwd()), tools=tools
    )
    async with AgentPool() as pool, ACPAgent.from_config(config, agent_pool=pool) as agent:
        # All toolsets should be exposed via single bridge
        assert agent._tool_bridge is not None
        tool_names = {t.name for t in await agent.tools.get_tools()}
        # Should have tools from both toolsets
        # SubagentToolset provides: list_available_nodes, task
        assert "list_available_nodes" in tool_names
        assert "task" in tool_names
        assert "execute_introspection" in tool_names


async def test_pool_cleanup_stops_tool_bridges(manifest_with_claude: AgentsManifest):
    """Test that pool cleanup properly stops tool bridges."""
    async with AgentPool(manifest=manifest_with_claude) as pool:
        agent = pool.get_agents(ACPAgent)["claude_orchestrator"]
        assert agent._tool_bridge is not None
        assert agent._tool_bridge.port > 0


if __name__ == "__main__":
    pytest.main(["-v", __file__])
