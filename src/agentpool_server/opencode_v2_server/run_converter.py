r"""Translation of AgentPool ``run_stream`` events into OpenCode V2 session events.

OpenCode V2 models one agent turn as an *execution* made of *steps*. Each step is one
model request and becomes its own assistant message. Within a step, text, reasoning and
tool calls are streamed as ``started`` / ``delta`` / ``ended`` triples, and tool calls then
move through ``called`` -> ``progress`` -> ``success`` / ``failed``.

Tool calls follow the canonical lifecycle of all agent types (``ToolCallStartEvent`` ->
``ToolCallProgressEvent``\* -> ``ToolCallCompleteEvent``); pydantic-ai part events only stream
the tool arguments. Steps come from ``StepStartedEvent`` / ``StepCompletedEvent`` when the
agent reports them, otherwise they are inferred: a new step starts when model output arrives
after tools of the current step were called.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import TYPE_CHECKING, Any, Literal, Protocol

from pydantic_ai.messages import (
    BaseToolCallPart,
    PartDeltaEvent,
    PartEndEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
    ToolCallPartDelta,
)

from agentpool.agents.events import (
    CommandCompleteEvent,
    CommandOutputEvent,
    CompactionEvent,
    DiffContentItem,
    FileContentItem,
    LocationContentItem,
    NoticeEvent,
    PlanUpdateEvent,
    RetryEvent,
    RunErrorEvent,
    StepCompletedEvent,
    StepStartedEvent,
    StreamCompleteEvent,
    SubAgentEvent,
    TerminalContentItem,
    TextContentItem,
    ToolCallCompleteEvent,
    ToolCallProgressEvent,
    ToolCallStartEvent,
)
from agentpool.log import get_logger
from agentpool.utils.time_utils import now_ms
from agentpool_server.opencode_v2_server.events import new_message_id
from agentpool_server.opencode_v2_server.tool_display import (
    ORIGINAL_TOOL_KEY,
    display_input,
    display_name,
)
from opencode_v2_sdk.models import (
    SessionCompactionEnded,
    SessionCompactionEndedData,
    SessionCompactionFailed,
    SessionCompactionFailedData,
    SessionCompactionStarted,
    SessionCompactionStartedData,
    SessionReasoningDelta,
    SessionReasoningDeltaData,
    SessionReasoningEnded,
    SessionReasoningEndedData,
    SessionReasoningStarted,
    SessionReasoningStartedData,
    SessionRetryScheduled,
    SessionRetryScheduledData,
    SessionStepEnded,
    SessionStepEndedData,
    SessionStepFailed,
    SessionStepFailedData,
    SessionStepFirstToken,
    SessionStepFirstTokenData,
    SessionStepStarted,
    SessionStepStartedData,
    SessionStepStreamed,
    SessionStepStreamedData,
    SessionStructuredError,
    SessionTextDelta,
    SessionTextDeltaData,
    SessionTextEnded,
    SessionTextEndedData,
    SessionTextStarted,
    SessionTextStartedData,
    SessionToolCalled,
    SessionToolCalledData,
    SessionToolFailed,
    SessionToolFailedData,
    SessionToolInputDelta,
    SessionToolInputDeltaData,
    SessionToolInputEnded,
    SessionToolInputEndedData,
    SessionToolInputStarted,
    SessionToolInputStartedData,
    SessionToolProgress,
    SessionToolProgressData,
    SessionToolSuccess,
    SessionToolSuccessData,
    SessionUsageUpdated,
    SessionUsageUpdatedData,
    TokenUsageInfo,
    TokenUsageInfoCache,
    ToolTextContent,
    TuiToastShow,
    TuiToastShowData,
)


if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from pydantic import BaseModel
    from pydantic_ai import RunUsage
    from pydantic_ai.usage import RequestUsage

    from agentpool.agents.events import (
        RichAgentStreamEvent,
        StreamWithCommandsEvent,
        ToolCallContentItem,
    )
    from agentpool.messaging import ChatMessage
    from opencode_v2_sdk.models import ModelRef


logger = get_logger(__name__)

COMMAND_PART_KEY = -2
"""Open-part key used for slash command output."""

type FinishReason = Literal["stop", "length", "tool-calls", "content-filter", "error", "unknown"]


class EventSink(Protocol):
    """Receives the V2 events produced by the converter."""

    def __call__(self, event_cls: type[BaseModel], data: BaseModel) -> None: ...


def usage_to_tokens(usage: RunUsage | RequestUsage | None) -> TokenUsageInfo:
    """Convert pydantic-ai usage into V2 token usage."""
    if usage is None:
        return zero_tokens()
    reasoning = usage.details.get("reasoning_tokens", 0) if usage.details else 0
    return TokenUsageInfo(
        input=usage.input_tokens,
        output=usage.output_tokens,
        reasoning=reasoning,
        cache=TokenUsageInfoCache(read=usage.cache_read_tokens, write=usage.cache_write_tokens),
    )


def zero_tokens() -> TokenUsageInfo:
    """Token usage with all counters at zero."""
    return TokenUsageInfo(
        input=0, output=0, reasoning=0, cache=TokenUsageInfoCache(read=0, write=0)
    )


def structured_error(message: str, *, error_type: str = "AgentError") -> SessionStructuredError:
    """Build a V2 structured error."""
    return SessionStructuredError(type=error_type, message=message)


def map_finish_reason(reason: str | None) -> FinishReason:
    """Map a pydantic-ai finish reason onto the V2 finish reasons."""
    match reason:
        case "stop" | None:
            return "stop"
        case "length":
            return "length"
        case "content_filter":
            return "content-filter"
        case "tool_call":
            return "tool-calls"
        case "error":
            return "error"
        case _:
            return "unknown"


def _content_item_text(item: ToolCallContentItem) -> tuple[str, str | None]:
    """Text contribution and affected path of a tool progress content item."""
    match item:
        case TextContentItem(text=text):
            return text, None
        case FileContentItem(content=content, path=path):
            return content, path
        case LocationContentItem(path=path):
            return "", path
        case DiffContentItem(path=path):
            return "", path
        case TerminalContentItem():
            return "", None


@dataclass
class _ToolState:
    call_id: str
    name: str
    message_id: str
    input_text: str = ""
    input_ended: bool = False
    called: bool = False
    finished: bool = False
    title: str | None = None
    output: str = ""
    files: list[str] = field(default_factory=list)


@dataclass
class _Step:
    message_id: str
    first_token: bool = False
    text_ordinal: int = 0
    reasoning_ordinal: int = 0
    tools_called: bool = False
    tokens: TokenUsageInfo | None = None
    """Usage reported by ``StepCompletedEvent`` (explicit steps only)."""
    cost: float | None = None
    finish: FinishReason | None = None
    awaiting_retry: bool = False
    """A retry was scheduled before this step produced output; the retried request
    continues the step instead of opening a new one."""


@dataclass
class _OpenPart:
    kind: Literal["text", "reasoning", "tool"]
    ordinal: int
    buffer: str = ""
    call_id: str | None = None


@dataclass
class RunEventConverter:
    """Stateful converter for one execution (agent turn) of a session."""

    session_id: str
    agent_id: str
    model_ref: ModelRef
    emit: EventSink
    clock: Callable[[], int] = now_ms
    """Time source for step start times (overridden when replaying history)."""
    step: _Step | None = None
    tools: dict[str, _ToolState] = field(default_factory=dict)
    open_parts: dict[int, _OpenPart] = field(default_factory=dict)
    """Currently streaming parts, keyed by the pydantic-ai part index."""
    explicit_steps: bool = False
    """Whether the agent reports steps itself (``StepStartedEvent`` / ``StepCompletedEvent``)."""
    reported_cost: float = 0.0
    """Sum of step costs reported by explicit step events."""
    final_message: ChatMessage[Any] | None = None
    errors: list[str] = field(default_factory=list)
    response_text: str = ""

    # --- steps ---------------------------------------------------------------------------

    def _ensure_step(self) -> _Step:
        # Without explicit step events, a step ends once its tools ran and new output arrives.
        inferred_boundary = (
            not self.explicit_steps
            and self.step is not None
            and self.step.tools_called
            and not self._has_running_tools()
        )
        if inferred_boundary:
            self._end_step("tool-calls")
        if self.step is None:
            self.step = _Step(message_id=new_message_id())
            self.emit(
                SessionStepStarted,
                SessionStepStartedData(
                    session_id=self.session_id,
                    assistant_message_id=self.step.message_id,
                    agent=self.agent_id,
                    model=self.model_ref,
                    started=self.clock(),
                ),
            )
        return self.step

    def _has_running_tools(self) -> bool:
        return any(t.called and not t.finished for t in self.tools.values())

    def _first_token(self, step: _Step) -> None:
        if not step.first_token:
            step.first_token = True
            self.emit(
                SessionStepFirstToken,
                SessionStepFirstTokenData(
                    session_id=self.session_id, assistant_message_id=step.message_id
                ),
            )

    def _end_step(
        self,
        finish: FinishReason,
        *,
        tokens: TokenUsageInfo | None = None,
        cost: float = 0.0,
    ) -> None:
        step = self.step
        if step is None:
            return
        self._close_all_parts()
        self.emit(
            SessionStepStreamed,
            SessionStepStreamedData(
                session_id=self.session_id, assistant_message_id=step.message_id
            ),
        )
        self.emit(
            SessionStepEnded,
            SessionStepEndedData(
                session_id=self.session_id,
                assistant_message_id=step.message_id,
                finish=finish,
                cost=cost,
                tokens=tokens or zero_tokens(),
            ),
        )
        self.step = None

    def _fail_step(self, message: str) -> None:
        step = self.step
        if step is None:
            return
        self._close_all_parts()
        self.emit(
            SessionStepFailed,
            SessionStepFailedData(
                session_id=self.session_id,
                assistant_message_id=step.message_id,
                error=structured_error(message),
            ),
        )
        self.step = None

    # --- text / reasoning ----------------------------------------------------------------

    def _start_text(self, index: int | None, initial: str) -> _OpenPart:
        step = self._ensure_step()
        part = _OpenPart(kind="text", ordinal=step.text_ordinal)
        step.text_ordinal += 1
        self.emit(
            SessionTextStarted,
            SessionTextStartedData(
                session_id=self.session_id,
                assistant_message_id=step.message_id,
                ordinal=part.ordinal,
            ),
        )
        self._register_part(index, part)
        if initial:
            self._text_delta(part, initial)
        return part

    def _text_delta(self, part: _OpenPart, delta: str) -> None:
        step = self._ensure_step()
        self._first_token(step)
        part.buffer += delta
        self.response_text += delta
        self.emit(
            SessionTextDelta,
            SessionTextDeltaData(
                session_id=self.session_id,
                assistant_message_id=step.message_id,
                ordinal=part.ordinal,
                delta=delta,
            ),
        )

    def _start_reasoning(self, index: int | None, initial: str) -> _OpenPart:
        step = self._ensure_step()
        part = _OpenPart(kind="reasoning", ordinal=step.reasoning_ordinal)
        step.reasoning_ordinal += 1
        self.emit(
            SessionReasoningStarted,
            SessionReasoningStartedData(
                session_id=self.session_id,
                assistant_message_id=step.message_id,
                ordinal=part.ordinal,
            ),
        )
        self._register_part(index, part)
        if initial:
            self._reasoning_delta(part, initial)
        return part

    def _reasoning_delta(self, part: _OpenPart, delta: str) -> None:
        step = self._ensure_step()
        self._first_token(step)
        part.buffer += delta
        self.emit(
            SessionReasoningDelta,
            SessionReasoningDeltaData(
                session_id=self.session_id,
                assistant_message_id=step.message_id,
                ordinal=part.ordinal,
                delta=delta,
            ),
        )

    def _register_part(self, index: int | None, part: _OpenPart) -> None:
        key = index if index is not None else -1
        if (previous := self.open_parts.get(key)) is not None:
            self._close_part(previous)
        self.open_parts[key] = part

    def _close_part(self, part: _OpenPart) -> None:
        step = self.step
        if step is None:
            return
        match part.kind:
            case "text":
                self.emit(
                    SessionTextEnded,
                    SessionTextEndedData(
                        session_id=self.session_id,
                        assistant_message_id=step.message_id,
                        ordinal=part.ordinal,
                        text=part.buffer,
                    ),
                )
            case "reasoning":
                self.emit(
                    SessionReasoningEnded,
                    SessionReasoningEndedData(
                        session_id=self.session_id,
                        assistant_message_id=step.message_id,
                        ordinal=part.ordinal,
                        text=part.buffer,
                    ),
                )
            case "tool":
                if part.call_id is not None and (tool := self.tools.get(part.call_id)):
                    self._end_tool_input(tool)
        for key, value in list(self.open_parts.items()):
            if value is part:
                del self.open_parts[key]

    def _close_all_parts(self) -> None:
        for part in list(self.open_parts.values()):
            self._close_part(part)
        self.open_parts.clear()

    def _close_streaming_text(self) -> None:
        """Close open text/reasoning parts (e.g. before a tool call starts)."""
        for part in list(self.open_parts.values()):
            if part.kind != "tool":
                self._close_part(part)

    # --- tools ---------------------------------------------------------------------------

    def _start_tool(self, call_id: str, name: str, index: int | None = None) -> _ToolState:
        if (existing := self.tools.get(call_id)) is not None:
            return existing
        self._close_streaming_text()
        step = self._ensure_step()
        tool = _ToolState(call_id=call_id, name=name, message_id=step.message_id)
        self.tools[call_id] = tool
        self.emit(
            SessionToolInputStarted,
            SessionToolInputStartedData(
                session_id=self.session_id,
                assistant_message_id=step.message_id,
                id=call_id,
                name=display_name(name),
            ),
        )
        if index is not None:
            self._register_part(index, _OpenPart(kind="tool", ordinal=0, call_id=call_id))
        return tool

    def _tool_input_delta(self, tool: _ToolState, delta: str) -> None:
        if not delta or tool.input_ended:
            return
        tool.input_text += delta
        self.emit(
            SessionToolInputDelta,
            SessionToolInputDeltaData(
                session_id=self.session_id,
                assistant_message_id=tool.message_id,
                id=tool.call_id,
                delta=delta,
            ),
        )

    def _end_tool_input(self, tool: _ToolState, args: dict[str, Any] | None = None) -> None:
        if tool.input_ended:
            return
        if not tool.input_text and args:
            tool.input_text = json.dumps(display_input(tool.name, args))
        tool.input_ended = True
        self.emit(
            SessionToolInputEnded,
            SessionToolInputEndedData(
                session_id=self.session_id,
                assistant_message_id=tool.message_id,
                id=tool.call_id,
                text=tool.input_text,
            ),
        )

    def _call_tool(self, tool: _ToolState, args: dict[str, Any]) -> None:
        if tool.called:
            return
        self._end_tool_input(tool, args)
        for key, value in list(self.open_parts.items()):
            if value.call_id == tool.call_id:
                del self.open_parts[key]
        tool.called = True
        if self.step is not None and self.step.message_id == tool.message_id:
            self.step.tools_called = True
        self.emit(
            SessionToolCalled,
            SessionToolCalledData(
                session_id=self.session_id,
                assistant_message_id=tool.message_id,
                id=tool.call_id,
                input=display_input(tool.name, args),
                executed=True,
            ),
        )

    def _tool_metadata(self, tool: _ToolState) -> dict[str, Any]:
        metadata: dict[str, Any] = {}
        if display_name(tool.name) != tool.name:
            metadata[ORIGINAL_TOOL_KEY] = tool.name
        if tool.title:
            metadata["title"] = tool.title
        if tool.output:
            metadata["output"] = tool.output
        if tool.files:
            metadata["files"] = list(tool.files)
        return metadata

    def _tool_progress(self, tool: _ToolState, extra: dict[str, Any] | None = None) -> None:
        metadata = self._tool_metadata(tool) | (extra or {})
        self.emit(
            SessionToolProgress,
            SessionToolProgressData(
                session_id=self.session_id,
                assistant_message_id=tool.message_id,
                id=tool.call_id,
                metadata=metadata,
            ),
        )

    def _finish_tool(self, tool: _ToolState, result: Any, error: str | None) -> None:
        if tool.finished:
            return
        if not tool.called:
            self._call_tool(tool, {})
        tool.finished = True
        metadata = self._tool_metadata(tool) or None
        if error is not None:
            self.emit(
                SessionToolFailed,
                SessionToolFailedData(
                    session_id=self.session_id,
                    assistant_message_id=tool.message_id,
                    id=tool.call_id,
                    error=structured_error(error, error_type="ToolError"),
                    metadata=metadata,
                    executed=True,
                ),
            )
            return
        text = result if isinstance(result, str) else _stringify(result)
        self.emit(
            SessionToolSuccess,
            SessionToolSuccessData(
                session_id=self.session_id,
                assistant_message_id=tool.message_id,
                id=tool.call_id,
                content=[ToolTextContent(text=text)],
                metadata=metadata,
                executed=True,
            ),
        )

    # --- main dispatch -------------------------------------------------------------------

    def handle(self, event: StreamWithCommandsEvent[Any]) -> None:
        """Translate one AgentPool stream event."""
        match event:
            case PartStartEvent() | PartDeltaEvent() | PartEndEvent():
                self._handle_part_event(event)
            case _:
                self._handle_rich_event(event)

    def _handle_part_event(self, event: PartStartEvent | PartDeltaEvent | PartEndEvent) -> None:
        """Translate pydantic-ai model response stream events."""
        match event:
            case PartStartEvent(index=index, part=TextPart(content=content)):
                self._start_text(index, content)
            case PartDeltaEvent(index=index, delta=TextPartDelta(content_delta=delta)):
                part = self.open_parts.get(index)
                if part is None or part.kind != "text":
                    part = self._start_text(index, "")
                if delta:
                    self._text_delta(part, delta)
            case PartStartEvent(index=index, part=ThinkingPart(content=content)):
                self._start_reasoning(index, content)
            case PartDeltaEvent(index=index, delta=ThinkingPartDelta(content_delta=delta)):
                part = self.open_parts.get(index)
                if part is None or part.kind != "reasoning":
                    part = self._start_reasoning(index, "")
                if delta:
                    self._reasoning_delta(part, delta)
            case PartStartEvent(index=index, part=BaseToolCallPart() as call_part):
                tool = self._start_tool(call_part.tool_call_id, call_part.tool_name, index)
                if isinstance(call_part.args, str):
                    self._tool_input_delta(tool, call_part.args)
                elif call_part.args:
                    self._tool_input_delta(tool, json.dumps(call_part.args))
            case PartDeltaEvent(index=index, delta=ToolCallPartDelta() as call_delta):
                part = self.open_parts.get(index)
                if part is not None and part.call_id is not None:
                    tool = self.tools[part.call_id]
                    match call_delta.args_delta:
                        case str() as text:
                            self._tool_input_delta(tool, text)
                        case dict() as args:
                            self._tool_input_delta(tool, json.dumps(args))
                        case None:
                            pass
            case PartEndEvent(index=index):
                if (part := self.open_parts.get(index)) is not None:
                    self._close_part(part)
            case _:
                pass

    def _handle_rich_event(self, event: StreamWithCommandsEvent[Any]) -> None:
        """Translate tool execution, lifecycle and AgentPool-specific events."""
        match event:
            case ToolCallStartEvent(
                tool_call_id=call_id, tool_name=name, title=title, raw_input=raw_input
            ):
                tool = self._start_tool(call_id, name)
                tool.title = title or tool.title
                self._call_tool(tool, raw_input)
                if title:
                    self._tool_progress(tool)
            case ToolCallProgressEvent(tool_call_id=call_id) if call_id:
                self._on_tool_progress(event)
            case ToolCallCompleteEvent(
                tool_call_id=call_id,
                tool_name=name,
                tool_input=tool_input,
                tool_result=result,
                error=error,
            ):
                tool = self.tools.get(call_id)
                if tool is None:
                    tool = self._start_tool(call_id, name)
                    self._call_tool(tool, tool_input)
                self._finish_tool(tool, result, error or _result_error(result))
            case StreamCompleteEvent(message=message):
                self.final_message = message
            case StepStartedEvent():
                self._on_step_started()
            case StepCompletedEvent():
                self._on_step_completed(event)
            case RetryEvent():
                self._on_retry(event)
            case RunErrorEvent(message=message, agent_name=agent_name):
                prefix = f"[{agent_name}] " if agent_name else ""
                self.errors.append(f"{prefix}{message}")
            case CompactionEvent(phase=phase, trigger=trigger, error=error):
                self._on_compaction(phase, trigger, error)
            case CommandOutputEvent() | CommandCompleteEvent():
                self._on_command_event(event)
            case NoticeEvent(title=title, description=description, severity=severity):
                self.emit(
                    TuiToastShow,
                    TuiToastShowData(
                        title=title if description else None,
                        message=description or title,
                        variant=severity,
                    ),
                )
            case PlanUpdateEvent(entries=entries, tool_call_id=call_id):
                todos = [
                    {"content": e.content, "status": e.status, "priority": e.priority}
                    for e in entries
                ]
                if call_id and (tool := self.tools.get(call_id)) is not None:
                    self._tool_progress(tool, {"todos": todos})
            case SubAgentEvent(
                source_name=source_name, event=inner, parent_tool_call_id=parent_call_id
            ):
                self._on_subagent(source_name, inner, parent_call_id)
            case _:
                pass

    def _on_step_started(self) -> None:
        """An explicit step begins: close the previous one with its reported usage."""
        self.explicit_steps = True
        if (previous := self.step) is not None and previous.awaiting_retry:
            previous.awaiting_retry = False
            return
        if previous is not None:
            self._end_step(
                previous.finish or "tool-calls",
                tokens=previous.tokens,
                cost=previous.cost or 0.0,
            )
        self._ensure_step()

    def _on_retry(self, event: RetryEvent) -> None:
        """Show the scheduled retry on the step that waits for it."""
        step = self._ensure_step()
        if not step.first_token and not step.tools_called:
            step.awaiting_retry = True
        error_type = f"HTTP{event.status_code}" if event.status_code else "RetryableError"
        self.emit(
            SessionRetryScheduled,
            SessionRetryScheduledData(
                session_id=self.session_id,
                assistant_message_id=step.message_id,
                attempt=event.attempt,
                at=int(self.clock() + event.delay_ms),
                error=structured_error(event.error, error_type=error_type),
            ),
        )

    def _on_step_completed(self, event: StepCompletedEvent) -> None:
        """Record the usage of the current step; it ends when the next one starts.

        Tool results still belong to the step that requested them, so the step stays open.
        """
        if (step := self.step) is None:
            return
        step.tokens = usage_to_tokens(event.usage)
        step.finish = map_finish_reason(event.finish_reason) if event.finish_reason else None
        if event.cost is not None:
            step.cost = float(event.cost)
            self.reported_cost += step.cost

    def _on_command_event(self, event: CommandOutputEvent | CommandCompleteEvent) -> None:
        """Render slash command output as text of the current step."""
        match event:
            case CommandOutputEvent(output=output) if output:
                part = self.open_parts.get(COMMAND_PART_KEY)
                if part is None or part.kind != "text":
                    part = self._start_text(COMMAND_PART_KEY, "")
                self._text_delta(part, output if output.endswith("\n") else output + "\n")
            case CommandOutputEvent():
                pass
            case CommandCompleteEvent(command=command, success=success):
                if (part := self.open_parts.get(COMMAND_PART_KEY)) is not None:
                    self._close_part(part)
                if not success:
                    self.errors.append(f"Command /{command} failed")

    def _on_tool_progress(self, event: ToolCallProgressEvent) -> None:
        tool = self.tools.get(event.tool_call_id)
        if tool is None:
            tool = self._start_tool(event.tool_call_id, event.tool_name or "unknown")
            self._call_tool(tool, event.tool_input or {})
        if event.title:
            tool.title = event.title
        text, paths = _collect_items(event.items)
        if event.replace_content:
            tool.output = text
        elif text:
            tool.output += text
        for path in paths:
            if path not in tool.files:
                tool.files.append(path)
        extra: dict[str, Any] = {}
        if event.message:
            extra["message"] = event.message
        if event.progress is not None:
            extra["progress"] = event.progress
            if event.total is not None:
                extra["total"] = event.total
        self._tool_progress(tool, extra)

    def _on_compaction(
        self, phase: str, trigger: Literal["auto", "manual"], error: str | None
    ) -> None:
        match phase:
            case "starting":
                self.emit(
                    SessionCompactionStarted,
                    SessionCompactionStartedData(session_id=self.session_id, reason=trigger),
                )
            case "completed":
                self.emit(
                    SessionCompactionEnded,
                    SessionCompactionEndedData(session_id=self.session_id, reason=trigger, text=""),
                )
            case "failed":
                self.emit(
                    SessionCompactionFailed,
                    SessionCompactionFailedData(
                        session_id=self.session_id,
                        reason=trigger,
                        error=structured_error(error or "Compaction failed"),
                    ),
                )

    def _on_subagent(
        self,
        source_name: str,
        inner: RichAgentStreamEvent[Any],
        parent_call_id: str | None,
    ) -> None:
        """Surface subagent activity as progress of the delegating tool call."""
        tool = self.tools.get(parent_call_id) if parent_call_id else None
        if tool is None:
            return
        match inner:
            case PartDeltaEvent(delta=TextPartDelta(content_delta=delta)) if delta:
                tool.output += delta
            case PartStartEvent(part=TextPart(content=content)) if content:
                tool.output += content
            case ToolCallStartEvent(tool_name=name, title=title):
                tool.output += f"\n[{source_name}] {title or name}\n"
            case _:
                return
        self._tool_progress(tool, {"subagent": source_name})

    # --- terminal states -----------------------------------------------------------------

    def finish(self) -> None:
        """Close the last step after the stream ended successfully."""
        for tool in self.tools.values():
            if not tool.finished:
                self._finish_tool(tool, None, "Tool call did not complete")
        message = self.final_message
        tokens = usage_to_tokens(message.usage) if message is not None else zero_tokens()
        cost = float(message.cost_info.total_cost) if message and message.cost_info else 0.0
        finish = map_finish_reason(message.finish_reason if message else None)
        if self.step is None and message is not None and not self.tools:
            # Agents that only deliver a final message (no streamed parts).
            text = (
                message.content if isinstance(message.content, str) else _stringify(message.content)
            )
            if text:
                part = self._start_text(None, text)
                self._close_part(part)
        if (step := self.step) is not None:
            if self.explicit_steps:
                # Steps carry their own usage; only cost the agent didn't report per step
                # (e.g. Claude Code) is attributed to the last step.
                remainder = max(cost - self.reported_cost, 0.0)
                self._end_step(
                    step.finish or finish,
                    tokens=step.tokens,
                    cost=(step.cost or 0.0) + remainder,
                )
            else:
                self._end_step(finish, tokens=tokens, cost=cost)
        self.emit(
            SessionUsageUpdated,
            SessionUsageUpdatedData(session_id=self.session_id, cost=cost, tokens=tokens),
        )

    def fail(self, message: str) -> None:
        """Mark the current step and pending tools as failed."""
        for tool in self.tools.values():
            if tool.called and not tool.finished:
                self._finish_tool(tool, None, message)
        if self.step is None:
            self._ensure_step()
        self._fail_step(message)

    def interrupt(self) -> None:
        """Close pending tools and the current step after an interruption."""
        for tool in self.tools.values():
            if not tool.finished:
                self._finish_tool(tool, None, "Interrupted")
        if self.step is not None:
            self._end_step("unknown")


def _collect_items(items: Sequence[ToolCallContentItem]) -> tuple[str, list[str]]:
    text = ""
    paths: list[str] = []
    for item in items:
        item_text, path = _content_item_text(item)
        text += item_text
        if path is not None:
            paths.append(path)
    return text, paths


def _result_error(result: Any) -> str | None:
    """Detect the error convention of tool results (``{"error": ...}``)."""
    if isinstance(result, dict) and (error := result.get("error")):
        return str(error)
    return None


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, default=str)
    except (TypeError, ValueError):
        return str(value)
