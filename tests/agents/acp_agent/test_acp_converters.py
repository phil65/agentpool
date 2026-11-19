"""Tests for ACP to ChatMessage converters."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aclientp.schema import (
    AgentMessageChunk,
    AgentThoughtChunk,
    SessionNotification,
    ToolCallProgress,
    ToolCallStart,
    UserMessageChunk,
)
from pydantic_ai import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
)
import pytest

from agentpool.agents.acp_agent.acp_converters import (
    ACPMessageAccumulator,
    acp_notifications_to_messages,
)


if TYPE_CHECKING:
    from aclientp.schema import SessionUpdate


class TestACPMessageAccumulator:
    """Tests for ACPMessageAccumulator class."""

    def test_empty_accumulator(self) -> None:
        """Empty accumulator returns empty list."""
        accumulator = ACPMessageAccumulator()
        messages = accumulator.finalize()
        assert messages == []

    def test_single_user_message(self) -> None:
        """Single user message chunk becomes a user ChatMessage."""
        accumulator = ACPMessageAccumulator()
        accumulator.process(UserMessageChunk.text("Hello"))
        messages = accumulator.finalize()
        assert len(messages) == 1
        assert messages[0].role == "user"
        assert messages[0].content == "Hello"

    def test_multiple_user_chunks(self) -> None:
        """Multiple user chunks are accumulated into one message."""
        accumulator = ACPMessageAccumulator()

        accumulator.process(UserMessageChunk.text("Hello "))
        accumulator.process(UserMessageChunk.text("world"))
        messages = accumulator.finalize()

        assert len(messages) == 1
        assert messages[0].content == "Hello world"

    def test_single_agent_message(self) -> None:
        """Single agent message chunk becomes an assistant ChatMessage."""
        accumulator = ACPMessageAccumulator()
        accumulator.process(AgentMessageChunk.text("Hi there"))
        messages = accumulator.finalize()
        assert len(messages) == 1
        assert messages[0].role == "assistant"
        assert messages[0].content == "Hi there"

    def test_multiple_agent_chunks(self) -> None:
        """Multiple agent chunks are accumulated into one message."""
        accumulator = ACPMessageAccumulator()

        accumulator.process(AgentMessageChunk.text("I am "))
        accumulator.process(AgentMessageChunk.text("Claude"))
        messages = accumulator.finalize()

        assert len(messages) == 1
        assert messages[0].content == "I am Claude"

    def test_user_then_agent_message(self) -> None:
        """User message followed by agent message creates two messages."""
        accumulator = ACPMessageAccumulator()
        accumulator.process(UserMessageChunk.text("Hello"))
        accumulator.process(AgentMessageChunk.text("Hi!"))
        messages = accumulator.finalize()
        assert len(messages) == 2
        assert messages[0].role == "user"
        assert messages[0].content == "Hello"
        assert messages[1].role == "assistant"
        assert messages[1].content == "Hi!"

    def test_parent_id_linking(self) -> None:
        """Messages are linked via parent_id."""
        accumulator = ACPMessageAccumulator()

        accumulator.process(UserMessageChunk.text("Hello"))
        accumulator.process(AgentMessageChunk.text("Hi!"))
        messages = accumulator.finalize()

        assert messages[0].parent_id is None
        assert messages[1].parent_id == messages[0].message_id

    def test_agent_thought_chunks(self) -> None:
        """Agent thought chunks are accumulated into ThinkingPart."""
        accumulator = ACPMessageAccumulator()
        accumulator.process(AgentThoughtChunk.text("thinking..."))
        accumulator.process(AgentMessageChunk.text("Result"))
        messages = accumulator.finalize()
        assert len(messages) == 1
        msg = messages[0]
        assert msg.role == "assistant"
        # Check that the ModelResponse has a ThinkingPart
        assert len(msg.messages) == 1
        response = msg.messages[0]
        assert isinstance(response, ModelResponse)
        thinking_parts = [p for p in response.parts if isinstance(p, ThinkingPart)]
        assert len(thinking_parts) == 1
        assert thinking_parts[0].content == "thinking..."

    def test_tool_call_complete(self) -> None:
        """Completed tool call creates ToolCallPart and ToolReturnPart."""
        accumulator = ACPMessageAccumulator()

        # Tool call start
        accumulator.process(
            ToolCallStart(
                tool_call_id="tc-123",
                title="read_file",
                raw_input={"path": "/tmp/test.txt"},
            )
        )
        # Tool call complete
        accumulator.process(
            ToolCallProgress(
                tool_call_id="tc-123",
                status="completed",
                raw_output="file contents here",
            )
        )
        # Final text
        accumulator.process(AgentMessageChunk.text("Done"))
        messages = accumulator.finalize()
        assert len(messages) == 1
        msg = messages[0]
        assert msg.role == "assistant"
        # Check message structure:
        # ModelResponse(ToolCallPart) -> ModelRequest(ToolReturnPart) -> ModelResponse(TextPart)
        assert len(msg.messages) == 3
        # First: ModelResponse with ToolCallPart
        assert isinstance(msg.messages[0], ModelResponse)
        tool_call_parts = [p for p in msg.messages[0].parts if isinstance(p, ToolCallPart)]
        assert len(tool_call_parts) == 1
        assert tool_call_parts[0].tool_name == "read_file"
        assert tool_call_parts[0].tool_call_id == "tc-123"
        # Second: ModelRequest with ToolReturnPart
        assert isinstance(msg.messages[1], ModelRequest)
        tool_return_parts = [p for p in msg.messages[1].parts if isinstance(p, ToolReturnPart)]
        assert len(tool_return_parts) == 1
        assert tool_return_parts[0].content == "file contents here"
        # Third: ModelResponse with TextPart
        assert isinstance(msg.messages[2], ModelResponse)
        text_parts = [p for p in msg.messages[2].parts if isinstance(p, TextPart)]
        assert len(text_parts) == 1
        assert text_parts[0].content == "Done"

    def test_pending_tool_call(self) -> None:
        """Pending tool call (not completed) is included in response parts."""
        accumulator = ACPMessageAccumulator()
        accumulator.process(
            ToolCallStart(
                tool_call_id="tc-456",
                title="slow_tool",
                raw_input={"arg": "value"},
            )
        )
        # No completion, just finalize
        messages = accumulator.finalize()
        assert len(messages) == 1
        msg = messages[0]
        # Should have one ModelResponse with a pending ToolCallPart
        assert len(msg.messages) == 1
        response = msg.messages[0]
        assert isinstance(response, ModelResponse)
        tool_call_parts = [p for p in response.parts if isinstance(p, ToolCallPart)]
        assert len(tool_call_parts) == 1
        assert tool_call_parts[0].tool_name == "slow_tool"

    def test_metadata_fields(self) -> None:
        """Accumulator passes through metadata fields."""
        accumulator = ACPMessageAccumulator(
            session_id="conv-123",
            agent_name="test-agent",
            model_name="test-model",
        )

        accumulator.process(AgentMessageChunk.text("Hi"))
        messages = accumulator.finalize()
        assert messages[0].session_id == "conv-123"
        assert messages[0].name == "test-agent"
        assert messages[0].model_name == "test-model"

    def test_reset(self) -> None:
        """Reset clears all state."""
        accumulator = ACPMessageAccumulator()
        accumulator.process(AgentMessageChunk.text("Hi"))
        accumulator.reset()
        messages = accumulator.finalize()
        assert messages == []

    def test_process_notification(self) -> None:
        """process_notification extracts update from SessionNotification."""
        accumulator = ACPMessageAccumulator()
        update = AgentMessageChunk.text("Hello")
        notification = SessionNotification(session_id="sess-1", update=update)
        accumulator.process(notification.update)
        messages = accumulator.finalize()

        assert len(messages) == 1
        assert messages[0].content == "Hello"

    def test_process_all_updates(self) -> None:
        """process_all handles list of updates."""
        accumulator = ACPMessageAccumulator()

        updates: list[SessionUpdate] = [
            UserMessageChunk.text("User"),
            AgentMessageChunk.text("Agent"),
        ]
        for item in updates:
            accumulator.process(item)
        messages = accumulator.finalize()

        assert len(messages) == 2

    def test_process_all_notifications(self) -> None:
        """process_all handles list of notifications."""
        accumulator = ACPMessageAccumulator()

        notifications = [
            SessionNotification(session_id="s1", update=UserMessageChunk.text("User")),
            SessionNotification(session_id="s1", update=AgentMessageChunk.text("Agent")),
        ]
        for item in notifications:
            accumulator.process(item.update)
        messages = accumulator.finalize()

        assert len(messages) == 2


class TestACPNotificationsToMessages:
    """Tests for acp_notifications_to_messages convenience function."""

    def test_basic_conversion(self) -> None:
        """Basic conversion of notifications to messages."""
        updates: list[SessionUpdate] = [
            UserMessageChunk.text("Hi"),
            AgentMessageChunk.text("Hello"),
        ]

        messages = acp_notifications_to_messages(updates)

        assert len(messages) == 2
        assert messages[0].role == "user"
        assert messages[1].role == "assistant"

    def test_with_metadata(self) -> None:
        """Conversion with metadata parameters."""
        updates = [AgentMessageChunk.text("Hi")]

        messages = acp_notifications_to_messages(
            updates,
            session_id="conv-1",
            agent_name="agent-1",
            model_name="model-1",
        )

        assert messages[0].session_id == "conv-1"
        assert messages[0].name == "agent-1"
        assert messages[0].model_name == "model-1"

    def test_empty_input(self) -> None:
        """Empty input returns empty list."""
        messages = acp_notifications_to_messages([])
        assert messages == []


if __name__ == "__main__":
    pytest.main([__file__, "-vv"])
