"""Test contextual progress handler functionality with Agent and TestModel."""

from pathlib import Path
from typing import TYPE_CHECKING

from pydantic_ai.models.test import TestModel
import pytest

from agentpool import Agent, AgentPool
from agentpool.agents.context import AgentContext  # noqa: TC001
from agentpool.agents.events import ToolCallProgressEvent
from agentpool_config.mcp_server import StdioMCPServerConfig


if TYPE_CHECKING:
    from typing import Any

    from agentpool.agents.events import RichAgentStreamEvent


# Constants for test expectations
EXPECTED_PROGRESS_EVENTS = 3
PROGRESS_COMPLETION_THRESHOLD = 99
TEST_PROGRESS_VALUE = 50.0

SERVER_PATH = Path(__file__).parent / ".." / "mcp_server" / "server.py"
ARGS = ["run", str(SERVER_PATH)]
mcp_server = StdioMCPServerConfig(name="progress_test", command="uv", args=ARGS)


class ProgressCapture:
    """Captures progress callbacks with full context."""

    def __init__(self):
        self.progress_events: list[ToolCallProgressEvent] = []

    async def __call__(self, ctx: AgentContext[Any], event: RichAgentStreamEvent[object]) -> None:
        """Capture progress with full context."""
        if isinstance(event, ToolCallProgressEvent):
            self.progress_events.append(event)


async def _test_progress_events_common(agent_name: str, run_method: str) -> None:
    """Common test logic for progress events."""
    progress_capture = ProgressCapture()
    async with AgentPool() as pool:
        agent = Agent(
            name=agent_name,
            model=TestModel(call_tools=["test_progress"]),
            system_prompt="You are a test assistant that calls tools.",
            mcp_servers=[mcp_server],
            event_handlers=[progress_capture],
        )
        await pool.add_agent(agent)
        tools = await agent.tools.get_tools()
        tool_names = [tool.name for tool in tools]

        assert "test_progress" in tool_names, f"test_progress tool not found in {tool_names}"

        # Execute based on method
        if run_method == "streaming":
            async for _event in agent.run_stream(""):
                pass
        else:
            await agent.run("")

        # Verify we captured progress events
        assert len(progress_capture.progress_events) >= EXPECTED_PROGRESS_EVENTS, (
            f"Should have captured at least {EXPECTED_PROGRESS_EVENTS} progress events, "
            f"got {len(progress_capture.progress_events)}"
        )

        # Check that all events have contextual information
        for event in progress_capture.progress_events:
            # Basic FastMCP fields should be present
            assert event.progress is not None, "Progress should be set"
            assert event.message is not None, "Message should be set"

            # Our contextual fields should be present
            assert event.tool_name == "test_progress", (
                f"Tool name should be 'test_progress', got {event.tool_name}"
            )
            assert event.tool_call_id is not None, "Tool call ID should be set"
            assert event.tool_input is not None, "Tool input should be set"

            # Tool input should contain message parameter
            tool_input = event.tool_input
            assert isinstance(tool_input, dict), "Tool input should be a dict"
            assert "message" in tool_input, "Tool input should have message parameter"

        # Verify progress sequence
        progress_values = [
            e.progress for e in progress_capture.progress_events if e.progress is not None
        ]
        assert progress_values == sorted(progress_values), (
            f"Progress values should be increasing, got {progress_values}"
        )

        # Check specific progress messages from server.py
        messages = [event.message for event in progress_capture.progress_events]
        expected_messages = ["first step", "second step", "third step"]
        for expected_msg in expected_messages:
            assert any(expected_msg in str(msg) for msg in messages if msg), (
                f"Should contain {expected_msg!r} in messages: {messages}"
            )


async def test_progress_handler_with_agent_non_streaming():
    """Test that progress handlers receive tool context information (non-streaming)."""
    await _test_progress_events_common("progress_test_agent", "non_streaming")


async def test_progress_handler_with_agent_streaming():
    """Test that progress handlers receive tool context information (streaming)."""
    await _test_progress_events_common("progress_test_agent_streaming", "streaming")


async def test_agent_stream_progress_events():
    """Test that ToolCallProgressEvent appears in agent stream."""
    model = TestModel(call_tools=["test_progress"])
    async with AgentPool() as pool:
        agent = Agent(name="test", model=model, mcp_servers=[mcp_server])
        await pool.add_agent(agent)
        events = [event async for event in agent.run_stream("")]
        progress_events = [e for e in events if isinstance(e, ToolCallProgressEvent)]
        assert len(progress_events) > 0, (
            f"No ToolCallProgressEvent found in {[type(e) for e in events]}"
        )


if __name__ == "__main__":
    pytest.main(["-v", __file__])
