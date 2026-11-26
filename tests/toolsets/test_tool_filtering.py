"""Tests for tool filtering in toolset configurations."""

from agentpool_config.toolsets import CodeToolsetConfig, SkillsToolsetConfig, SubagentToolsetConfig


async def test_subagent_tool_filtering():
    """Test filtering tools in subagent toolset."""
    config = SubagentToolsetConfig(tools={"task": True, "list_available_nodes": False})
    provider = config.get_provider()
    tools = await provider.get_tools()
    tool_names = {t.name for t in tools}

    assert "task" in tool_names
    assert "list_available_nodes" not in tool_names


async def test_skills_tool_filtering():
    """Test filtering tools in skills toolset."""
    config = SkillsToolsetConfig(tools={"load_skill": False})
    provider = config.get_provider()
    tools = await provider.get_tools()
    tool_names = {t.name for t in tools}

    assert "load_skill" not in tool_names
    assert "list_skills" in tool_names


async def test_code_toolset_filtering():
    """Test filtering tools in code toolset."""
    config = CodeToolsetConfig(tools={"format_code": True, "ast_grep": False})
    provider = config.get_provider()
    tools = await provider.get_tools()
    tool_names = {t.name for t in tools}

    assert "format_code" in tool_names
    assert "ast_grep" not in tool_names


async def test_filtering_provider_delegates_attributes():
    """Test that FilteringResourceProvider delegates attributes correctly."""
    config = SubagentToolsetConfig(tools={"task": True})
    provider = config.get_provider()

    # Should delegate name attribute
    assert provider.name == "subagent_tools"
    # Should have log attribute from ResourceProvider
    assert hasattr(provider, "log")
