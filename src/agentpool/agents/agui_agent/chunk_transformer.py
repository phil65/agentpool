"""Chunk transformer for AG-UI events.

Transforms TEXT_MESSAGE_CHUNK and TOOL_CALL_CHUNK events into proper
START/CONTENT/END sequences for easier downstream processing.
"""

from typing import TYPE_CHECKING

from agentpool.log import get_logger


if TYPE_CHECKING:
    from ag_ui.core import (
        Event,
        ReasoningMessageChunkEvent,
        TextMessageChunkEvent,
        ToolCallChunkEvent,
    )


logger = get_logger(__name__)


class ChunkTransformer:
    """Transforms CHUNK events into proper START/CONTENT/END sequences.

    AG-UI supports two streaming modes:
    1. Explicit: TEXT_MESSAGE_START -> TEXT_MESSAGE_CONTENT* -> TEXT_MESSAGE_END
    2. Chunk: TEXT_MESSAGE_CHUNK* (implicit start/end based on message_id changes)

    This transformer normalizes chunk mode into explicit mode for consistent handling.
    Same applies for TOOL_CALL_CHUNK events.
    """

    def __init__(self) -> None:
        """Initialize transformer."""
        # Track active text message: message_id -> role
        self._active_text: dict[str, str] | None = None
        # Track active tool call: tool_call_id -> (name, parent_message_id)
        self._active_tool: dict[str, tuple[str, str | None]] | None = None
        # Track active reasoning message: message_id
        self._active_reasoning: str | None = None

    def transform(self, event: Event) -> list[Event]:
        """Transform a single event, potentially expanding chunks.

        Args:
            event: Input event

        Returns:
            List of output events (may be empty, single, or multiple)
        """
        from ag_ui.core import (
            ReasoningMessageChunkEvent,
            ReasoningMessageEndEvent,
            ReasoningMessageStartEvent,
            RunErrorEvent,
            RunFinishedEvent,
            TextMessageChunkEvent,
            TextMessageEndEvent,
            TextMessageStartEvent,
            ToolCallChunkEvent,
            ToolCallEndEvent,
            ToolCallStartEvent,
        )

        match event:
            case TextMessageChunkEvent():
                return self._handle_text_chunk(event)

            case ToolCallChunkEvent():
                return self._handle_tool_chunk(event)

            case ReasoningMessageChunkEvent():
                return self._handle_reasoning_chunk(event)

            # These events close any pending chunks
            case (
                TextMessageStartEvent()
                | TextMessageEndEvent()
                | ToolCallStartEvent()
                | ToolCallEndEvent()
                | ReasoningMessageStartEvent()
                | ReasoningMessageEndEvent()
                | RunFinishedEvent()
                | RunErrorEvent()
            ):
                close_events = self._close_all_pending()
                return [*close_events, event]

            case _:
                # Pass through other events unchanged
                return [event]

    def _handle_text_chunk(self, event: TextMessageChunkEvent) -> list[Event]:
        """Handle TEXT_MESSAGE_CHUNK event."""
        from ag_ui.core import TextMessageContentEvent, TextMessageStartEvent

        result: list[Event] = []
        message_id = event.message_id
        role = event.role or "assistant"
        delta = event.delta

        # Check if we need to close current text message (different ID)
        if self._active_text is not None:
            current_id = next(iter(self._active_text.keys()))
            if message_id and message_id != current_id:
                result.extend(self._close_text_message())

        # Start new message if needed
        if self._active_text is None and message_id:
            self._active_text = {message_id: role}
            start_event = TextMessageStartEvent(message_id=message_id, role=role)
            result.append(start_event)

        # Emit content if we have delta and active message
        if delta and self._active_text:
            current_id = next(iter(self._active_text.keys()))
            content_event = TextMessageContentEvent(message_id=current_id, delta=delta)
            result.append(content_event)

        return result

    def _handle_tool_chunk(self, event: ToolCallChunkEvent) -> list[Event]:
        """Handle TOOL_CALL_CHUNK event."""
        from ag_ui.core import ToolCallArgsEvent, ToolCallStartEvent

        result: list[Event] = []
        tool_call_id = event.tool_call_id
        tool_name = event.tool_call_name
        parent_id = event.parent_message_id
        delta = event.delta

        # Check if we need to close current tool call (different ID)
        if self._active_tool is not None:
            current_id = next(iter(self._active_tool.keys()))
            if tool_call_id and tool_call_id != current_id:
                result.extend(self._close_tool_call())

        # Start new tool call if needed
        if self._active_tool is None and tool_call_id and tool_name:
            self._active_tool = {tool_call_id: (tool_name, parent_id)}
            start_event = ToolCallStartEvent(
                tool_call_id=tool_call_id,
                tool_call_name=tool_name,
                parent_message_id=parent_id,
            )
            result.append(start_event)

        # Emit args if we have delta and active tool call
        if delta and self._active_tool:
            current_id = next(iter(self._active_tool.keys()))
            args_event = ToolCallArgsEvent(tool_call_id=current_id, delta=delta)
            result.append(args_event)

        return result

    def _close_text_message(self) -> list[Event]:
        """Close active text message."""
        from ag_ui.core import TextMessageEndEvent

        if self._active_text is None:
            return []

        message_id = next(iter(self._active_text.keys()))
        self._active_text = None

        end_event = TextMessageEndEvent(message_id=message_id)
        logger.debug("Chunk transformer: TEXT_MESSAGE_END", message_id=message_id)
        return [end_event]

    def _close_tool_call(self) -> list[Event]:
        """Close active tool call."""
        from ag_ui.core import ToolCallEndEvent

        if self._active_tool is None:
            return []

        tool_call_id = next(iter(self._active_tool.keys()))
        self._active_tool = None
        end_event = ToolCallEndEvent(tool_call_id=tool_call_id)
        logger.debug("Chunk transformer: TOOL_CALL_END", tool_call_id=tool_call_id)
        return [end_event]

    def _handle_reasoning_chunk(self, event: ReasoningMessageChunkEvent) -> list[Event]:
        """Handle REASONING_MESSAGE_CHUNK event."""
        from ag_ui.core import ReasoningMessageContentEvent, ReasoningMessageStartEvent

        result: list[Event] = []
        message_id = event.message_id
        delta = event.delta

        # Check if we need to close current reasoning message (different ID)
        if (
            self._active_reasoning is not None
            and message_id
            and message_id != self._active_reasoning
        ):
            result.extend(self._close_reasoning_message())

        # Start new reasoning message if needed
        if self._active_reasoning is None and message_id:
            self._active_reasoning = message_id
            start_event = ReasoningMessageStartEvent(message_id=message_id, role="reasoning")
            result.append(start_event)

        # Emit content if we have delta and active reasoning message
        if delta and self._active_reasoning:
            event_ = ReasoningMessageContentEvent(message_id=self._active_reasoning, delta=delta)
            result.append(event_)

        return result

    def _close_reasoning_message(self) -> list[Event]:
        """Close active reasoning message."""
        from ag_ui.core import ReasoningMessageEndEvent

        if self._active_reasoning is None:
            return []

        message_id = self._active_reasoning
        self._active_reasoning = None

        end_event = ReasoningMessageEndEvent(message_id=message_id)
        logger.debug("Chunk transformer: REASONING_MESSAGE_END", message_id=message_id)
        return [end_event]

    def _close_all_pending(self) -> list[Event]:
        """Close all pending chunks (text, tool, and reasoning)."""
        result: list[Event] = []
        result.extend(self._close_text_message())
        result.extend(self._close_tool_call())
        result.extend(self._close_reasoning_message())
        return result

    def flush(self) -> list[Event]:
        """Flush any pending events at end of stream.

        Call this when the stream ends to ensure all pending
        messages/tool calls are properly closed.

        Returns:
            List of closing events
        """
        return self._close_all_pending()

    def reset(self) -> None:
        """Reset transformer state."""
        self._active_text = None
        self._active_tool = None
        self._active_reasoning = None
