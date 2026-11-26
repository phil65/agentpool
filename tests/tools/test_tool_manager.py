"""Tests for tool management."""

from typing import TYPE_CHECKING

import pytest

from agentpool.tools import FunctionTool, ToolError, ToolManager


if TYPE_CHECKING:
    from agentpool.tools.base import Tool


async def test_basic_tool_management():
    """Test basic tool enabling/disabling."""
    tool1 = FunctionTool.from_callable(lambda x: x, name_override="tool1")
    tool2 = FunctionTool.from_callable(lambda x: x, name_override="tool2")

    manager = ToolManager([tool1, tool2])

    await manager.disable_tool("tool1")
    t_1 = await manager.get_tool("tool1")
    t_2 = await manager.get_tool("tool2")
    assert t_1
    assert t_2
    assert not t_1.enabled
    assert t_2.enabled


async def test_state_filtering():
    """Test filtering tools by state."""
    tool1 = FunctionTool.from_callable(lambda x: x, name_override="tool1")
    tool2 = FunctionTool.from_callable(lambda x: x, name_override="tool2")

    manager = ToolManager([tool1, tool2])
    await manager.disable_tool("tool1")

    enabled = await manager.get_tools(state="enabled")
    assert len(enabled) == 1
    assert enabled[0].name == "tool2"

    disabled = await manager.get_tools(state="disabled")
    assert len(disabled) == 1
    assert disabled[0].name == "tool1"


async def test_invalid_tool_operations():
    """Test error handling for invalid tool operations."""
    manager = ToolManager()

    with pytest.raises(ToolError):
        await manager.enable_tool("nonexistent")

    with pytest.raises(ToolError):
        await manager.disable_tool("nonexistent")


async def test_providers_property():
    """Test that providers property returns all providers."""
    from agentpool.resource_providers import StaticResourceProvider

    tool1 = FunctionTool.from_callable(lambda x: x, name_override="tool1")
    manager = ToolManager([tool1])

    # Add an external provider
    external_provider = StaticResourceProvider(name="test_external")
    manager.add_provider(external_provider)

    # Check providers property includes all three types
    all_providers = manager.providers
    expected_provider_count = 3  # external + worker + builtin
    assert len(all_providers) == expected_provider_count

    # Check that external providers are included
    assert external_provider in all_providers
    assert manager.worker_provider in all_providers
    assert manager.builtin_provider in all_providers

    # Check that external_providers only contains external providers
    assert len(manager.external_providers) == 1
    assert manager.external_providers[0] == external_provider


async def test_enabled_state_preservation():
    """Test that enabled states are preserved when tools change."""

    # Create a mock MCP provider with some tools
    class MockMCPProvider:
        def __init__(self):
            self.name = "mock_mcp"
            self._tools_cache: list[Tool] | None = [
                FunctionTool.from_callable(lambda x: x, name_override="tool1"),
                FunctionTool.from_callable(lambda x: x, name_override="tool2"),
            ]
            self._saved_enabled_states: dict[str, bool] = {}

        async def _on_tools_changed(self):
            """Callback when tools change on the MCP server."""
            # Save current enabled states before invalidating cache
            if self._tools_cache:
                self._saved_enabled_states = {tool.name: tool.enabled for tool in self._tools_cache}
            self._tools_cache = None  # Invalidate cache

    provider = MockMCPProvider()

    # Disable one tool
    assert provider._tools_cache is not None
    provider._tools_cache[0].enabled = False

    # Simulate tool change event (this should save states and invalidate cache)
    await provider._on_tools_changed()

    # Verify states were saved
    assert "tool1" in provider._saved_enabled_states
    assert "tool2" in provider._saved_enabled_states
    assert not provider._saved_enabled_states["tool1"]  # Should be False
    assert provider._saved_enabled_states["tool2"]  # Should be True

    # Cache should be invalidated
    assert provider._tools_cache is None


if __name__ == "__main__":
    pytest.main(["-v", __file__])
