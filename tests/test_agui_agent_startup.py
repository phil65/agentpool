"""Integration tests for AGUIAgent with startup command lifecycle management."""

from __future__ import annotations

import socket
import sys

import pytest

from agentpool.agents.agui_agent import AGUIAgent
from agentpool.agents.events import StreamCompleteEvent


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.mark.skipif(sys.platform == "win32", reason="Hangs on Windows CI")
async def test_agui_agent_with_managed_server():
    """Test AGUIAgent with automatic server lifecycle management."""
    port = _free_port()
    agent = AGUIAgent(
        endpoint=f"http://127.0.0.1:{port}/agent/run",
        name="test-agent",
        startup_command=f"uv run python tests/test_server_agui.py --port {port}",
    )

    async with agent:
        # Verify server process is running
        assert agent._startup_process is not None
        assert agent._startup_process.returncode is None
        # Test actual communication with the server
        result = await agent.run("What is 2+2?")
        assert result.content
        assert "4" in result.content

    # Verify cleanup - process should be stopped
    assert agent._startup_process is None


@pytest.mark.skipif(sys.platform == "win32", reason="Hangs on Windows CI")
async def test_agui_agent_streaming_with_managed_server():
    """Test AGUIAgent streaming with managed server."""
    port = _free_port()
    agent = AGUIAgent(
        endpoint=f"http://127.0.0.1:{port}/agent/run",
        name="test-agent",
        startup_command=f"uv run python tests/test_server_agui.py --port {port}",
    )

    async with agent:
        events = []
        async for event in agent.run_stream("Hello"):
            events.append(event)  # noqa: PERF401
        assert len(events) > 0
        # Last event should be StreamCompleteEvent with final message
        complete_event = events[-1]
        assert isinstance(complete_event, StreamCompleteEvent)
        assert complete_event.message.content


@pytest.mark.skipif(sys.platform == "win32", reason="Hangs on Windows CI")
async def test_agui_agent_startup_failure():
    """Test AGUIAgent handles startup command failures gracefully."""
    agent = AGUIAgent(
        endpoint="http://localhost:8765/agent/run",
        name="test-agent",
        startup_command="false",  # Command that fails immediately
        startup_delay=0.1,
    )

    with pytest.raises(RuntimeError, match="Startup process exited"):
        async with agent:
            pass


if __name__ == "__main__":
    pytest.main(["-v", __file__])
