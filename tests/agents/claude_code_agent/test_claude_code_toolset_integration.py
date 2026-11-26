"""Integration test for ClaudeCodeAgent with toolsets exposed via MCP bridge.

This test creates an AgentPool with a ClaudeCodeAgent configured with
toolsets, which get exposed via an internal MCP server bridge (SDK transport).
The Claude agent can then use our internal tools through MCP.

Run with: pytest tests/agents/claude_code_agent/test_claude_code_toolset_integration.py -v
"""

import asyncio
import shutil

import pytest

from agentpool import AgentPool
from agentpool.agents.claude_code_agent import ClaudeCodeAgent
from agentpool.models.claude_code_agents import ClaudeCodeAgentConfig
from agentpool.models.manifest import AgentsManifest
from agentpool_config.mcp_server import StdioMCPServerConfig
from agentpool_config.toolsets import SkillsToolsetConfig, SubagentToolsetConfig


if not shutil.which("claude"):
    pytest.skip("claude CLI not available", allow_module_level=True)

pytestmark = [pytest.mark.integration]


@pytest.fixture
def claude_code_config_with_subagent() -> ClaudeCodeAgentConfig:
    """Create ClaudeCodeAgent config with Subagent toolset."""
    return ClaudeCodeAgentConfig(
        name="claude_code_orchestrator",
        description="Claude Code agent with subagent delegation capabilities",
        model="haiku",
        permission_mode="acceptEdits",
        tools=[SubagentToolsetConfig()],
        builtin_tools=[],  # Disable builtin tools for focused testing
        env_vars={"ANTHROPIC_API_KEY": ""},  # Use subscription, not direct API key
    )


@pytest.fixture
def manifest_with_claude_code(
    claude_code_config_with_subagent: ClaudeCodeAgentConfig,
) -> AgentsManifest:
    """Create manifest with ClaudeCodeAgent."""
    return AgentsManifest(
        agents={"claude_code_orchestrator": claude_code_config_with_subagent},
    )


async def test_claude_code_with_subagent_toolset_setup(
    manifest_with_claude_code: AgentsManifest,
):
    """Test that ClaudeCodeAgent with Subagent toolset initializes correctly."""
    async with AgentPool(manifest=manifest_with_claude_code) as pool:
        # Verify ClaudeCodeAgent was created (in claude_code_agents, not agents)
        assert "claude_code_orchestrator" in pool.get_agents()
        agent = pool.get_agents()["claude_code_orchestrator"]
        # Verify tools are registered (SubagentToolset always has tools)
        tools = await agent.tools.get_tools()
        assert len(tools) > 0
        tool_names = {t.name for t in tools}
        # SubagentTools provides: list_available_nodes, task
        assert "list_available_nodes" in tool_names or "task" in tool_names


async def test_claude_code_subagent_tool_invocation(
    manifest_with_claude_code: AgentsManifest,
):
    """Test invoking subagent tools through ClaudeCodeAgent.

    Note: This test requires:
    - claude CLI to be installed and accessible
    - Valid API credentials for Claude
    """
    async with AgentPool(manifest=manifest_with_claude_code) as pool:
        agent = pool.get_agents()["claude_code_orchestrator"]
        assert isinstance(agent, ClaudeCodeAgent)
        # Ask the agent to list available nodes - it should have access via MCP
        prompt = "Use the list_available_nodes tool to show me available agents"
        result = await asyncio.wait_for(agent.run(prompt), timeout=60.0)
        assert result is not None
        # Result should contain information about available nodes
        result_text = str(result)
        # Should at least contain this agent's own name or some indication of nodes
        assert len(result_text) > 0


async def test_claude_code_multiple_toolsets():
    """Test ClaudeCodeAgent with multiple toolsets."""
    config = ClaudeCodeAgentConfig(
        name="claude_code_multi",
        permission_mode="acceptEdits",
        tools=[SubagentToolsetConfig(), SkillsToolsetConfig()],
        builtin_tools=[],
    )

    async with AgentPool() as pool:
        agent = ClaudeCodeAgent.from_config(config, agent_pool=pool)
        agent = await pool.exit_stack.enter_async_context(agent)
        # All toolsets should be exposed via single bridge
        tools = await agent.tools.get_tools()
        tool_names = {t.name for t in tools}
        # Should have tools from both toolsets
        # SubagentToolset provides: list_available_nodes, task
        assert "list_available_nodes" in tool_names or "task" in tool_names
        # SkillsToolset provides: list_skills, load_skill, run_command
        assert "list_skills" in tool_names or "load_skill" in tool_names


async def test_claude_code_mcp_servers_config():
    """Test that MCP servers config includes both external and bridge servers."""
    # Create config with both external MCP server and toolset
    config = ClaudeCodeAgentConfig(
        name="claude_code_mixed",
        permission_mode="acceptEdits",
        tools=[SubagentToolsetConfig()],
        mcp_servers=[StdioMCPServerConfig(name="external_test", command="echo", args=["test"])],
        builtin_tools=[],
    )

    async with AgentPool() as pool:
        agent = ClaudeCodeAgent.from_config(config, agent_pool=pool)
        agent = await pool.exit_stack.enter_async_context(agent)
        # Should have both external and bridge MCP servers
        assert len(agent._mcp_servers) >= 2
        # Should have the external server
        assert any("external" in name for name in agent._mcp_servers)
        # Should have the bridge server
        assert any("tools" in name for name in agent._mcp_servers)


if __name__ == "__main__":
    pytest.main(["-v", __file__])
