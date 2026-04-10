"""Convert between Codex and AgentPool types.

Provides converters for:
- Event conversion (Codex streaming events -> AgentPool events)
- MCP server configs (Native configs -> Codex types)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from codexed.models import (
    ThreadTokenUsageUpdatedEvent,
    TurnCompletedEvent,
    TurnStartedEvent,
)
from pydantic_ai import PartEndEvent, TextPart, ThinkingPart

from agentpool.agents.codex_agent.codex_converters import (
    _format_tool_result,
    _thread_item_to_tool_call_part,
)
from agentpool.agents.events import (
    CompactionEvent,
    PartDeltaEvent,
    PartStartEvent,
    PlanUpdateEvent,
    TextContentItem,
    ToolCallCompleteEvent,
    ToolCallProgressEvent,
    ToolCallStartEvent,
)
from agentpool.utils.streams.streamed_response import StreamedResponse
from agentpool.utils.time_utils import get_now
from agentpool.utils.todos import PlanEntry


if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from datetime import datetime

    from codexed.models import CodexEvent, TokenUsageBreakdown, TurnStatus

    from agentpool.agents.events import RichAgentStreamEvent


@dataclass(kw_only=True)
class CodexStreamedResponse(StreamedResponse):
    """Streamed codex response."""

    stream: AsyncIterator[CodexEvent]
    _timestamp: datetime = field(default_factory=get_now)
    _model_name: str | None = None
    _token_usage_data: TokenUsageBreakdown | None = None
    _turn_status: TurnStatus | None = None
    _current_turn_id: str | None = None

    async def _get_event_iterator(self) -> AsyncIterator[RichAgentStreamEvent[Any]]:  # noqa: PLR0915
        from codexed.models import (
            ItemAgentMessageDeltaNotification,
            ItemCommandExecutionOutputDeltaNotification,
            ItemCompletedEvent,
            ItemFileChangeOutputDeltaNotification,
            ItemMcpToolCallProgressNotification,
            ItemReasoningTextDeltaNotification,
            ItemStartedEvent,
            ThreadCompactedMessage,
            ThreadItemCommandExecution,
            ThreadItemFileChange,
            ThreadItemMcpToolCall,
            TurnPlanUpdatedMessage,
        )

        # Accumulation state for streaming tool outputs
        tool_outputs: dict[str, list[str]] = {}
        active_part: Literal["text", "thinking", "tool"] | None = None
        part_index = 0

        async for event in self.stream:
            match event:
                case TurnStartedEvent(params=data):
                    self._current_turn_id = data.turn.id
                case TurnCompletedEvent(params=data):
                    self._turn_status = data.turn.status
                case ThreadTokenUsageUpdatedEvent(params=data):
                    self._token_usage_data = data.token_usage.last
                # === Stateful: Accumulate command execution output ===
                case ItemCommandExecutionOutputDeltaNotification(params=data):
                    item_id = data.item_id
                    tool_outputs.setdefault(item_id, []).append(data.delta)
                    # Emit accumulated progress with replace semantics, wrapped in code block
                    output = "".join(tool_outputs[item_id])
                    items = [TextContentItem(text=f"```\n{output}\n```")]
                    yield ToolCallProgressEvent(
                        tool_call_id=item_id, items=items, replace_content=True
                    )

                # File change output delta - ignore the summary, we show diff from item/started
                case ItemFileChangeOutputDeltaNotification():
                    # The outputDelta is just "Success. Updated..." summary - not useful
                    # We already emitted the actual diff content in item/started
                    pass

                case ItemAgentMessageDeltaNotification(params=data):
                    if active_part != "text":
                        if active_part == "thinking":
                            yield PartEndEvent(index=part_index, part=ThinkingPart(content=""))
                            part_index += 1
                        yield PartStartEvent.text(index=part_index, content="")
                        active_part = "text"
                    yield PartDeltaEvent.text(index=part_index, content=data.delta)

                case ItemReasoningTextDeltaNotification(params=data):
                    if active_part != "thinking":
                        if active_part == "text":
                            yield PartEndEvent(index=part_index, part=TextPart(content=""))
                            part_index += 1
                        yield PartStartEvent.thinking(index=part_index, content="")
                        active_part = "thinking"
                    yield PartDeltaEvent.thinking(index=part_index, content=data.delta)

                case ItemStartedEvent(params=data):
                    # Close any open text/thinking part before a tool call
                    if active_part == "text":
                        yield PartEndEvent(index=part_index, part=TextPart(content=""))
                        part_index += 1
                    elif active_part == "thinking":
                        yield PartEndEvent(index=part_index, part=ThinkingPart(content=""))
                        part_index += 1
                    active_part = "tool"
                    if part := _thread_item_to_tool_call_part(data.item):
                        # Extract title based on tool type
                        match data.item:
                            case ThreadItemCommandExecution(command=command):
                                title = f"Execute: {command}"
                            case ThreadItemFileChange(changes=changes):
                                # Build title from file paths
                                paths = [c.path for c in changes[:3]]  # First 3 paths
                                if len(changes) > 3:  # noqa: PLR2004
                                    title = f"Edit: {', '.join(paths)} (+{len(changes) - 3} more)"
                                else:
                                    title = f"Edit: {', '.join(paths)}"
                            case ThreadItemMcpToolCall(tool=tool):
                                title = f"Call {tool}"
                            case _:
                                title = f"Call {part.tool_name}"

                        yield ToolCallStartEvent(
                            tool_call_id=part.tool_call_id,
                            tool_name=part.tool_name,
                            title=title,
                            raw_input=part.args_as_dict(),
                        )

                        # For file changes, immediately emit the diff as progress
                        if isinstance(data.item, ThreadItemFileChange):
                            diff_parts = []
                            for change in data.item.changes:
                                diff_parts.append(f"{change.kind.type.upper()}: {change.path}")
                                if change.diff:
                                    diff_parts.append(change.diff)
                            if diff_parts:
                                items = [TextContentItem(text="\n".join(diff_parts))]
                                yield ToolCallProgressEvent(
                                    tool_call_id=part.tool_call_id, items=items
                                )

                # === Stateful: Tool/command completed - clean up accumulator ===
                case ItemCompletedEvent(params=data):
                    item = data.item
                    # Clean up accumulated output for this item
                    tool_outputs.pop(item.id, None)
                    if part := _thread_item_to_tool_call_part(item):
                        yield ToolCallCompleteEvent(
                            tool_name=part.tool_name,
                            tool_call_id=part.tool_call_id,
                            tool_input=part.args_as_dict(),
                            tool_result=await _format_tool_result(item),
                            agent_name="codex",  # Will be overridden by agent
                            message_id=data.turn_id,
                        )

                # === Stateless: MCP tool call progress ===
                case ItemMcpToolCallProgressNotification(params=data):
                    yield ToolCallProgressEvent(tool_call_id=data.item_id, message=data.message)

                # === Stateless: Thread compacted ===
                case ThreadCompactedMessage(params=data):
                    yield CompactionEvent(session_id=data.thread_id, phase="completed")

                # === Stateless: Turn plan updated ===
                case TurnPlanUpdatedMessage(params=data):
                    entries = [
                        PlanEntry(
                            content=step.step,
                            priority="medium",  # Codex doesn't provide priority
                            status="in_progress" if step.status == "inProgress" else step.status,
                        )
                        for step in data.plan
                    ]
                    yield PlanUpdateEvent(entries=entries)

                # Ignore other events (token usage, turn started/completed, etc.)
                case _:
                    pass

        # Emit end event for any open part
        match active_part:
            case "text":
                yield PartEndEvent(index=part_index, part=TextPart(content=""))
            case "thinking":
                yield PartEndEvent(index=part_index, part=ThinkingPart(content=""))

    @property
    def model_name(self) -> str:
        """Get the model name of the response."""
        assert self._model_name
        return self._model_name

    @property
    def timestamp(self) -> datetime:
        """Get the timestamp of the response."""
        return self._timestamp
