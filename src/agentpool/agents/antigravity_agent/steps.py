"""Convert Google Antigravity SDK Step streams into AgentPool stream events."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from agentpool.agents.events import (
    CompactionEvent,
    LocationContentItem,
    PartDeltaEvent,
    PartStartEvent,
    SubAgentEvent,
    ToolCallCompleteEvent,
    ToolCallProgressEvent,
    ToolCallStartEvent,
)


if TYPE_CHECKING:
    from collections.abc import Iterator

    from google.antigravity.types import Step, ToolCall, ToolResult

    from agentpool.agents.events import RichAgentStreamEvent
    from agentpool.tools.base import ToolKind


_TOOL_KINDS: dict[str, ToolKind] = {
    "view_file": "read",
    "list_directory": "read",
    "skill_lookup": "read",
    "search_directory": "search",
    "find_file": "search",
    "search_web": "search",
    "create_file": "edit",
    "edit_file": "edit",
    "run_command": "execute",
    "run_workflow": "execute",
    "read_url_content": "fetch",
    "invoke_subagent": "think",
    "generate_image": "other",
    "ask_question": "other",
    "schedule": "other",
    "finish": "other",
}

_PATH_ARG_KEYS = (
    "path",
    "file_path",
    "directory_path",
    "TargetFile",
    "output_path",
    "script_path",
    "ScriptPath",
)

_COMMAND_ARG_KEYS = ("CommandLine", "command_line", "command", "cmd")


def _tool_kind(name: str) -> ToolKind:
    return _TOOL_KINDS.get(name, "other")


def _tool_locations(call: ToolCall) -> list[LocationContentItem]:
    if call.canonical_path:
        return [LocationContentItem(path=call.canonical_path)]
    for key in _PATH_ARG_KEYS:
        val = call.args.get(key)
        if isinstance(val, str) and val:
            return [LocationContentItem(path=val)]
    return []


def _tool_title(call: ToolCall) -> str:
    name = str(call.name)
    if name == "run_command":
        for key in _COMMAND_ARG_KEYS:
            cmd = call.args.get(key)
            if isinstance(cmd, str) and cmd:
                return f"`{cmd}`"
    if call.canonical_path:
        return f"{name}: {call.canonical_path}"
    for key in _PATH_ARG_KEYS:
        val = call.args.get(key)
        if isinstance(val, str) and val:
            return f"{name}: {val}"
    if call.server_name:
        return f"{call.server_name}/{name}"
    return name.replace("_", " ").capitalize()


def _format_tool_result(result: ToolResult | None, step: Step | None = None) -> Any:
    if result is not None:
        if result.error is not None:
            return result.error
        if result.result is not None:
            if isinstance(result.result, (str, dict, list)):
                return result.result
            return str(result.result)
    if step is not None:
        if step.workflow_progress is not None and step.workflow_progress.output:
            return step.workflow_progress.output
        if step.error:
            return step.error
    return ""


@dataclass
class _OpenTool:
    """An announced tool call that has not yet emitted ToolCallCompleteEvent."""

    tool_call_id: str
    tool_name: str
    args: dict[str, Any]
    step_id: str | None = None
    last_progress: str = ""


@dataclass
class StepTracker:
    """Translate an Antigravity turn's ``Step`` updates into AgentPool stream events.

    Handles:
    - Incremental thinking and text deltas (``PartStartEvent`` / ``PartDeltaEvent``)
    - Tool call lifecycle (``ToolCallStartEvent`` -> ``ToolCallProgressEvent`` ->
      ``ToolCallCompleteEvent``), enriched with outputs captured by ``PostToolCallHook``
    - Context compaction (``CompactionEvent``)
    - Subagent trajectories (``step.depth > 0``) wrapped in ``SubAgentEvent``
    - Structured output extracted from ``StepType.FINISH``
    """

    agent_name: str
    message_id: str
    session_id: str
    tool_results: dict[str, ToolResult] = field(default_factory=dict)
    structured_output: Any | None = None
    _next_part: int = 0
    _text_parts: dict[str, int] = field(default_factory=dict)
    _thinking_parts: dict[str, int] = field(default_factory=dict)
    _open_tools: dict[str, _OpenTool] = field(default_factory=dict)
    _closed_tools: set[str] = field(default_factory=set)
    _compaction_phases: dict[str, str] = field(default_factory=dict)
    _subagent_trackers: dict[str, StepTracker] = field(default_factory=dict)

    def process_step(self, step: Step) -> Iterator[RichAgentStreamEvent[Any]]:
        """Process one Antigravity ``Step`` update and yield AgentPool events."""
        if step.depth > 0:
            yield from self._process_subagent_step(step)
            return

        from google.antigravity.types import StepSource, StepStatus, StepTarget, StepType

        if step.type == StepType.COMPACTION:
            yield from self._handle_compaction(step)
            return

        if step.type == StepType.FINISH:
            if step.structured_output is not None:
                self.structured_output = step.structured_output
            return

        if (
            step.source == StepSource.MODEL
            and step.target == StepTarget.USER
            and step.status != StepStatus.ERROR
        ):
            yield from self._handle_thinking(step)
            yield from self._handle_text(step)

        if step.tool_calls:
            yield from self._handle_tool_calls(step)

    def flush_open_tools(self) -> Iterator[RichAgentStreamEvent[Any]]:
        """Close any tool calls that started without receiving a terminal step update."""
        for tc_id, open_tool in list(self._open_tools.items()):
            result = self._pop_tool_result(tc_id, open_tool.step_id)
            error = result.error if result is not None else None
            self._open_tools.pop(tc_id, None)
            self._closed_tools.add(tc_id)
            yield ToolCallCompleteEvent(
                tool_name=open_tool.tool_name,
                tool_call_id=tc_id,
                tool_input=open_tool.args,
                tool_result=_format_tool_result(result),
                agent_name=self.agent_name,
                message_id=self.message_id,
                error=error,
            )

    def _process_subagent_step(self, step: Step) -> Iterator[RichAgentStreamEvent[Any]]:
        traj_id = step.trajectory_id or f"subagent-{step.depth}"
        child = self._subagent_trackers.get(traj_id)
        if child is None:
            child = StepTracker(
                agent_name=f"{self.agent_name}:subagent",
                message_id=self.message_id,
                session_id=self.session_id,
                tool_results=self.tool_results,
            )
            self._subagent_trackers[traj_id] = child
        # Process as depth-0 inside the child tracker, then wrap each emitted event.
        local_step = step.model_copy(update={"depth": 0})
        for event in child.process_step(local_step):
            yield SubAgentEvent(
                source_name=child.agent_name,
                source_type="agent",
                event=event,
                depth=step.depth,
                parent_tool_call_id=step.parent_trajectory_id or None,
            )

    def _handle_thinking(self, step: Step) -> Iterator[RichAgentStreamEvent[Any]]:
        from google.antigravity.types import StepStatus

        step_key = step.id or str(step.step_index)
        if step.thinking_delta:
            if (part_idx := self._thinking_parts.get(step_key)) is None:
                part_idx = self._next_part
                self._next_part += 1
                self._thinking_parts[step_key] = part_idx
                yield PartStartEvent.thinking(index=part_idx, content=step.thinking_delta)
            else:
                yield PartDeltaEvent.thinking(index=part_idx, content=step.thinking_delta)
            return

        if (
            step.status == StepStatus.DONE
            and step.thinking
            and step_key not in self._thinking_parts
        ):
            part_idx = self._next_part
            self._next_part += 1
            self._thinking_parts[step_key] = part_idx
            yield PartStartEvent.thinking(index=part_idx, content=step.thinking)

    def _handle_text(self, step: Step) -> Iterator[RichAgentStreamEvent[Any]]:
        from google.antigravity.types import StepStatus

        step_key = step.id or str(step.step_index)
        if step.content_delta:
            if (part_idx := self._text_parts.get(step_key)) is None:
                part_idx = self._next_part
                self._next_part += 1
                self._text_parts[step_key] = part_idx
                yield PartStartEvent.text(index=part_idx, content=step.content_delta)
            else:
                yield PartDeltaEvent.text(index=part_idx, content=step.content_delta)
            return

        if step.status == StepStatus.DONE and step.content and step_key not in self._text_parts:
            part_idx = self._next_part
            self._next_part += 1
            self._text_parts[step_key] = part_idx
            yield PartStartEvent.text(index=part_idx, content=step.content)

    def _handle_compaction(self, step: Step) -> Iterator[RichAgentStreamEvent[Any]]:
        from google.antigravity.types import StepStatus

        step_key = step.id or str(step.step_index)
        match step.status:
            case StepStatus.ACTIVE:
                phase = "starting"
            case StepStatus.DONE:
                phase = "completed"
            case StepStatus.ERROR:
                phase = "failed"
            case _:
                return
        if self._compaction_phases.get(step_key) == phase:
            return
        self._compaction_phases[step_key] = phase
        yield CompactionEvent(
            session_id=self.session_id,
            trigger="auto",
            phase=phase,
            error=step.error or None if phase == "failed" else None,
        )

    def _handle_tool_calls(self, step: Step) -> Iterator[RichAgentStreamEvent[Any]]:
        from google.antigravity.types import StepStatus

        terminal = step.status in (StepStatus.DONE, StepStatus.ERROR, StepStatus.CANCELED)
        for call in step.tool_calls:
            tc_id = call.id or call.step_id or step.id or f"step-{step.step_index}"
            tool_name = f"{call.server_name}/{call.name}" if call.server_name else str(call.name)

            if tc_id not in self._open_tools and tc_id not in self._closed_tools:
                self._open_tools[tc_id] = _OpenTool(
                    tool_call_id=tc_id,
                    tool_name=tool_name,
                    args=dict(call.args),
                    step_id=call.step_id or step.id or None,
                )
                yield ToolCallStartEvent(
                    tool_call_id=tc_id,
                    tool_name=tool_name,
                    title=_tool_title(call),
                    kind=_tool_kind(str(call.name)),
                    locations=_tool_locations(call),
                    raw_input=dict(call.args),
                )

            if terminal and tc_id not in self._closed_tools:
                open_tool = self._open_tools.pop(tc_id, None)
                self._closed_tools.add(tc_id)
                result = self._pop_tool_result(
                    tc_id, call.step_id or step.id or (open_tool.step_id if open_tool else None)
                )
                error = self._resolve_tool_error(step, result)
                yield ToolCallCompleteEvent(
                    tool_name=tool_name,
                    tool_call_id=tc_id,
                    tool_input=dict(call.args),
                    tool_result=_format_tool_result(result, step),
                    agent_name=self.agent_name,
                    message_id=self.message_id,
                    error=error,
                )
            elif (
                not terminal
                and tc_id in self._open_tools
                and step.workflow_progress is not None
                and step.workflow_progress.output
                and step.workflow_progress.output != self._open_tools[tc_id].last_progress
            ):
                self._open_tools[tc_id].last_progress = step.workflow_progress.output
                yield ToolCallProgressEvent(
                    tool_call_id=tc_id,
                    tool_name=tool_name,
                    title=step.workflow_progress.description or _tool_title(call),
                    message=step.workflow_progress.output,
                    tool_input=dict(call.args),
                )

    def _pop_tool_result(self, tc_id: str, step_id: str | None) -> ToolResult | None:
        if (result := self.tool_results.pop(tc_id, None)) is not None:
            if step_id and step_id != tc_id:
                self.tool_results.pop(step_id, None)
            return result
        if step_id and (result := self.tool_results.pop(step_id, None)) is not None:
            return result
        return None

    @staticmethod
    def _resolve_tool_error(step: Step, result: ToolResult | None) -> str | None:
        from google.antigravity.types import StepStatus

        if result is not None and result.error:
            return result.error
        if step.status == StepStatus.ERROR:
            return step.error or "Tool execution failed"
        if step.status == StepStatus.CANCELED:
            return step.error or "Tool execution cancelled"
        return None
