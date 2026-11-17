"""OpenCode stream adapter.

Translates RichAgentStreamEvent objects from the agent event system
into OpenCode SSE Event objects. The adapter yields events; broadcasting
and persistence are handled by the caller.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, assert_never

from pydantic_ai import FunctionToolCallEvent, RunUsage
from pydantic_ai.messages import (
    PartDeltaEvent,
    PartStartEvent,
    TextPart as PydanticTextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
    ToolCallPart as PydanticToolCallPart,
)

from agentpool.agents.events import (
    CompactionEvent,
    DiffContentItem,
    FileContentItem,
    LocationContentItem,
    NoticeEvent,
    RunErrorEvent,
    StreamCompleteEvent,
    SubAgentEvent,
    TerminalContentItem,
    TextContentItem,
    ToolCallCompleteEvent,
    ToolCallProgressEvent,
    ToolCallStartEvent,
)
from agentpool.agents.events.infer_info import derive_rich_tool_info
from agentpool.log import get_logger
from agentpool.utils import identifiers as identifier
from agentpool.utils.pydantic_ai_helpers import safe_args_as_dict
from agentpool.utils.time_utils import now_ms
from agentpool_server.opencode_server.converters import _convert_params_for_ui
from opencode_sdk.models import (
    FileEditedEvent,
    PartUpdatedEvent,
    ReasoningPart,
    SessionCompactedEvent,
    SessionErrorEvent,
    StepFinishPart,
    TextPart,
    TimeStart,
    TimeStartEnd,
    TimeStartEndCompacted,
    TimeStartEndOptional,
    Tokens,
    ToolPart,
    ToolStateCompleted,
    ToolStateError,
    ToolStatePending,
    ToolStateRunning,
    TuiToastShowEvent,
)


if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterator, Sequence

    from agentpool.agents.events import ToolCallContentItem
    from agentpool.agents.events.events import RichAgentStreamEvent, SubAgentType
    from agentpool.messaging.messages import TokenCost
    from opencode_sdk.models import (
        AssistantMessage,
        Event,
        MessageWithParts,
        ToolState,
    )

logger = get_logger(__name__)


@dataclass
class OpenCodeStreamAdapter:
    """Translates agent stream events into OpenCode SSE events.

    Owns all mutable tracking state (tool parts, text accumulation, token
    counters). Yields OpenCode ``Event`` objects ready for broadcasting.
    """

    assistant_msg: MessageWithParts[AssistantMessage]
    """The mutable assistant message to append parts to."""

    working_dir: str
    """Working directory for path context."""

    on_file_paths: Callable[[list[str]], None] | None = None
    """Optional callback invoked with file paths discovered during tool progress events."""

    # --- mutable tracking state ---
    _response_text: str = field(default="", init=False)
    _usage: RunUsage = field(default_factory=RunUsage, init=False)
    _cost_info: TokenCost | None = field(default=None, init=False)

    _tool_parts: dict[str, ToolPart] = field(default_factory=dict, init=False)
    _tool_outputs: dict[str, str] = field(default_factory=dict, init=False)
    _tool_inputs: dict[str, dict[str, Any]] = field(default_factory=dict, init=False)

    _text_part: TextPart | None = field(default=None, init=False)
    _reasoning_part: ReasoningPart | None = field(default=None, init=False)
    _stream_start_ms: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self._stream_start_ms = now_ms()

    # --- public read-only accessors ---

    @property
    def session_id(self) -> str:
        return self.assistant_msg.info.session_id

    @property
    def assistant_msg_id(self) -> str:
        return self.assistant_msg.info.id

    @property
    def response_text(self) -> str:
        return self._response_text

    @property
    def usage(self) -> RunUsage:
        return self._usage

    @property
    def cost_info(self) -> TokenCost | None:
        return self._cost_info

    @property
    def text_part(self) -> TextPart | None:
        return self._text_part

    # --- main entry point ---

    async def process_stream(
        self,
        stream: AsyncIterator[RichAgentStreamEvent[Any]],
    ) -> AsyncIterator[Event]:
        """Consume agent events and yield OpenCode SSE events.

        Wraps the entire stream in error handling; on exception yields
        a ``SessionErrorEvent`` so the UI shows the failure.
        """
        try:
            async for event in stream:
                for oc_event in self._handle_event(event):
                    yield oc_event
        except Exception as e:  # noqa: BLE001
            self._response_text = f"Error calling agent: {e}"
            yield SessionErrorEvent.from_exception(session_id=self.session_id, exception=e)

    def finalize(self) -> Iterator[Event]:
        """Yield final events after the stream has ended.

        Produces the final text part update (or creates one if text was never
        streamed), the step-finish part, and the final text timing update.
        """
        response_time = now_ms()
        # Final text part
        time_ = TimeStartEndOptional(start=self._stream_start_ms, end=response_time)
        if self._response_text and self._text_part is None:
            # Text was never streamed incrementally — create a text part now
            text_part = TextPart(
                id=identifier.ascending("part"),
                message_id=self.assistant_msg_id,
                session_id=self.session_id,
                text=self._response_text,
                time=time_,
            )
            text_part = self.assistant_msg.add_text_part(text=self._response_text, time=time_)
            yield PartUpdatedEvent.create(text_part)
        elif self._text_part is not None:
            # Update streamed text part with final timing
            final_text_part = TextPart(
                id=self._text_part.id,
                message_id=self.assistant_msg_id,
                session_id=self.session_id,
                text=self._response_text,
                time=time_,
            )
            self.assistant_msg.update_part(final_text_part)

        # Step finish
        step_finish = StepFinishPart(
            id=identifier.ascending("part"),
            message_id=self.assistant_msg_id,
            session_id=self.session_id,
            tokens=Tokens.from_pydantic_ai(self._usage),
            cost=float(self._cost_info.total_cost) if self._cost_info else 0.0,
        )
        self.assistant_msg.parts.append(step_finish)
        yield PartUpdatedEvent.create(step_finish)

    # --- private event dispatch ---

    def _handle_event(self, event: RichAgentStreamEvent[Any]) -> Iterator[Event]:
        """Dispatch a single agent event to the appropriate handler."""
        match event:
            case PartStartEvent(part=PydanticTextPart(content=delta)):
                yield from self._on_text_start(delta)

            case PartDeltaEvent(delta=TextPartDelta(content_delta=delta)) if delta:
                yield from self._on_text_delta(delta)

            case PartStartEvent(part=ThinkingPart(content=delta)):
                yield from self._on_thinking_start(delta)

            case PartDeltaEvent(delta=ThinkingPartDelta(content_delta=delta)):
                yield from self._on_thinking_delta(delta)

            case ToolCallStartEvent(
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                raw_input=raw_input,
                title=title,
            ):
                yield from self._on_tool_call_start(tool_name, tool_call_id, raw_input, title)

            case (
                FunctionToolCallEvent(part=tc_part)
                | PartStartEvent(part=PydanticToolCallPart() as tc_part)
            ) if tc_part.tool_call_id not in self._tool_parts:
                yield from self._on_pydantic_tool_call(tc_part)

            case ToolCallProgressEvent(
                tool_call_id=tool_call_id,
                title=title,
                items=items,
                tool_name=tool_name,
                tool_input=event_tool_input,
            ) if tool_call_id:
                yield from self._on_tool_progress(
                    tool_call_id, title, items, tool_name, event_tool_input
                )

            case ToolCallCompleteEvent(
                tool_call_id=tool_call_id,
                tool_result=result,
                metadata=event_metadata,
            ) if tool_call_id in self._tool_parts:
                yield from self._on_tool_complete(tool_call_id, result, event_metadata)

            case StreamCompleteEvent(message=msg) if msg:
                self._usage = msg.usage
                self._cost_info = msg.cost_info

            case SubAgentEvent(
                source_name=source_name,
                source_type=source_type,
                event=wrapped_event,
                depth=depth,
            ):
                yield from self._on_subagent(source_name, source_type, wrapped_event, depth)

            case CompactionEvent(session_id=compact_session_id, phase="completed"):
                yield SessionCompactedEvent.create(session_id=compact_session_id)

            case NoticeEvent(severity=severity, title=title, description=description):
                yield TuiToastShowEvent.create(
                    message=description or title,
                    variant=severity,
                    title=title if description else None,
                )

            case RunErrorEvent(message=error_message, agent_name=agent_name):
                error_prefix = f"[{agent_name}] " if agent_name else ""
                yield SessionErrorEvent.create(
                    session_id=self.session_id,
                    error_name="AgentError",
                    error_message=f"{error_prefix}{error_message}",
                )

    # --- text streaming ---

    def _on_text_start(self, delta: str) -> Iterator[Event]:
        self._response_text = delta
        self._text_part = TextPart(
            id=identifier.ascending("part"),
            message_id=self.assistant_msg_id,
            session_id=self.session_id,
            text=delta,
        )
        self.assistant_msg.parts.append(self._text_part)
        yield PartUpdatedEvent.create(self._text_part, delta=delta)

    def _on_text_delta(self, delta: str) -> Iterator[Event]:
        self._response_text += delta
        if self._text_part is not None:
            updated = TextPart(
                id=self._text_part.id,
                message_id=self.assistant_msg_id,
                session_id=self.session_id,
                text=self._response_text,
            )
            self.assistant_msg.update_part(updated)
            self._text_part = updated
            yield PartUpdatedEvent.create(updated, delta=delta)

    # --- thinking / reasoning ---

    def _on_thinking_start(self, delta: str) -> Iterator[Event]:
        """Handle initial thinking part - create ReasoningPart and emit update."""
        # Skip empty reasoning content
        if not delta or not delta.strip():
            return

        self._reasoning_part = ReasoningPart(
            id=identifier.ascending("part"),
            message_id=self.assistant_msg_id,
            session_id=self.session_id,
            text=delta,
            time=TimeStartEndOptional.now(),
        )
        self.assistant_msg.parts.append(self._reasoning_part)
        yield PartUpdatedEvent.create(self._reasoning_part)

    def _on_thinking_delta(self, delta: str | None) -> Iterator[Event]:
        """Handle incremental thinking updates - update existing ReasoningPart."""
        # Skip empty reasoning content
        if not delta or not delta.strip():
            return

        if self._reasoning_part is not None:
            updated = ReasoningPart(
                id=self._reasoning_part.id,
                message_id=self.assistant_msg_id,
                session_id=self.session_id,
                text=self._reasoning_part.text + delta,
                time=self._reasoning_part.time,
            )
            self.assistant_msg.update_part(updated)
            self._reasoning_part = updated
            yield PartUpdatedEvent.create(updated, delta=delta)

    # --- tool call start (rich events from toolsets / Claude Code) ---

    def _on_tool_call_start(
        self,
        tool_name: str,
        tool_call_id: str,
        raw_input: dict[str, Any] | None,
        title: str | None,
    ) -> Iterator[Event]:
        ui_input = _convert_params_for_ui(raw_input) if raw_input else {}

        if tool_call_id in self._tool_parts:
            # Update existing part with the custom title
            existing = self._tool_parts[tool_call_id]
            self._tool_inputs[tool_call_id] = ui_input or self._tool_inputs.get(tool_call_id, {})
            start = TimeStart(start=self._stream_start_ms)
            state = ToolStateRunning(time=start, input=self._tool_inputs[tool_call_id], title=title)
            updated = ToolPart(
                id=existing.id,
                message_id=existing.message_id,
                session_id=existing.session_id,
                tool=existing.tool,
                call_id=existing.call_id,
                state=state,
            )
            self._tool_parts[tool_call_id] = updated
            self.assistant_msg.update_part(updated)
            yield PartUpdatedEvent.create(updated)
        else:
            # Create new tool part
            self._tool_inputs[tool_call_id] = ui_input
            self._tool_outputs[tool_call_id] = ""
            tool_part = ToolPart(
                id=identifier.ascending("part"),
                message_id=self.assistant_msg_id,
                session_id=self.session_id,
                tool=tool_name,
                call_id=tool_call_id,
                state=ToolStateRunning(time=TimeStart.now(), input=ui_input, title=title),
            )
            self._tool_parts[tool_call_id] = tool_part
            self.assistant_msg.parts.append(tool_part)
            yield PartUpdatedEvent.create(tool_part)

    # --- pydantic-ai tool call events (fallback for pydantic-ai agents) ---

    def _on_pydantic_tool_call(self, tc_part: PydanticToolCallPart) -> Iterator[Event]:
        tool_call_id = tc_part.tool_call_id
        tool_name = tc_part.tool_name
        raw_input = safe_args_as_dict(tc_part)
        ui_input = _convert_params_for_ui(raw_input)
        self._tool_inputs[tool_call_id] = ui_input
        self._tool_outputs[tool_call_id] = ""
        rich_info = derive_rich_tool_info(tool_name, raw_input)
        tool_part = ToolPart(
            id=identifier.ascending("part"),
            message_id=self.assistant_msg_id,
            session_id=self.session_id,
            tool=tool_name,
            call_id=tool_call_id,
            state=ToolStateRunning(time=TimeStart.now(), input=ui_input, title=rich_info.title),
        )
        self._tool_parts[tool_call_id] = tool_part
        self.assistant_msg.parts.append(tool_part)
        yield PartUpdatedEvent.create(tool_part)

    # --- tool progress ---

    def _on_tool_progress(
        self,
        tool_call_id: str,
        title: str | None,
        items: Sequence[ToolCallContentItem],
        tool_name: str | None,
        event_tool_input: dict[str, Any] | None,
    ) -> Iterator[Event]:
        new_output = ""
        file_paths: list[str] = []
        for item in items:
            match item:
                case TextContentItem(text=text):
                    new_output += text
                case FileContentItem(content=content, path=path):
                    new_output += content
                    file_paths.append(path)
                case LocationContentItem(path=path):
                    file_paths.append(path)
                case TerminalContentItem() | DiffContentItem():
                    pass
                case _ as unreachable:
                    assert_never(unreachable)

        if file_paths:
            if self.on_file_paths is not None:
                self.on_file_paths(file_paths)
            # Emit file.edited for each file path (matches OpenCode's edit/write/patch tools)
            for fp in file_paths:
                yield FileEditedEvent.create(file=fp)

        if new_output:
            self._tool_outputs[tool_call_id] = self._tool_outputs.get(tool_call_id, "") + new_output

        if tool_call_id in self._tool_parts:
            existing = self._tool_parts[tool_call_id]
            existing_title = _extract_title_from_tool_state(existing.state)
            accumulated_output = self._tool_outputs.get(tool_call_id, "")
            tool_state = ToolStateRunning(
                time=TimeStart.now(),
                title=title or existing_title,
                input=self._tool_inputs.get(tool_call_id, {}),
                metadata={"output": accumulated_output} if accumulated_output else None,
            )
            updated = ToolPart(
                id=existing.id,
                message_id=existing.message_id,
                session_id=existing.session_id,
                tool=existing.tool,
                call_id=existing.call_id,
                state=tool_state,
            )
            self._tool_parts[tool_call_id] = updated
            self.assistant_msg.update_part(updated)
            yield PartUpdatedEvent.create(updated)
        else:
            # Create new tool part from progress event
            ui_input = _convert_params_for_ui(event_tool_input) if event_tool_input else {}
            self._tool_inputs[tool_call_id] = ui_input
            accumulated_output = self._tool_outputs.get(tool_call_id, "")
            tool_state = ToolStateRunning(
                time=TimeStart.now(),
                input=ui_input,
                title=title or tool_name or "Running...",
                metadata={"output": accumulated_output} if accumulated_output else None,
            )
            tool_part = ToolPart(
                id=identifier.ascending("part"),
                message_id=self.assistant_msg_id,
                session_id=self.session_id,
                tool=tool_name or "unknown",
                call_id=tool_call_id,
                state=tool_state,
            )
            self._tool_parts[tool_call_id] = tool_part
            self.assistant_msg.parts.append(tool_part)
            yield PartUpdatedEvent.create(tool_part)

    # --- tool complete ---

    def _on_tool_complete(
        self,
        tool_call_id: str,
        result: Any,
        event_metadata: dict[str, Any] | None,
    ) -> Iterator[Event]:
        existing = self._tool_parts[tool_call_id]
        tool_input = self._tool_inputs.get(tool_call_id, {})
        new_state: ToolStateCompleted | ToolStateError
        if isinstance(result, dict) and result.get("error"):
            t = TimeStartEnd(start=self._stream_start_ms, end=now_ms())
            error_string = str(result.get("error", "Unknown error"))
            new_state = ToolStateError(error=error_string, input=tool_input, time=t)
        else:
            new_state = ToolStateCompleted(
                title=f"Completed {existing.tool}",
                input=tool_input,
                output=str(result) if result else "",
                metadata=event_metadata or {},
                time=TimeStartEndCompacted(start=self._stream_start_ms, end=now_ms()),
            )

        updated = ToolPart(
            id=existing.id,
            message_id=existing.message_id,
            session_id=existing.session_id,
            tool=existing.tool,
            call_id=existing.call_id,
            state=new_state,
        )
        self._tool_parts[tool_call_id] = updated
        self.assistant_msg.update_part(updated)
        yield PartUpdatedEvent.create(updated)

    # --- sub-agent / team events ---

    def _on_subagent(
        self,
        source_name: str,
        source_type: SubAgentType,
        wrapped_event: RichAgentStreamEvent[Any],
        depth: int,
    ) -> Iterator[Event]:
        indent = "  " * (depth - 1)

        match wrapped_event:
            case StreamCompleteEvent(message=msg):
                match source_type:
                    case "team_parallel":
                        type_label = " (parallel)"
                        icon = "⚡"
                    case "team_sequential":
                        type_label = " (sequential)"
                        icon = "→"
                    case _:
                        type_label = ""
                        icon = "→"
                indicator_part = TextPart(
                    id=identifier.ascending("part"),
                    message_id=self.assistant_msg_id,
                    session_id=self.session_id,
                    text=f"{indent}{icon} {source_name}{type_label}",
                    time=TimeStartEndOptional.now(),
                )
                self.assistant_msg.parts.append(indicator_part)
                yield PartUpdatedEvent.create(indicator_part)
                content_part = TextPart(
                    id=identifier.ascending("part"),
                    message_id=self.assistant_msg_id,
                    session_id=self.session_id,
                    text=str(msg.content) if msg.content else "(no output)",
                    time=TimeStartEndOptional.now(),
                )
                self.assistant_msg.parts.append(content_part)
                yield PartUpdatedEvent.create(content_part)

            case ToolCallCompleteEvent(tool_name=tool_name, tool_result=result):
                result_str = str(result) if result else ""
                preview = result_str[:60] + "..." if len(result_str) > 60 else result_str  # noqa: PLR2004
                summary_part = TextPart(
                    id=identifier.ascending("part"),
                    message_id=self.assistant_msg_id,
                    session_id=self.session_id,
                    text=f"{indent}  ├─ {tool_name}: {preview}",
                    time=TimeStartEndOptional.now(),
                )
                self.assistant_msg.parts.append(summary_part)
                yield PartUpdatedEvent.create(summary_part)


def _extract_title_from_tool_state(state: ToolState) -> str:
    """Extract the title from a tool state."""
    match state:
        case ToolStateRunning(title=title):
            return title or ""
        case ToolStateCompleted(title=title):
            return title or ""
        case ToolStatePending() | ToolStateError():
            return ""
        case _ as unreachable:
            assert_never(unreachable)
