"""Translate OpenCode V2 SSE events into AgentPool stream events."""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from pydantic_ai import RunUsage

from agentpool.agents.events import (
    CompactionEvent,
    DiffContentItem,
    LocationContentItem,
    NoticeEvent,
    PartDeltaEvent,
    PartStartEvent,
    PlanUpdateEvent,
    RetryEvent,
    RunErrorEvent,
    StepCompletedEvent,
    StepStartedEvent,
    ToolCallCompleteEvent,
    ToolCallProgressEvent,
    ToolCallStartEvent,
)
from agentpool.agents.opencode_v2_agent.converters import (
    format_model_ref,
    format_tool_content,
    to_finish_reason,
    to_request_usage,
    to_run_usage,
)
from agentpool.utils.time_utils import now_ms
from agentpool.utils.todos import PlanEntry, TodoPriority, TodoStatus
from opencode_v2_sdk.models import (
    SessionCompactionEnded,
    SessionCompactionFailed,
    SessionCompactionStarted,
    SessionExecutionFailed,
    SessionExecutionInterrupted,
    SessionExecutionStarted,
    SessionExecutionSucceeded,
    SessionReasoningDelta,
    SessionReasoningEnded,
    SessionReasoningStarted,
    SessionRetryScheduled,
    SessionStepEnded,
    SessionStepFailed,
    SessionStepStarted,
    SessionTextDelta,
    SessionTextEnded,
    SessionTextStarted,
    SessionToolCalled,
    SessionToolFailed,
    SessionToolInputDelta,
    SessionToolInputEnded,
    SessionToolInputStarted,
    SessionToolProgress,
    SessionToolSuccess,
    TuiToastShow,
)


if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from pydantic_ai import FinishReason

    from agentpool.agents.events import RichAgentStreamEvent
    from agentpool.tools.base import ToolKind


_TOOL_KINDS: dict[str, ToolKind] = {
    "read": "read",
    "view": "read",
    "list": "read",
    "ls": "read",
    "edit": "edit",
    "write": "edit",
    "patch": "edit",
    "apply_patch": "edit",
    "multiedit": "edit",
    "delete": "delete",
    "grep": "search",
    "glob": "search",
    "find": "search",
    "websearch": "search",
    "web_search": "search",
    "webfetch": "fetch",
    "web_fetch": "fetch",
    "bash": "execute",
    "shell": "execute",
    "run_command": "execute",
    "task": "think",
    "subagent": "think",
    "todowrite": "other",
    "todoread": "read",
}

_PATH_KEYS = ("filePath", "file_path", "path", "directory", "target_file")
_COMMAND_KEYS = ("command", "cmd", "CommandLine")


def _tool_kind(name: str) -> ToolKind:
    lower = name.lower()
    if lower in _TOOL_KINDS:
        return _TOOL_KINDS[lower]
    return "other"


def _tool_locations(tool_input: dict[str, Any]) -> list[LocationContentItem]:
    for key in _PATH_KEYS:
        val = tool_input.get(key)
        if isinstance(val, str) and val:
            return [LocationContentItem(path=val)]
    return []


def tool_metadata_events(
    call_id: str, name: str, tool_input: dict[str, Any], metadata: Mapping[str, Any] | None
) -> list[ToolCallProgressEvent | PlanUpdateEvent]:
    """What OpenCode's metadata of a finished tool shows beyond its output.

    Changed files (edit, multiedit, apply_patch; write of a new file) as diffs: the turn
    records them as file changes. Todos (todowrite, todoread) as the plan. Diagnostics are in
    the output already (OpenCode adds them for the model).
    """
    match metadata:
        case {"todos": list(todos)}:
            entries = [entry for todo in todos if (entry := _plan_entry(todo)) is not None]
            return [PlanUpdateEvent(entries=entries, tool_call_id=call_id)]
        case {"filediff": dict(filediff)}:  # edit
            diffs = _file_diffs([filediff])
        case {"results": list(results)}:  # multiedit: per file, first before to last after
            diffs = _file_diffs([r["filediff"] for r in results if isinstance(r, dict)])
        case {"files": list(files)}:  # apply_patch
            diffs = [diff for f in files if (diff := _patch_diff(f)) is not None]
        case {"filepath": str(path), "exists": False}:  # write of a new file
            content = tool_input.get("content")
            diffs = (
                [DiffContentItem(path=path, new_text=content)] if isinstance(content, str) else []
            )
        case _:
            diffs = []
    if not diffs:
        return []
    progress = ToolCallProgressEvent(
        tool_call_id=call_id, tool_name=name, items=diffs, replace_content=True
    )
    return [progress]


def _file_diffs(filediffs: list[Any]) -> list[DiffContentItem]:
    """One diff per file: its content before the first change and after the last."""
    before: dict[str, str | None] = {}
    after: dict[str, str] = {}
    for filediff in filediffs:
        match filediff:
            case {"file": str(path), "before": str(old), "after": str(new)}:
                added = filediff.get("status") == "added"
                before.setdefault(path, None if added else old)
                after[path] = new
    return [
        DiffContentItem(path=path, old_text=old, new_text=after[path])
        for path, old in before.items()
    ]


def _patch_diff(file: Any) -> DiffContentItem | None:
    match file:
        case {"filePath": str(path), "type": str(kind), "before": str(old), "after": str(new)}:
            target = file.get("movePath") if kind == "move" else None
            return DiffContentItem(
                path=target if isinstance(target, str) else path,
                old_text=None if kind == "add" else old,
                new_text=new,
            )
        case _:
            return None


def _plan_entry(todo: Any) -> PlanEntry | None:
    match todo:
        case {"content": str(content), "status": str(status), "priority": str(priority)}:
            return PlanEntry(
                content=content, status=_todo_status(status), priority=_priority(priority)
            )
        case _:
            return None


def _todo_status(status: str) -> TodoStatus:
    match status:
        case "pending" | "in_progress" | "completed" | "cancelled":
            return status
        case _:
            return "pending"


def _priority(priority: str) -> TodoPriority:
    match priority:
        case "high" | "medium" | "low":
            return priority
        case _:
            return "medium"


def _tool_title(
    name: str, tool_input: dict[str, Any], metadata: dict[str, Any] | None = None
) -> str:
    if metadata and isinstance(title := metadata.get("title"), str) and title:
        return title
    if isinstance(desc := tool_input.get("description"), str) and desc:
        return desc
    if name.lower() in ("bash", "shell", "run_command"):
        for key in _COMMAND_KEYS:
            cmd = tool_input.get(key)
            if isinstance(cmd, str) and cmd:
                return f"`{cmd}`"
    for key in _PATH_KEYS:
        val = tool_input.get(key)
        if isinstance(val, str) and val:
            return f"{name}: {val}"
    return name


@dataclass
class _ToolTrackerState:
    call_id: str
    name: str
    tool_input: dict[str, Any] = field(default_factory=dict)
    started: bool = False
    completed: bool = False


@dataclass
class V2EventTracker:
    """Convert one OpenCode V2 session execution's SSE events into AgentPool events."""

    session_id: str
    agent_name: str
    message_id: str
    done: bool = False
    failed: bool = False
    interrupted: bool = False
    finish_reason: FinishReason = "stop"
    total_usage: RunUsage = field(default_factory=RunUsage)
    total_cost: Decimal = Decimal(0)
    last_model_name: str | None = None
    _next_part: int = 0
    _text_parts: dict[tuple[str, int], int] = field(default_factory=dict)
    _reasoning_parts: dict[tuple[str, int], int] = field(default_factory=dict)
    _tools: dict[str, _ToolTrackerState] = field(default_factory=dict)

    def handle(self, event: Any) -> Iterator[RichAgentStreamEvent[Any]]:
        """Process one unwrapped ``V2Event.root`` and yield AgentPool stream events."""
        match event:
            case SessionExecutionStarted(data=data) if data.session_id == self.session_id:
                pass
            case SessionExecutionSucceeded(data=data) if data.session_id == self.session_id:
                self.done = True
            case SessionExecutionInterrupted(data=data) if data.session_id == self.session_id:
                self.done = True
                self.interrupted = True
            case SessionExecutionFailed(data=data) if data.session_id == self.session_id:
                self.done = True
                self.failed = True
                self.finish_reason = "error"
                yield RunErrorEvent(
                    message=data.error.message,
                    code=data.error.type,
                    agent_name=self.agent_name,
                )
            case SessionStepStarted() | SessionStepEnded() | SessionStepFailed():
                if event.data.session_id == self.session_id:
                    yield from self._handle_step(event)
            case SessionTextStarted() | SessionTextDelta() | SessionTextEnded():
                if event.data.session_id == self.session_id:
                    yield from self._handle_text(event)
            case SessionReasoningStarted() | SessionReasoningDelta() | SessionReasoningEnded():
                if event.data.session_id == self.session_id:
                    yield from self._handle_reasoning(event)
            case (
                SessionToolInputStarted()
                | SessionToolInputDelta()
                | SessionToolInputEnded()
                | SessionToolCalled()
                | SessionToolProgress()
                | SessionToolSuccess()
                | SessionToolFailed()
            ):
                if event.data.session_id == self.session_id:
                    yield from self._handle_tool(event)
            case SessionCompactionStarted() | SessionCompactionEnded() | SessionCompactionFailed():
                if event.data.session_id == self.session_id:
                    yield from self._handle_compaction(event)
            case SessionRetryScheduled(data=data) if data.session_id == self.session_id:
                delay_ms = max(0.0, float(data.at - now_ms()))
                yield RetryEvent(
                    attempt=data.attempt,
                    delay_ms=delay_ms,
                    error=data.error.message,
                )
            case TuiToastShow(data=data):
                severity = (
                    "error"
                    if data.variant == "error"
                    else ("warning" if data.variant == "warning" else "info")
                )
                yield NoticeEvent(
                    title=data.title or data.message,
                    description=data.message if data.title else None,
                    severity=severity,
                )
            case _:
                pass

    def _handle_step(
        self,
        event: SessionStepStarted | SessionStepEnded | SessionStepFailed,
    ) -> Iterator[RichAgentStreamEvent[Any]]:
        match event:
            case SessionStepStarted(data=data):
                self.last_model_name = format_model_ref(data.model)
                yield StepStartedEvent(
                    step_id=data.assistant_message_id,
                    model_name=self.last_model_name,
                )
            case SessionStepEnded(data=data):
                step_usage = to_request_usage(data.tokens)
                self._accumulate_usage(to_run_usage(data.tokens), data.cost)
                step_finish = to_finish_reason(data.finish)
                self.finish_reason = step_finish
                cost_dec = Decimal(str(data.cost)) if data.cost else None
                yield StepCompletedEvent(
                    step_id=data.assistant_message_id,
                    usage=step_usage,
                    finish_reason=step_finish,
                    cost=cost_dec,
                    model_name=self.last_model_name,
                )
            case SessionStepFailed(data=data):
                step_usage = to_request_usage(data.tokens)
                self._accumulate_usage(to_run_usage(data.tokens), data.cost or 0.0)
                self.finish_reason = "error"
                cost_dec = Decimal(str(data.cost)) if data.cost else None
                yield StepCompletedEvent(
                    step_id=data.assistant_message_id,
                    usage=step_usage,
                    finish_reason="error",
                    cost=cost_dec,
                    model_name=self.last_model_name,
                )

    def _handle_compaction(
        self,
        event: SessionCompactionStarted | SessionCompactionEnded | SessionCompactionFailed,
    ) -> Iterator[RichAgentStreamEvent[Any]]:
        match event:
            case SessionCompactionStarted(data=data):
                yield CompactionEvent(
                    session_id=self.session_id,
                    trigger=data.reason,
                    phase="starting",
                )
            case SessionCompactionEnded(data=data):
                if data.tokens is not None or data.cost:
                    self._accumulate_usage(to_run_usage(data.tokens), data.cost or 0.0)
                yield CompactionEvent(
                    session_id=self.session_id,
                    trigger=data.reason,
                    phase="completed",
                )
            case SessionCompactionFailed(data=data):
                yield CompactionEvent(
                    session_id=self.session_id,
                    trigger=data.reason,
                    phase="failed",
                    error=data.error.message,
                )

    def flush_open_tools(self) -> Iterator[RichAgentStreamEvent[Any]]:
        """Close any tool calls started during the turn that never reached completion."""
        for state in list(self._tools.values()):
            if state.started and not state.completed:
                state.completed = True
                yield ToolCallCompleteEvent(
                    tool_name=state.name,
                    tool_call_id=state.call_id,
                    tool_input=state.tool_input,
                    tool_result="",
                    agent_name=self.agent_name,
                    message_id=self.message_id,
                    error="Interrupted" if self.interrupted else None,
                )

    def _accumulate_usage(self, usage: RunUsage, cost: float) -> None:
        self.total_usage.input_tokens += usage.input_tokens
        self.total_usage.output_tokens += usage.output_tokens
        self.total_usage.cache_read_tokens += usage.cache_read_tokens
        self.total_usage.cache_write_tokens += usage.cache_write_tokens
        if usage.details:
            for key, val in usage.details.items():
                self.total_usage.details[key] = self.total_usage.details.get(key, 0) + val
        if cost:
            self.total_cost += Decimal(str(cost))

    def _handle_text(
        self,
        event: SessionTextStarted | SessionTextDelta | SessionTextEnded,
    ) -> Iterator[RichAgentStreamEvent[Any]]:
        key = (event.data.assistant_message_id, event.data.ordinal)
        match event:
            case SessionTextStarted():
                return
            case SessionTextDelta(data=data) if data.delta:
                if (idx := self._text_parts.get(key)) is None:
                    idx = self._next_part
                    self._next_part += 1
                    self._text_parts[key] = idx
                    yield PartStartEvent.text(index=idx, content=data.delta)
                else:
                    yield PartDeltaEvent.text(index=idx, content=data.delta)
            case SessionTextEnded(data=data) if data.text and key not in self._text_parts:
                idx = self._next_part
                self._next_part += 1
                self._text_parts[key] = idx
                yield PartStartEvent.text(index=idx, content=data.text)
            case _:
                return

    def _handle_reasoning(
        self,
        event: SessionReasoningStarted | SessionReasoningDelta | SessionReasoningEnded,
    ) -> Iterator[RichAgentStreamEvent[Any]]:
        key = (event.data.assistant_message_id, event.data.ordinal)
        match event:
            case SessionReasoningStarted():
                return
            case SessionReasoningDelta(data=data) if data.delta:
                if (idx := self._reasoning_parts.get(key)) is None:
                    idx = self._next_part
                    self._next_part += 1
                    self._reasoning_parts[key] = idx
                    yield PartStartEvent.thinking(index=idx, content=data.delta)
                else:
                    yield PartDeltaEvent.thinking(index=idx, content=data.delta)
            case SessionReasoningEnded(data=data) if data.text and key not in self._reasoning_parts:
                idx = self._next_part
                self._next_part += 1
                self._reasoning_parts[key] = idx
                yield PartStartEvent.thinking(index=idx, content=data.text)
            case _:
                return

    def _handle_tool(
        self,
        event: (
            SessionToolInputStarted
            | SessionToolInputDelta
            | SessionToolInputEnded
            | SessionToolCalled
            | SessionToolProgress
            | SessionToolSuccess
            | SessionToolFailed
        ),
    ) -> Iterator[RichAgentStreamEvent[Any]]:
        match event:
            case SessionToolInputStarted(data=data):
                self._tools.setdefault(data.id, _ToolTrackerState(call_id=data.id, name=data.name))
            case SessionToolInputDelta() | SessionToolInputEnded():
                return
            case SessionToolCalled(data=data):
                state = self._tools.setdefault(
                    data.id, _ToolTrackerState(call_id=data.id, name="tool")
                )
                state.tool_input = dict(data.input)
                state.started = True
                yield ToolCallStartEvent(
                    tool_call_id=data.id,
                    tool_name=state.name,
                    title=_tool_title(state.name, state.tool_input),
                    kind=_tool_kind(state.name),
                    locations=_tool_locations(state.tool_input),
                    raw_input=state.tool_input,
                )
            case SessionToolProgress(data=data):
                state = self._tools.setdefault(
                    data.id, _ToolTrackerState(call_id=data.id, name="tool")
                )
                title = _tool_title(state.name, state.tool_input, data.metadata)
                output = data.metadata.get("output")
                yield ToolCallProgressEvent(
                    tool_call_id=data.id,
                    tool_name=state.name,
                    title=title,
                    message=output if isinstance(output, str) else None,
                    tool_input=state.tool_input,
                )
            case SessionToolSuccess(data=data):
                state = self._tools.setdefault(
                    data.id, _ToolTrackerState(call_id=data.id, name="tool")
                )
                if not state.started:
                    state.started = True
                    yield ToolCallStartEvent(
                        tool_call_id=data.id,
                        tool_name=state.name,
                        title=_tool_title(state.name, state.tool_input, data.metadata),
                        kind=_tool_kind(state.name),
                        locations=_tool_locations(state.tool_input),
                        raw_input=state.tool_input,
                    )
                if not state.completed:
                    state.completed = True
                    yield from tool_metadata_events(
                        data.id, state.name, state.tool_input, data.metadata
                    )
                    yield ToolCallCompleteEvent(
                        tool_name=state.name,
                        tool_call_id=data.id,
                        tool_input=state.tool_input,
                        tool_result=format_tool_content(data.content),
                        agent_name=self.agent_name,
                        message_id=self.message_id,
                    )
            case SessionToolFailed(data=data):
                state = self._tools.setdefault(
                    data.id, _ToolTrackerState(call_id=data.id, name="tool")
                )
                if not state.started:
                    state.started = True
                    yield ToolCallStartEvent(
                        tool_call_id=data.id,
                        tool_name=state.name,
                        title=_tool_title(state.name, state.tool_input, data.metadata),
                        kind=_tool_kind(state.name),
                        locations=_tool_locations(state.tool_input),
                        raw_input=state.tool_input,
                    )
                if not state.completed:
                    state.completed = True
                    output = format_tool_content(data.content) or data.error.message
                    yield ToolCallCompleteEvent(
                        tool_name=state.name,
                        tool_call_id=data.id,
                        tool_input=state.tool_input,
                        tool_result=output,
                        agent_name=self.agent_name,
                        message_id=self.message_id,
                        error=data.error.message,
                    )
