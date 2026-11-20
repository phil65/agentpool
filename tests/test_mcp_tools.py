"""Tests for MCP tools functionality."""

from __future__ import annotations

from pydantic_ai import FunctionToolCallEvent
import pytest

from agentpool import Agent


@pytest.mark.integration
@pytest.mark.flaky(reruns=2)
async def test_mcp_tool_call(default_model: str):
    """Test basic MCP tool functionality with context7 server."""
    tool_calls = []  # Track tool usage

    def track_tool_usage(event: FunctionToolCallEvent, **kwargs):
        tool_calls.append((event, kwargs))

    servers = ["npx -y @upstash/context7-mcp"]
    async with Agent(model=default_model, mcp_servers=servers) as agent:
        agent.run_stream[FunctionToolCallEvent].connect(track_tool_usage)
        events = [i async for i in agent.run_stream("Look up pydantic docs")]
        assert events
        assert len(tool_calls) > 0


if __name__ == "__main__":
    pytest.main(["-v", __file__])
