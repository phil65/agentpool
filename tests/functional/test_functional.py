"""Integration tests for agent pipeline functions."""

import pytest

from agentpool.functional import run_agent, run_agent_sync


async def test_agent_pipeline():
    """Test async pipeline."""
    result = await run_agent("Hello!", model="test")
    assert isinstance(result, str)
    assert result == "success (no tool calls)"  # From TestModel fixture


def test_sync_pipeline():
    """Test sync pipeline."""
    result = run_agent_sync("Hello!", model="test")
    assert isinstance(result, str)
    assert result == "success (no tool calls)"


if __name__ == "__main__":
    pytest.main([__file__])
