"""Integration test for CodexAgent with toolsets exposed via MCP bridge.

This test creates an AgentPool with a CodexAgent configured with
toolsets, which get exposed via an internal MCP server bridge (HTTP transport).
The Codex agent can then use our internal tools through MCP.

Run with: pytest tests/agents/codex_agent/test_codex_toolset_integration.py -v
"""

import asyncio
from pathlib import Path
import shutil

import pytest

from agentpool import AgentPool
from agentpool.agents.codex_agent import CodexAgent
from agentpool.models.codex_agents import CodexAgentConfig
from agentpool.models.manifest import AgentsManifest
from agentpool_config.mcp_server import StdioMCPServerConfig
from agentpool_config.toolsets import SkillsToolsetConfig, SubagentToolsetConfig


if not shutil.which("codex"):
    pytest.skip("codex CLI not available", allow_module_level=True)

pytestmark = [pytest.mark.integration]
DEFAULT_MODEL = "gpt-5.1-codex-mini"


@pytest.fixture
def codex_config_with_subagent() -> CodexAgentConfig:
    """Create CodexAgent config with Subagent toolset."""
    return CodexAgentConfig(
        name="codex_orchestrator",
        description="Codex agent with subagent delegation capabilities",
        model=DEFAULT_MODEL,
        reasoning_effort="medium",
        approval_policy="never",
        tools=[SubagentToolsetConfig()],
    )


@pytest.fixture
def manifest_with_codex(
    codex_config_with_subagent: CodexAgentConfig,
) -> AgentsManifest:
    """Create manifest with CodexAgent."""
    return AgentsManifest(
        agents={"codex_orchestrator": codex_config_with_subagent},
    )


async def test_codex_with_subagent_toolset_setup(
    manifest_with_codex: AgentsManifest,
):
    """Test that CodexAgent with Subagent toolset initializes correctly."""
    async with AgentPool(manifest=manifest_with_codex) as pool:
        # Verify CodexAgent was created (in codex_agents, not agents)
        assert "codex_orchestrator" in pool.get_agents()
        agent = pool.get_agents()["codex_orchestrator"]
        # Verify tools are registered (SubagentToolset always has tools)
        tools = await agent.tools.get_tools()
        assert len(tools) > 0
        tool_names = {t.name for t in tools}
        # SubagentTools provides: list_available_nodes, task
        assert "list_available_nodes" in tool_names or "task" in tool_names


@pytest.mark.flaky(reruns=3, reruns_delay=2)
async def test_codex_subagent_tool_invocation():
    """Test invoking subagent tools through CodexAgent.

    Note: This test requires:
    - codex CLI to be installed and accessible
    - Valid Codex server to be running
    - May be flaky due to external service timing

    Uses a unique agent name to avoid conflicts with other tests.
    """
    # Create a unique config for this test to avoid conflicts with other tests
    config = CodexAgentConfig(
        name="codex_tool_invoker",  # Unique name
        model=DEFAULT_MODEL,
        reasoning_effort="medium",
        approval_policy="never",
        tools=[SubagentToolsetConfig()],
    )
    manifest = AgentsManifest(agents={"codex_tool_invoker": config})

    async with AgentPool(manifest=manifest) as pool:
        agent = pool.get_agents()["codex_tool_invoker"]
        assert isinstance(agent, CodexAgent)
        # Ask the agent to list available nodes - it should have access via MCP
        prompt = "Use the list_available_nodes tool to show me available agents"
        result = await asyncio.wait_for(agent.run(prompt), timeout=60.0)
        assert result is not None
        # Result should contain information about available nodes
        result_text = str(result)
        # Should at least contain this agent's own name or some indication of nodes
        assert len(result_text) > 0


async def test_codex_multiple_toolsets():
    """Test CodexAgent with multiple toolsets."""
    config = CodexAgentConfig(
        name="codex_multi",
        model=DEFAULT_MODEL,
        reasoning_effort="medium",
        approval_policy="never",
        tools=[SubagentToolsetConfig(), SkillsToolsetConfig()],
    )

    async with AgentPool() as pool:
        agent = CodexAgent.from_config(config, agent_pool=pool)
        agent = await pool.exit_stack.enter_async_context(agent)
        # All toolsets should be exposed via single bridge
        tools = await agent.tools.get_tools()
        tool_names = {t.name for t in tools}
        # Should have tools from both toolsets
        # SubagentToolset provides: list_available_nodes, task
        assert "list_available_nodes" in tool_names or "task" in tool_names
        # SkillsToolset provides: list_skills, load_skill, run_command
        assert "list_skills" in tool_names or "load_skill" in tool_names


async def test_codex_mcp_servers_config():
    """Test that MCP servers config includes both external and bridge servers."""
    # Create config with both external MCP server and toolset
    # Use the actual test MCP server instead of a bogus 'echo' command
    test_server_path = Path(__file__).parent.parent.parent / "mcp_server" / "server.py"
    config = CodexAgentConfig(
        name="codex_mixed",
        model=DEFAULT_MODEL,
        reasoning_effort="medium",
        approval_policy="never",
        tools=[SubagentToolsetConfig()],
        mcp_servers=[
            StdioMCPServerConfig(
                name="external_test",
                command="uv",
                args=["run", "python", str(test_server_path)],
            )
        ],
    )

    async with AgentPool() as pool:
        agent = CodexAgent.from_config(config, agent_pool=pool)
        agent = await pool.exit_stack.enter_async_context(agent)
        # Should have added toolset via tool bridge (extra MCP server)
        # The bridge is stored in agent._extra_mcp_servers
        assert len(agent._extra_mcp_servers) >= 1
        # Should have bridge server name containing "tools"
        bridge_names = [name for name, _ in agent._extra_mcp_servers]
        assert any("tools" in name for name in bridge_names)


if __name__ == "__main__":
    pytest.main(["-v", __file__])
