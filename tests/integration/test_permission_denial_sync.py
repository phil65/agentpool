"""Test tool call event ordering with permission denial.

These tests verify that:
1. ToolCallStartEvent is emitted exactly once per tool call
2. Events arrive in correct order (start before result)
3. No duplicate ACP notifications are sent (which would cause UI sync issues)

The tests use a DenyingInputProvider to trigger the permission flow and
verify the event sequence matches the expected flow documented in
claude_code_agent.py's module docstring.
"""

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
import shutil
from typing import TYPE_CHECKING, Any

from aclientp.schema import ToolCallStart
from mcp.types import ElicitResult
import pytest

from agentpool.agents.claude_code_agent import ClaudeCodeAgent
from agentpool.tools.decisions import Deny
from agentpool.ui.base import InputProvider
from agentpool_server.acp_server.event_converter import ACPEventConverter


if TYPE_CHECKING:
    from agentpool import AgentContext
    from agentpool.tools.decisions import ToolDecision


@dataclass
class EventTrace:
    """Traces events for verification."""

    events: list[dict[str, Any]] = field(default_factory=list)
    permission_requests: list[dict[str, Any]] = field(default_factory=list)

    def log_event(self, event_type: str, event: Any) -> None:
        """Log an event."""
        self.events.append({"type": event_type, "event_class": type(event).__name__})

    def log_permission_request(self, tool_name: str, tool_call_id: str) -> None:
        """Log a permission request."""
        self.permission_requests.append({"tool_name": tool_name, "tool_call_id": tool_call_id})


class DenyingInputProvider(InputProvider):
    """Input provider that denies all tool calls."""

    def __init__(self, trace: EventTrace, delay: float = 0.1):
        self.trace = trace
        self.delay = delay
        self.denial_count = 0

    async def get_tool_confirmation(
        self,
        context: AgentContext[Any],
        tool_description: str = "",
    ) -> ToolDecision:
        """Deny all tool calls after a small delay."""
        tool_name = context.tool_name or "unknown"
        tool_call_id = context.tool_call_id or "unknown"
        self.trace.log_permission_request(tool_name, tool_call_id)
        await asyncio.sleep(self.delay)
        self.denial_count += 1
        return Deny()

    async def get_elicitation(self, *args: Any, **kwargs: Any) -> Any:
        """Not used in this test."""
        return ElicitResult(action="cancel")


requires_claude_code = pytest.mark.skipif(
    shutil.which("claude") is None,
    reason="Claude Code CLI not found - install with: npm install -g @anthropic-ai/claude-code",
)


@pytest.mark.integration
@requires_claude_code
async def test_tool_call_event_ordering():
    """Test that tool call events are emitted in correct order.

    Expected sequence per tool call:
    1. ToolCallStartEvent (from content_block_start)
    2. PartDeltaEvent (tool args streaming)
    3. [No ToolCallProgressEvent - removed to avoid race with permission]
    4. FunctionToolResultEvent or ToolCallCompleteEvent (after permission resolved)
    """
    trace = EventTrace()
    input_provider = DenyingInputProvider(trace)
    # Track events per tool_call_id
    tool_call_events = defaultdict[str, list[str]](list)

    async with ClaudeCodeAgent(name="test-agent", permission_mode="default") as agent:
        prompt = (
            "Create a file at /tmp/test_event_order.txt with content 'hello'. "
            "Don't retry if denied."
        )

        async for event in agent.run_stream(prompt, input_provider=input_provider):
            event_type = type(event).__name__
            trace.log_event("stream", event)

            # Track tool call specific events
            tool_call_id = None
            if hasattr(event, "tool_call_id"):
                tool_call_id = event.tool_call_id
            elif hasattr(event, "part") and hasattr(event.part, "tool_call_id"):
                tool_call_id = event.part.tool_call_id

            if tool_call_id:
                assert isinstance(tool_call_id, str)
                tool_call_events[tool_call_id].append(event_type)

        # Verify event ordering for each tool call
        for tc_id, events in tool_call_events.items():
            has_start = "ToolCallStartEvent" in events
            has_result = "FunctionToolResultEvent" in events or "ToolCallCompleteEvent" in events

            if has_result:
                assert has_start, f"Tool call {tc_id} has result but no start event"

                # Start should come before result
                start_idx = -1
                for i, e in enumerate(events):
                    if e == "ToolCallStartEvent":
                        start_idx = i
                        break

                result_idx = -1
                for i, e in enumerate(events):
                    if e in ("FunctionToolResultEvent", "ToolCallCompleteEvent"):
                        result_idx = i
                        break

                assert start_idx < result_idx, (
                    f"Tool call {tc_id}: start ({start_idx}) should come before "
                    f"result ({result_idx})"
                )


@pytest.mark.integration
@requires_claude_code
async def test_no_duplicate_acp_tool_call_notifications():
    """Test that each tool call produces exactly one ToolCallStart notification.

    Duplicate notifications cause UI sync issues - the ACP client (e.g., Zed)
    may cancel permission dialogs if it receives unexpected updates.
    """
    trace = EventTrace()
    input_provider = DenyingInputProvider(trace)
    converter = ACPEventConverter()
    # Track ToolCallStart notifications per tool_call_id
    tool_call_starts: dict[str, int] = {}

    async with ClaudeCodeAgent(
        name="test-agent",
        permission_mode="default",
    ) as agent:
        prompt = (
            "Create a file at /tmp/test_acp_sync.txt with content 'test'. Don't retry if denied."
        )

        async for event in agent.run_stream(prompt, input_provider=input_provider):
            # Convert to ACP notifications
            async for acp_update in converter.convert(event):
                if isinstance(acp_update, ToolCallStart):
                    tc_id = acp_update.tool_call_id
                    tool_call_starts[tc_id] = tool_call_starts.get(tc_id, 0) + 1

        # Verify no duplicates
        for tc_id, count in tool_call_starts.items():
            assert count == 1, (
                f"Tool call {tc_id} had {count} ToolCallStart notifications "
                f"(expected 1). Duplicate notifications cause ACP client sync issues."
            )


if __name__ == "__main__":
    asyncio.run(test_tool_call_event_ordering())
    asyncio.run(test_no_duplicate_acp_tool_call_notifications())
    print("All tests passed!")
