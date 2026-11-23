"""Convert agent stream events to ACP notifications.

This module provides a stateful converter that transforms agent stream events
into ACP session update objects. The converter tracks tool call state but does
not perform any I/O - it yields notification objects that can be emitted
by the caller.

This separation enables easy testing without mocks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal, assert_never
import uuid

from aclientp.schema import (
    AgentMessageChunk,
    AgentPlanUpdate,
    AgentThoughtChunk,
    CompactionUpdate,
    ContentToolCallContent,
    Cost,
    Notice,
    PromptSuggestion,
    PromptSuggestionsUpdate,
    TextContentBlock,
    ToolCallLocation,
    ToolCallProgress,
    ToolCallStart,
    UsageUpdate,
)
from pydantic_ai import (
    FinalResultEvent,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
    PartEndEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
    ToolCallPartDelta,
)

from agentpool.acp_utils import generate_tool_title, infer_tool_kind, to_acp_content_blocks
from agentpool.agents.events import (
    CompactionEvent,
    CustomEvent,
    DiffContentItem,
    FileContentItem,
    LocationContentItem,
    NoticeEvent,
    PlanUpdateEvent,
    PromptSuggestionEvent,
    RetryEvent,
    RunErrorEvent,
    RunStartedEvent,
    StepCompletedEvent,
    StepStartedEvent,
    StreamCompleteEvent,
    SubAgentEvent,
    TaskFinishedEvent,
    TaskStartedEvent,
    TaskUpdatedEvent,
    TerminalContentItem,
    TextContentItem,
    ToolCallCompleteEvent,
    ToolCallProgressEvent,
    ToolCallStartEvent,
)
from agentpool.log import get_logger
from agentpool_server.acp_server.converters import to_usage


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from aclientp.schema import SessionUpdate, Usage
    from aclientp.schema.tool_call import ToolCallContent, ToolCallKind

    from agentpool.agents.events import RichAgentStreamEvent
    from agentpool.agents.events.events import NoticeSeverity, SubAgentType

from agentpool.messaging.message_utils import context_tokens


logger = get_logger(__name__)


@dataclass
class _ToolState:
    """Internal state for a single tool call."""

    tool_call_id: str
    tool_name: str
    title: str
    kind: ToolCallKind
    raw_input: dict[str, Any]
    started: bool = False
    has_content: bool = False


# ============================================================================
# Event Converter
# ============================================================================


@dataclass
class ACPEventConverter:
    """Converts agent stream events to ACP session updates.

    Stateful converter that tracks tool calls and subagent content,
    yielding ACP schema objects without performing I/O.

    Example:
        ```python
        converter = ACPEventConverter()
        async for event in agent.run_stream(...):
            async for update in converter.convert(event):
                await client.session_update(SessionNotification(session_id=sid, update=update))
        ```
    """

    subagent_display_mode: Literal["inline", "tool_box"] = "tool_box"
    """How to display subagent output."""

    use_notices: bool = False
    """Send errors as ``notice`` updates (client advertised ``session.notices``)."""

    use_compaction_updates: bool = False
    """Send ``compaction_update`` updates (client advertised ``session.compaction``)."""

    use_prompt_suggestions: bool = False
    """Send ``prompt_suggestions_update`` (client advertised ``session.prompt_suggestions``)."""

    context_window: int | None = None
    """Context window of the model in tokens; ``usage_update`` is only sent when known."""

    session_cost: Decimal = Decimal(0)
    """Cumulative session cost, including this turn once it completed."""

    # Internal state
    _tool_states: dict[str, _ToolState] = field(default_factory=dict)
    """Active tool call states."""

    _current_tool_inputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    """Current tool inputs by tool_call_id."""

    _subagent_headers: set[str] = field(default_factory=set)
    """Track which subagent headers have been sent (for inline mode)."""

    _subagent_content: dict[str, list[str]] = field(default_factory=dict)
    """Accumulated content per subagent (for tool_box mode)."""

    _subagent_tool_calls: set[str] = field(default_factory=set)
    """Subagent tool calls already announced (repeated start events only update a call)."""

    background_tasks: dict[str, str] = field(default_factory=dict)
    """Descriptions of running background tasks by task ID.

    Background tasks outlive the run (and the converter): pass the session's registry so
    a completion reported in a later turn is recognized. ``reset()`` keeps it.
    """

    _current_message_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    """Message ID for the current agent response."""

    last_usage: Usage | None = field(default=None, init=False)
    """Usage from the last completed stream, if available."""

    def reset(self) -> None:
        """Reset converter state for a new run."""
        self._tool_states.clear()
        self._current_tool_inputs.clear()
        self._subagent_headers.clear()
        self._subagent_content.clear()
        self._current_message_id = str(uuid.uuid4())
        self.last_usage = None

    async def cancel_pending_tools(self) -> AsyncIterator[ToolCallProgress]:
        """Cancel all pending tool calls.

        Yields ToolCallProgress notifications with status="completed" for all
        tool calls that were started but not completed. This should be called
        when the stream is interrupted to properly clean up client-side state.

        Note:
            Uses status="completed" since ACP doesn't have a "cancelled" status.
            This signals to the client that we're done with these tool calls.

        Yields:
            ToolCallProgress notifications for each pending tool call
        """
        for tool_call_id, state in list(self._tool_states.items()):
            if state.started:
                yield ToolCallProgress(tool_call_id=tool_call_id, status="completed")
        # Clean up all state
        self.reset()

    def _get_or_create_tool_state(
        self,
        tool_call_id: str,
        tool_name: str,
        tool_input: dict[str, Any],
    ) -> _ToolState:
        """Get existing tool state or create a new one."""
        if tool_call_id not in self._tool_states:
            self._tool_states[tool_call_id] = _ToolState(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                title=generate_tool_title(tool_name, tool_input),
                kind=infer_tool_kind(tool_name),
                raw_input=tool_input,
            )
        return self._tool_states[tool_call_id]

    def _first_subagent_start(self, tool_call_id: str) -> bool:
        """Whether this is the first start event of a subagent tool call."""
        if tool_call_id in self._subagent_tool_calls:
            return False
        self._subagent_tool_calls.add(tool_call_id)
        return True

    def _cleanup_tool_state(self, tool_call_id: str) -> None:
        """Remove tool state after completion."""
        self._tool_states.pop(tool_call_id, None)
        self._current_tool_inputs.pop(tool_call_id, None)

    async def convert(  # noqa: PLR0915
        self, event: RichAgentStreamEvent[Any]
    ) -> AsyncIterator[SessionUpdate]:
        """Convert an agent event to zero or more ACP session updates."""
        from aclientp.schema import (
            FileEditToolCallContent,
            PlanEntry as ACPPlanEntry,
            TerminalToolCallContent,
        )

        from agentpool_server.acp_server.syntax_detection import format_zed_code_block

        match event:
            # Text output
            case (
                PartStartEvent(part=TextPart(content=delta))
                | PartDeltaEvent(delta=TextPartDelta(content_delta=delta))
            ):
                yield AgentMessageChunk.text(delta, message_id=self._current_message_id)

            # Thinking/reasoning
            case (
                PartStartEvent(part=ThinkingPart(content=delta))
                | PartDeltaEvent(delta=ThinkingPartDelta(content_delta=delta))
            ):
                # Signature-only deltas carry no text; nothing to show
                if delta:
                    yield AgentThoughtChunk.text(delta, message_id=self._current_message_id)

            case PartStartEvent(part=part):
                logger.debug("Received unhandled PartStartEvent", part=part)

            # Tool call streaming delta
            case PartDeltaEvent(delta=ToolCallPartDelta() as delta):
                if delta_part := delta.as_part():
                    tool_call_id = delta_part.tool_call_id
                    try:
                        tool_input = delta_part.args_as_dict()
                    except ValueError:
                        pass  # Args still streaming, not valid JSON yet
                    else:
                        self._current_tool_inputs[tool_call_id] = tool_input
                        state = self._get_or_create_tool_state(
                            tool_call_id, delta_part.tool_name, tool_input
                        )
                        if not state.started:
                            state.started = True
                            yield ToolCallStart(
                                tool_call_id=tool_call_id,
                                title=state.title,
                                name=state.tool_name,
                                kind=state.kind,
                                raw_input=state.raw_input,
                                status="pending",
                            )

            # Tool call finished (canonical lifecycle end for all agent types)
            case ToolCallCompleteEvent(tool_call_id=tc_id, tool_result=out, error=error):
                if error is not None:
                    content = ContentToolCallContent.text(f"Error: {error}")
                    yield ToolCallProgress(tool_call_id=tc_id, status="failed", content=[content])
                elif (tool_state := self._tool_states.get(tc_id)) and tool_state.has_content:
                    yield ToolCallProgress(tool_call_id=tc_id, status="completed", raw_output=out)
                else:
                    converted = to_acp_content_blocks(out)
                    yield ToolCallProgress(
                        tool_call_id=tc_id,
                        status="completed",
                        raw_output=out,
                        content=[ContentToolCallContent(content=block) for block in converted],
                    )
                self._cleanup_tool_state(tc_id)

            # Tool call started; repeated starts (e.g. from the tool itself) update it
            case ToolCallStartEvent(
                tool_call_id=tc_id,
                tool_name=tool_name,
                title=title,
                kind=kind,
                locations=loc_items,
                raw_input=raw_input,
                field_meta=meta,
            ):
                state = self._get_or_create_tool_state(tc_id, tool_name, raw_input or {})
                acp_locations = [ToolCallLocation(path=i.path, line=i.line) for i in loc_items]
                # If not started, send start notification
                if not state.started:
                    state.started = True
                    yield ToolCallStart(
                        tool_call_id=tc_id,
                        title=title,
                        name=state.tool_name,
                        kind=kind,
                        raw_input=raw_input,
                        locations=acp_locations or None,
                        status="pending",
                        field_meta=meta,
                    )
                else:
                    # Send update with tool-provided details
                    yield ToolCallProgress(
                        tool_call_id=tc_id,
                        title=title,
                        kind=kind,
                        locations=acp_locations or None,
                        field_meta=meta,
                    )

            # Tool progress event - create state if needed (tool may emit progress before SDK event)
            case ToolCallProgressEvent(
                tool_call_id=tool_call_id,
                title=title,
                # status=status,
                items=items,
                progress=progress,
                total=total,
                message=message,
                tool_input=tool_input,
                field_meta=meta,
            ) if tool_call_id:
                # Get or create state - handles race where tool emits before SDK event
                state = self._get_or_create_tool_state(tool_call_id, "unknown", {})
                # Streamed calls start with empty args; the final ones come with a progress
                new_input = tool_input if tool_input not in (None, state.raw_input) else None
                if new_input is not None:
                    state.raw_input = new_input
                # Emit start if this is the first event for this tool call
                if not state.started:
                    state.started = True
                    yield ToolCallStart(
                        tool_call_id=tool_call_id,
                        title=title or state.title,
                        name=None if state.tool_name == "unknown" else state.tool_name,
                        kind=state.kind,
                        raw_input=state.raw_input,
                        status="pending",
                    )
                acp_content: list[ToolCallContent] = []
                locations: list[ToolCallLocation] = []
                for item in items:
                    match item:
                        case TerminalContentItem(terminal_id=tid):
                            acp_content.append(TerminalToolCallContent(terminal_id=tid))
                        case TextContentItem(text=text):
                            acp_content.append(ContentToolCallContent.text(text))
                        case FileContentItem(
                            content=file_content,
                            path=file_path,
                            start_line=start_line,
                            end_line=end_line,
                        ):
                            formatted = format_zed_code_block(
                                file_content, file_path, start_line, end_line
                            )
                            acp_content.append(ContentToolCallContent.text(formatted))
                            locations.append(ToolCallLocation(path=file_path, line=start_line or 0))
                        case DiffContentItem(path=diff_path, old_text=old, new_text=new):
                            acp_content.append(
                                FileEditToolCallContent(path=diff_path, old_text=old, new_text=new)
                            )
                            locations.append(ToolCallLocation(path=diff_path))
                            state.has_content = True
                        case LocationContentItem(path=loc_path, line=loc_line):
                            location = ToolCallLocation(path=loc_path, line=loc_line)
                            locations.append(location)

                # Build title: use provided title, or format MCP numeric progress
                effective_title = title
                if not effective_title and (progress is not None or message):
                    # MCP-style numeric progress - format into title
                    if progress is not None and total:
                        pct = int(progress / total * 100)
                        effective_title = f"{message} ({pct}%)" if message else f"Progress: {pct}%"
                    elif message:
                        effective_title = message

                # TODO: Progress events shouldn't control completion status.
                # The file_operation helper sets status="completed" on success, but that's
                # emitted mid-operation (before content display). Only ToolCallCompleteEvent
                # should mark a tool as completed. For now, hardcode in_progress.
                yield ToolCallProgress(
                    tool_call_id=tool_call_id,
                    title=effective_title,
                    status="in_progress",
                    content=acp_content or None,
                    locations=locations or None,
                    # Clients read e.g. the command from rawInput; sent when it changed
                    raw_input=new_input,
                    field_meta=meta,
                )
                if acp_content:
                    state.has_content = True

            case FinalResultEvent():
                pass  # No notification needed

            case StreamCompleteEvent(message=message):
                if message.usage.total_tokens > 0:
                    self.last_usage = to_usage(message.usage)
                if message.cost_info:
                    self.session_cost += message.cost_info.total_cost
                # Without the window size a fill level would be made up, so skip the update
                if self.context_window and (used := context_tokens(message)) is not None:
                    cost = (
                        Cost(amount=float(self.session_cost), currency="USD")
                        if self.session_cost
                        else None
                    )
                    yield UsageUpdate(used=used, size=self.context_window, cost=cost)

            case PlanUpdateEvent(entries=entries):
                acp_entries = [
                    ACPPlanEntry(
                        content=e.content,
                        priority=e.priority,
                        # ACP has no "cancelled": the entry is done with
                        status="completed" if e.status == "cancelled" else e.status,
                    )
                    for e in entries
                ]
                yield AgentPlanUpdate(entries=acp_entries)

            case PromptSuggestionEvent(suggestions=suggestions) if self.use_prompt_suggestions:
                yield PromptSuggestionsUpdate(
                    suggestions=[
                        PromptSuggestion(prompt=[TextContentBlock(text=s)]) for s in suggestions
                    ]
                )

            case CompactionEvent(phase=phase, error=error) if self.use_compaction_updates:
                # One compaction per run; a later "completed" event patches the same entity
                compaction_id = f"compaction-{self._current_message_id}"
                status = {"starting": "in_progress", "completed": "completed", "failed": "failed"}
                yield CompactionUpdate(
                    compaction_id=compaction_id, status=status[phase], error=error
                )

            case CompactionEvent(phase="starting"):
                text = event.format()
                yield AgentMessageChunk.text(text, message_id=self._current_message_id)

            case CompactionEvent(phase="failed", error=error):
                text = f"\n\n❌ **Compaction failed**{f': {error}' if error else ''}\n\n"
                yield AgentMessageChunk.text(text, message_id=self._current_message_id)

            case NoticeEvent(severity=severity, title=title, description=description) if (
                self.use_notices
            ):
                yield Notice(severity=severity, title=title, description=description)

            case NoticeEvent():
                # Client lacks notice support: rendering inline beats dropping user-facing info
                yield AgentMessageChunk.text(event.format(), message_id=self._current_message_id)

            case TaskStartedEvent(task_id=task_id, description=description, background=True):
                self.background_tasks[task_id] = description

            case TaskUpdatedEvent(task_id=task_id, background=True):
                self.background_tasks.setdefault(task_id, event.description or "Background task")

            case TaskFinishedEvent(task_id=task_id, status=status, summary=summary) if (
                task_id in self.background_tasks
            ):
                description = self.background_tasks.pop(task_id)
                title = f"Background task {status}: {description}"
                if self.use_notices:
                    severity: NoticeSeverity = "info" if status == "completed" else "warning"
                    yield Notice(severity=severity, title=title, description=summary)
                else:
                    text = f"\n\n{title}{f': {summary}' if summary else ''}\n\n"
                    yield AgentMessageChunk.text(text, message_id=self._current_message_id)

            case TaskStartedEvent() | TaskUpdatedEvent() | TaskFinishedEvent():
                pass  # Foreground tasks are shown through the tool call that runs them

            case RetryEvent() if self.use_notices:
                yield Notice(
                    severity="warning", title="Retrying request", description=event.format()
                )

            case RetryEvent():
                text = f"\n\n⏳ {event.format()}\n\n"
                yield AgentMessageChunk.text(text, message_id=self._current_message_id)

            case SubAgentEvent(
                source_name=source_name,
                source_type=source_type,
                event=inner_event,
                depth=depth,
            ):
                if self.subagent_display_mode == "tool_box":
                    async for update in self._convert_subagent_tool_box(
                        source_name, source_type, inner_event, depth
                    ):
                        yield update
                else:
                    async for update in self._convert_subagent_inline(
                        source_name, source_type, inner_event, depth
                    ):
                        yield update

            case RunErrorEvent(message=message, agent_name=agent_name) if self.use_notices:
                yield Notice(severity="error", title=agent_name or "Error", description=message)

            case RunErrorEvent(message=message, agent_name=agent_name):
                # Display error as agent text with formatting
                agent_prefix = f"[{agent_name}] " if agent_name else ""
                error_text = f"\n\n❌ **Error**: {agent_prefix}{message}\n\n"
                yield AgentMessageChunk.text(error_text, message_id=self._current_message_id)

            case _:
                logger.debug("Unhandled event", event_type=type(event).__name__)

    async def _convert_subagent_inline(
        self,
        source_name: str,
        source_type: SubAgentType,
        inner_event: RichAgentStreamEvent[Any],
        depth: int,
    ) -> AsyncIterator[SessionUpdate]:
        """Convert subagent event to inline text notifications."""
        indent = "  " * depth
        icon = "🤖" if source_type == "agent" else "👥"

        match inner_event:
            case (
                PartStartEvent(part=TextPart(content=delta))
                | PartDeltaEvent(delta=TextPartDelta(content_delta=delta))
            ):
                header_key = f"{source_name}:{depth}"
                if header_key not in self._subagent_headers:
                    self._subagent_headers.add(header_key)
                    yield AgentMessageChunk.text(
                        f"\n{indent}{icon} **{source_name}**: ", message_id=self._current_message_id
                    )
                yield AgentMessageChunk.text(delta, message_id=self._current_message_id)

            case (
                PartStartEvent(part=ThinkingPart(content=delta))
                | PartDeltaEvent(delta=ThinkingPartDelta(content_delta=delta))
            ):
                yield AgentThoughtChunk.text(
                    f"{indent}[{source_name}] {delta or ''}", message_id=self._current_message_id
                )

            case ToolCallStartEvent(tool_call_id=tc_id, tool_name=tool_name) if (
                self._first_subagent_start(tc_id)
            ):
                text = f"\n{indent}🔧 [{source_name}] Using tool: {tool_name}\n"
                yield AgentMessageChunk.text(text, message_id=self._current_message_id)

            case ToolCallCompleteEvent(tool_name=tool_name, error=str() as error_msg):
                text = f"{indent}❌ [{source_name}] {tool_name}: {error_msg}\n"
                yield AgentMessageChunk.text(text, message_id=self._current_message_id)

            case ToolCallCompleteEvent(tool_result=content, tool_name=tool_name):
                result_str = str(content)
                if len(result_str) > 200:  # noqa: PLR2004
                    result_str = result_str[:200] + "..."
                text = f"{indent}✅ [{source_name}] {tool_name}: {result_str}\n"
                yield AgentMessageChunk.text(text, message_id=self._current_message_id)

            case StreamCompleteEvent():
                header_key = f"{source_name}:{depth}"
                self._subagent_headers.discard(header_key)
                yield AgentMessageChunk.text(
                    f"\n{indent}---\n", message_id=self._current_message_id
                )

            case (
                CompactionEvent()
                | FinalResultEvent()
                | FunctionToolCallEvent()
                | FunctionToolResultEvent()
                | PartDeltaEvent()
                | PartEndEvent()
                | PartStartEvent()
                | PlanUpdateEvent()
                | PromptSuggestionEvent()
                | NoticeEvent()
                | RetryEvent()
                | TaskStartedEvent()
                | TaskUpdatedEvent()
                | TaskFinishedEvent()
                | RunErrorEvent()
                | RunStartedEvent()
                | StepStartedEvent()
                | StepCompletedEvent()
                | SubAgentEvent()
                | ToolCallCompleteEvent()
                | ToolCallProgressEvent()
                | ToolCallStartEvent()
                | CustomEvent()
            ):
                pass  # TODO

            case _ as unreachable:
                assert_never(unreachable)

    async def _convert_subagent_tool_box(
        self,
        source_name: str,
        source_type: SubAgentType,
        inner_event: RichAgentStreamEvent[Any],
        depth: int,
    ) -> AsyncIterator[SessionUpdate]:
        """Convert subagent event to tool box notifications."""
        state_key = f"subagent:{source_name}:{depth}"
        icon = "🤖" if source_type == "agent" else "👥"

        if state_key not in self._subagent_content:
            self._subagent_content[state_key] = []

        accumulated = self._subagent_content[state_key]

        match inner_event:
            case (
                PartStartEvent(part=TextPart(content=delta))
                | PartDeltaEvent(delta=TextPartDelta(content_delta=delta))
            ):
                accumulated.append(delta)
                async for n in self._emit_subagent_progress(state_key, f"{icon} {source_name}"):
                    yield n

            case (
                PartStartEvent(part=ThinkingPart(content=delta))
                | PartDeltaEvent(delta=ThinkingPartDelta(content_delta=delta))
            ):
                if delta:
                    accumulated.append(f"💭 {delta}")
                    async for n in self._emit_subagent_progress(state_key, f"{icon} {source_name}"):
                        yield n

            case ToolCallStartEvent(tool_call_id=tc_id, tool_name=tool_name) if (
                self._first_subagent_start(tc_id)
            ):
                accumulated.append(f"\n🔧 Using tool: {tool_name}\n")
                async for n in self._emit_subagent_progress(state_key, f"{icon} {source_name}"):
                    yield n

            case ToolCallCompleteEvent(tool_name=tool_name, error=str() as error_msg):
                accumulated.append(f"❌ {tool_name}: {error_msg}\n")
                async for n in self._emit_subagent_progress(state_key, f"{icon} {source_name}"):
                    yield n

            case ToolCallCompleteEvent(tool_result=content, tool_name=tool_name):
                result_str = str(content)
                if len(result_str) > 200:  # noqa: PLR2004
                    result_str = result_str[:200] + "..."
                accumulated.append(f"✅ {tool_name}: {result_str}\n")
                async for n in self._emit_subagent_progress(state_key, f"{icon} {source_name}"):
                    yield n

            case StreamCompleteEvent():
                # Complete the tool call
                if state_key in self._tool_states:
                    yield ToolCallProgress(tool_call_id=state_key, status="completed")
                    self._cleanup_tool_state(state_key)
                self._subagent_content.pop(state_key, None)

            case (
                CompactionEvent()
                | FinalResultEvent()
                | FunctionToolCallEvent()
                | FunctionToolResultEvent()
                | PartDeltaEvent()
                | PartEndEvent()
                | PartStartEvent()
                | PlanUpdateEvent()
                | PromptSuggestionEvent()
                | NoticeEvent()
                | RetryEvent()
                | TaskStartedEvent()
                | TaskUpdatedEvent()
                | TaskFinishedEvent()
                | RunErrorEvent()
                | RunStartedEvent()
                | StepStartedEvent()
                | StepCompletedEvent()
                | SubAgentEvent()
                | ToolCallCompleteEvent()
                | ToolCallProgressEvent()
                | ToolCallStartEvent()
                | CustomEvent()
            ):
                pass  # TODO

            case _ as unreachable:
                assert_never(unreachable)

    async def _emit_subagent_progress(
        self, state_key: str, title: str
    ) -> AsyncIterator[SessionUpdate]:
        """Emit tool call notifications for subagent content."""
        accumulated = self._subagent_content.get(state_key, [])
        content_text = "".join(accumulated)

        if state_key not in self._tool_states:
            # Create state and emit start
            self._tool_states[state_key] = _ToolState(
                tool_call_id=state_key,
                tool_name="delegate",
                title=title,
                kind="other",
                raw_input={},
                started=True,
            )
            yield ToolCallStart(
                tool_call_id=state_key,
                title=title,
                name="delegate",
                kind="other",
                raw_input={},
                status="pending",
            )

        # Emit progress update
        yield ToolCallProgress(
            tool_call_id=state_key,
            title=title,
            status="in_progress",
            content=[ContentToolCallContent.text(content_text)],
        )
