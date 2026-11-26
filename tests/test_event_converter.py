"""Tests for ACP event converter.

These tests demonstrate how the converter pattern makes testing easy -
no mocks needed, just assert on the yielded ACP session updates.
"""

from aclientp.schema import AgentMessageChunk, ToolCallProgress
from pydantic_ai import (
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
)
import pytest

from agentpool.agents.events import ToolCallStartEvent
from agentpool_server.acp_server.event_converter import ACPEventConverter


async def collect_updates(converter: ACPEventConverter, event):
    """Helper to collect all updates from an event."""
    return [u async for u in converter.convert(event)]


class TestACPEventConverter:
    """Test the ACP event converter."""

    @pytest.mark.anyio
    async def test_text_part_start_yields_agent_message_chunk(self):
        """PartStartEvent with TextPart yields AgentMessageChunk."""
        converter = ACPEventConverter()
        event = PartStartEvent(part=TextPart(content="Hello, world!"), index=0)

        updates = await collect_updates(converter, event)

        assert len(updates) == 1
        assert isinstance(updates[0], AgentMessageChunk)

    @pytest.mark.anyio
    async def test_text_delta_yields_agent_message_chunk(self):
        """PartDeltaEvent with TextPartDelta yields AgentMessageChunk."""
        converter = ACPEventConverter()
        event = PartDeltaEvent(delta=TextPartDelta(content_delta="streaming..."), index=0)

        updates = await collect_updates(converter, event)

        assert len(updates) == 1
        assert isinstance(updates[0], AgentMessageChunk)

    @pytest.mark.anyio
    async def test_multiple_events_yield_multiple_updates(self):
        """Multiple text events yield multiple updates."""
        converter = ACPEventConverter()

        events = [
            PartStartEvent(part=TextPart(content="Hello"), index=0),
            PartDeltaEvent(delta=TextPartDelta(content_delta=", "), index=0),
            PartDeltaEvent(delta=TextPartDelta(content_delta="world!"), index=0),
        ]

        all_updates = []
        for event in events:
            all_updates.extend(await collect_updates(converter, event))

        assert len(all_updates) == 3
        assert all(isinstance(u, AgentMessageChunk) for u in all_updates)

    @pytest.mark.anyio
    async def test_converter_reset_clears_state(self):
        """reset() clears internal state."""
        converter = ACPEventConverter()

        # Add some state by processing events
        event = PartStartEvent(part=TextPart(content="test"), index=0)
        await collect_updates(converter, event)

        # Reset
        converter.reset()

        # State should be cleared
        assert len(converter._tool_states) == 0
        assert len(converter._subagent_headers) == 0
        assert len(converter._subagent_content) == 0

    @pytest.mark.anyio
    async def test_converter_is_stateless_for_text(self):
        """Text conversion doesn't accumulate state."""
        converter = ACPEventConverter()

        # Process multiple text events
        for _ in range(5):
            event = PartStartEvent(part=TextPart(content="text"), index=0)
            await collect_updates(converter, event)

        # No tool state should be accumulated for plain text
        assert len(converter._tool_states) == 0

    @pytest.mark.anyio
    async def test_cancel_pending_tools_sends_cancellation_for_active_tools(self):
        """cancel_pending_tools() sends cancellation for all pending tool calls."""
        converter = ACPEventConverter()

        # Start two tool calls
        tool_event_1 = ToolCallStartEvent(
            tool_call_id="tool-1",
            tool_name="test_tool",
            title="test_tool",
            raw_input={"arg": "value"},
        )
        tool_event_2 = ToolCallStartEvent(
            tool_call_id="tool-2", tool_name="another_tool", title="another_tool"
        )

        # Process tool call starts
        await collect_updates(converter, tool_event_1)
        await collect_updates(converter, tool_event_2)

        # Verify both tools are tracked
        assert len(converter._tool_states) == 2

        # Cancel pending tools
        cancellations = [u async for u in converter.cancel_pending_tools()]

        # Should get cancellation notifications for both tools (status="completed")
        assert len(cancellations) == 2
        assert all(isinstance(u, ToolCallProgress) for u in cancellations)
        assert all(u.status == "completed" for u in cancellations)
        tool_ids = {u.tool_call_id for u in cancellations}
        assert tool_ids == {"tool-1", "tool-2"}

        # State should be cleared after cancellation
        assert len(converter._tool_states) == 0

    @pytest.mark.anyio
    async def test_cancel_pending_tools_handles_empty_state(self):
        """cancel_pending_tools() works when no tools are active."""
        converter = ACPEventConverter()

        # Cancel with no active tools
        cancellations = [u async for u in converter.cancel_pending_tools()]

        # Should yield nothing
        assert len(cancellations) == 0
        assert len(converter._tool_states) == 0


@pytest.mark.anyio
async def test_empty_thinking_content_sends_nothing() -> None:
    """Claude sends signature-only thinking deltas; they used to become newline chunks.

    T3 Code showed them as reasoning items made of nothing but newlines.
    """
    from aclientp.schema import AgentThoughtChunk
    from pydantic_ai import ThinkingPart, ThinkingPartDelta

    converter = ACPEventConverter()
    events = [
        PartStartEvent(part=ThinkingPart(content=""), index=0),
        PartDeltaEvent(delta=ThinkingPartDelta(content_delta=""), index=0),
        PartDeltaEvent(delta=ThinkingPartDelta(signature_delta="sig"), index=0),
        PartDeltaEvent(delta=ThinkingPartDelta(content_delta="Let me think"), index=0),
    ]
    updates = [u for event in events for u in await collect_updates(converter, event)]
    thoughts = [u for u in updates if isinstance(u, AgentThoughtChunk)]
    assert [t.content.text for t in thoughts] == ["Let me think"]  # type: ignore[union-attr]


@pytest.mark.anyio
async def test_progress_with_final_input_sends_it_as_raw_input() -> None:
    """Streamed tool calls start with empty args; the final args come with a progress event.

    Clients read the command from rawInput (T3 Code showed "$ Tool" without it).
    """
    from agentpool.agents.events import ToolCallProgressEvent

    converter = ACPEventConverter()
    start = ToolCallStartEvent(tool_call_id="t1", tool_name="Bash", title="Terminal", raw_input={})
    final = ToolCallProgressEvent(
        tool_call_id="t1", tool_name="Bash", title="`echo hi`", tool_input={"command": "echo hi"}
    )
    await collect_updates(converter, start)
    [update] = await collect_updates(converter, final)
    assert isinstance(update, ToolCallProgress)
    assert update.raw_input == {"command": "echo hi"}
    assert update.title == "`echo hi`"
