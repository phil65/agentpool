"""Event stream events.

Unified event system using ToolCallProgressEvent for all tool-related progress updates.
Rich content (terminals, diffs, locations) is conveyed through the items field.

## UI Content vs Agent Return Values (ACP Protocol)

Tools can control what the UI displays separately from what the agent receives:

- Emit `tool_call_progress` with content items for UI display
- Return raw/structured data for the agent
- Session layer populates `content` (for UI) and `raw_output` (for agent) separately

Use `replace_content=True` for streaming/final content that should replace previous state.
If no content is emitted, the return value is automatically converted for UI (fallback).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Annotated, Any, Literal

import pydantic
from pydantic_ai import (
    FinalResultEvent,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent as PyAIPartDeltaEvent,
    PartEndEvent,
    PartStartEvent as PyAIPartStartEvent,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
    ToolCallPart,
    ToolCallPartDelta,
)
from pydantic_ai.messages import (
    BuiltinToolCallEvent,  # ty:ignore[deprecated]
    BuiltinToolResultEvent,  # ty:ignore[deprecated]
)


if TYPE_CHECKING:
    from collections.abc import Sequence

    from agentpool.messaging import ChatMessage
    from agentpool.tools.base import ToolKind
    from agentpool.utils.todos import PlanEntry


SubAgentType = Literal["agent", "team_parallel", "team_sequential"]
# Lifecycle events (aligned with AG-UI protocol)
CompactionTrigger = Literal["auto", "manual"]
CompactionPhase = Literal["starting", "completed"]
ToolCallStatus = Literal["pending", "in_progress", "completed", "failed"]


class PartStartEvent(PyAIPartStartEvent):
    """Part start event."""

    @classmethod
    def thinking(cls, index: int, content: str) -> PartStartEvent:
        return cls(index=index, part=ThinkingPart(content=content))

    @classmethod
    def text(cls, index: int, content: str) -> PartStartEvent:
        return cls(index=index, part=TextPart(content=content))

    @classmethod
    def tool_call(
        cls, index: int, tool_name: str, args: str | dict[str, Any], tool_call_id: str
    ) -> PartStartEvent:
        part = ToolCallPart(tool_name=tool_name, args=args, tool_call_id=tool_call_id)
        return cls(index=index, part=part)


class PartDeltaEvent(PyAIPartDeltaEvent):
    """Part start event."""

    @classmethod
    def thinking(cls, index: int, content: str) -> PartDeltaEvent:
        return cls(index=index, delta=ThinkingPartDelta(content_delta=content))

    @classmethod
    def text(cls, index: int, content: str) -> PartDeltaEvent:
        return cls(index=index, delta=TextPartDelta(content_delta=content))

    @classmethod
    def tool_call(cls, index: int, content: str, tool_call_id: str) -> PartDeltaEvent:
        delta = ToolCallPartDelta(args_delta=content, tool_call_id=tool_call_id)
        return cls(index=index, delta=delta)


@dataclass(kw_only=True)
class RunStartedEvent:
    """Signals the start of an agent run."""

    session_id: str
    """ID of the session."""
    run_id: str
    """ID of the agent run (unique per request/response cycle)."""
    agent_name: str | None = None
    """Name of the agent starting the run."""
    event_kind: Literal["run_started"] = "run_started"
    """Event type identifier."""


@dataclass(kw_only=True)
class RunErrorEvent:
    """Signals an error during an agent run."""

    message: str
    """Error message."""
    code: str | None = None
    """Error code."""
    run_id: str | None = None
    """ID of the agent run that failed."""
    agent_name: str | None = None
    """Name of the agent that errored."""
    event_kind: Literal["run_error"] = "run_error"
    """Event type identifier."""


# Unified tool call content models (dataclass versions of ACP schema models)


@dataclass(kw_only=True)
class TerminalContentItem:
    """Embed a terminal for live output display."""

    type: Literal["terminal"] = "terminal"
    """Content type identifier."""
    terminal_id: str
    """The ID of the terminal being embedded."""


@dataclass(kw_only=True)
class DiffContentItem:
    """File modification shown as a diff."""

    type: Literal["diff"] = "diff"
    """Content type identifier."""
    path: str
    """The file path being modified."""
    old_text: str | None = None
    """The original content (None for new files)."""
    new_text: str
    """The new content after modification."""


@dataclass(kw_only=True)
class LocationContentItem:
    """A file location being accessed or modified.

    Note: line defaults to 0 (not None) to ensure ACP clients render clickable paths.
    The ACP spec allows None, but some clients (e.g., Claude Code) require the field present.
    """

    type: Literal["location"] = "location"
    """Content type identifier."""
    path: str
    """The file path being accessed or modified."""
    line: int = 0
    """Line number within the file (0 = beginning/unspecified)."""


@dataclass(kw_only=True)
class TextContentItem:
    """Simple text content."""

    type: Literal["text"] = "text"
    """Content type identifier."""
    text: str
    """The text content."""


@dataclass(kw_only=True)
class FileContentItem:
    """File content with metadata for rich display.

    Carries structured data about file content. Formatting (e.g., Zed-style
    code blocks) happens at the ACP layer based on client capabilities.
    """

    type: Literal["file"] = "file"
    """Content type identifier."""
    content: str
    """The file content."""
    path: str
    """The file path."""
    language: str | None = None
    """Language for syntax highlighting (inferred from path if not provided)."""
    start_line: int | None = None
    """Starting line number (1-based) if showing a range."""
    end_line: int | None = None
    """Ending line number (1-based) if showing a range."""


# Union type for all tool call content items
ToolCallContentItem = (
    TerminalContentItem | DiffContentItem | LocationContentItem | TextContentItem | FileContentItem
)


@dataclass(kw_only=True)
class StreamCompleteEvent[TContent]:
    """Event indicating streaming is complete with final message."""

    message: ChatMessage[TContent]
    """The final chat message with all metadata."""
    event_kind: Literal["stream_complete"] = "stream_complete"
    """Event type identifier."""


@dataclass(kw_only=True)
class ToolCallStartEvent:
    """Event indicating a tool call has started with rich ACP metadata."""

    tool_call_id: str
    """The ID of the tool call."""
    tool_name: str
    """The name of the tool being called."""
    title: str
    """Human-readable title describing what the tool is doing."""
    kind: ToolKind = "other"
    """Tool kind (read, edit, delete, move, search, execute, think, fetch, other)."""
    content: list[ToolCallContentItem] = field(default_factory=list)
    """Content produced by the tool call."""
    locations: list[LocationContentItem] = field(default_factory=list)
    """File locations affected by this tool call."""
    raw_input: dict[str, Any] = field(default_factory=dict)
    """The raw input parameters sent to the tool."""
    field_meta: dict[str, Any] | None = None
    """Protocol-level metadata passed through to ACP ``_meta``.

    Used for undocumented conventions like display-only terminal lifecycle
    signals (``terminal_info``, ``terminal_output``, ``terminal_exit``).
    See :mod:`acp.schema.field_meta` for known conventions.
    """

    event_kind: Literal["tool_call_start"] = "tool_call_start"
    """Event type identifier."""


@dataclass(kw_only=True)
class ToolCallProgressEvent:
    """Unified tool call progress event with rich content support.

    This event carries a title and rich content items (terminals, diffs, locations, text)
    that map directly to ACP tool call notifications.

    Use the classmethod constructors for common patterns:
    - process_started() - Process start with terminal
    - process_output() - Process output update
    - process_exit() - Process completion
    - process_killed() - Process termination
    - process_released() - Process cleanup
    - file_operation() - File read/write/delete
    - file_edit() - File edit with diff
    """

    tool_call_id: str
    """The ID of the tool call."""
    status: ToolCallStatus = "in_progress"
    """Current execution status."""
    title: str | None = None
    """Human-readable title describing the operation."""

    # Rich content items
    items: Sequence[ToolCallContentItem] = field(default_factory=list)
    """Rich content items (terminals, diffs, locations, text)."""
    replace_content: bool = False
    """If True, items replace existing content instead of appending."""

    # Legacy fields for backwards compatibility
    progress: int | None = None
    """The current progress of the tool call."""
    total: int | None = None
    """The total progress of the tool call."""
    message: str | None = None
    """Progress message."""
    tool_name: str | None = None
    """The name of the tool being called."""
    tool_input: dict[str, Any] | None = None
    """The input provided to the tool."""
    field_meta: dict[str, Any] | None = None
    """Protocol-level metadata passed through to ACP ``_meta``.

    Used for undocumented conventions like display-only terminal lifecycle
    signals (``terminal_info``, ``terminal_output``, ``terminal_exit``).
    See :mod:`acp.schema.field_meta` for known conventions.
    """

    event_kind: Literal["tool_call_progress"] = "tool_call_progress"
    """Event type identifier."""

    @classmethod
    def process_started(
        cls,
        *,
        tool_call_id: str,
        process_id: str,
        command: str,
        success: bool = True,
        error: str | None = None,
        tool_name: str | None = None,
    ) -> ToolCallProgressEvent:
        """Create event for process start.

        Args:
            tool_call_id: Tool call identifier
            process_id: Process/terminal identifier
            command: Command being executed
            success: Whether process started successfully
            error: Error message if failed
            tool_name: Optional tool name
        """
        status: Literal["in_progress", "failed"] = "in_progress" if success else "failed"
        title = f"Running: {command}" if success else f"Failed to start: {command}"
        if error:
            title = f"{title} - {error}"

        items: list[ToolCallContentItem] = [TerminalContentItem(terminal_id=process_id)]
        if error:
            items.append(TextContentItem(text=f"Error: {error}"))

        return cls(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            status=status,
            title=title,
            items=items,
        )

    @classmethod
    def process_output(
        cls,
        *,
        tool_call_id: str,
        process_id: str,
        output: str,
        tool_name: str | None = None,
    ) -> ToolCallProgressEvent:
        """Create event for process output.

        Args:
            tool_call_id: Tool call identifier
            process_id: Process/terminal identifier
            output: Process output
            tool_name: Optional tool name
        """
        items = [TerminalContentItem(terminal_id=process_id)]
        title = f"Output: {output[:50]}..." if len(output) > 50 else output  # noqa: PLR2004

        return cls(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            status="in_progress",
            title=title,
            items=items,
        )

    @classmethod
    def process_exit(
        cls,
        *,
        tool_call_id: str,
        process_id: str,
        exit_code: int,
        final_output: str | None = None,
        tool_name: str | None = None,
    ) -> ToolCallProgressEvent:
        """Create event for process exit.

        Args:
            tool_call_id: Tool call identifier
            process_id: Process/terminal identifier
            exit_code: Process exit code
            final_output: Final process output
            tool_name: Optional tool name
        """
        success = exit_code == 0
        status_icon = "✓" if success else "✗"
        title = f"Process exited [{status_icon} exit {exit_code}]"

        items: list[ToolCallContentItem] = [TerminalContentItem(terminal_id=process_id)]
        if final_output:
            items.append(TextContentItem(text=final_output))

        return cls(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            status="in_progress",
            title=title,
            items=items,
        )

    @classmethod
    def process_killed(
        cls,
        *,
        tool_call_id: str,
        process_id: str,
        success: bool = True,
        error: str | None = None,
        tool_name: str | None = None,
    ) -> ToolCallProgressEvent:
        """Create event for process kill.

        Args:
            tool_call_id: Tool call identifier
            process_id: Process/terminal identifier
            success: Whether kill succeeded
            error: Error message if failed
            tool_name: Optional tool name
        """
        title = (
            f"Killed process {process_id}" if success else f"Failed to kill process {process_id}"
        )
        if error:
            title = f"{title} - {error}"

        status: Literal["in_progress", "failed"] = "in_progress" if success else "failed"
        items = [TerminalContentItem(terminal_id=process_id)]

        return cls(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            status=status,
            title=title,
            items=items,
        )

    @classmethod
    def process_released(
        cls,
        *,
        tool_call_id: str,
        process_id: str,
        success: bool = True,
        error: str | None = None,
        tool_name: str | None = None,
    ) -> ToolCallProgressEvent:
        """Create event for process resource release.

        Args:
            tool_call_id: Tool call identifier
            process_id: Process/terminal identifier
            success: Whether release succeeded
            error: Error message if failed
            tool_name: Optional tool name
        """
        title = (
            f"Released process {process_id}"
            if success
            else f"Failed to release process {process_id}"
        )
        if error:
            title = f"{title} - {error}"

        status: Literal["in_progress", "failed"] = "in_progress" if success else "failed"
        items = [TerminalContentItem(terminal_id=process_id)]

        return cls(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            status=status,
            title=title,
            items=items,
        )

    @classmethod
    def file_operation(
        cls,
        *,
        tool_call_id: str,
        operation: Literal["read", "write", "delete", "list", "edit"],
        path: str,
        success: bool,
        error: str | None = None,
        tool_name: str | None = None,
        line: int = 0,
    ) -> ToolCallProgressEvent:
        """Create event for file operation.

        Args:
            tool_call_id: Tool call identifier
            operation: File operation type
            path: File path
            success: Whether operation succeeded
            error: Error message if failed
            tool_name: Optional tool name
            line: Line number for navigation (0 = beginning)
        """
        status: Literal["completed", "failed"] = "completed" if success else "failed"
        title = f"{operation.capitalize()}: {path}"
        if error:
            title = f"{title} - {error}"

        items: list[ToolCallContentItem] = [LocationContentItem(path=path, line=line)]
        if error:
            items.append(TextContentItem(text=f"Error: {error}"))

        return cls(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            status=status,
            title=title,
            items=items,
        )

    @classmethod
    def file_edit(
        cls,
        *,
        tool_call_id: str,
        path: str,
        old_text: str,
        new_text: str,
        status: ToolCallStatus,
        tool_name: str | None = None,
    ) -> ToolCallProgressEvent:
        """Create event for file edit with diff.

        Args:
            tool_call_id: Tool call identifier
            path: File path being edited
            old_text: Original file content
            new_text: New file content
            status: Edit status
            tool_name: Optional tool name
        """
        items: list[ToolCallContentItem] = [
            DiffContentItem(path=path, old_text=old_text, new_text=new_text),
        ]

        return cls(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            status=status,
            title=f"Editing: {path}",
            items=items,
            replace_content=True,  # Streaming diffs should replace, not accumulate
        )


@dataclass(kw_only=True)
class CommandOutputEvent:
    """Event for slash command output."""

    command: str
    """The command name that was executed."""
    output: str
    """The output text from the command."""
    event_kind: Literal["command_output"] = "command_output"
    """Event type identifier."""


@dataclass(kw_only=True)
class CommandCompleteEvent:
    """Event indicating slash command execution is complete."""

    command: str
    """The command name that was completed."""
    success: bool
    """Whether the command executed successfully."""
    event_kind: Literal["command_complete"] = "command_complete"
    """Event type identifier."""


@dataclass(kw_only=True)
class ToolCallCompleteEvent:
    """Event indicating tool call is complete with both input and output."""

    tool_name: str
    """The name of the tool that was called."""
    tool_call_id: str
    """The ID of the tool call."""
    tool_input: dict[str, Any]
    """The input provided to the tool."""
    tool_result: Any
    """The result returned by the tool."""
    agent_name: str
    """The name of the agent that made the tool call."""
    message_id: str
    """The message ID associated with this tool call."""
    metadata: dict[str, Any] | None = None
    """Optional metadata for UI/client use (diffs, diagnostics, etc.)."""
    event_kind: Literal["tool_call_complete"] = "tool_call_complete"
    """Event type identifier."""


@dataclass(kw_only=True)
class CustomEvent[T]:
    """Generic custom event that can be emitted during tool execution."""

    event_data: T
    """The custom event data of any type."""
    event_type: str = "custom"
    """Type identifier for the custom event."""
    source: str | None = None
    """Optional source identifier (tool name, etc.)."""
    event_kind: Literal["custom"] = "custom"
    """Event type identifier."""


@dataclass(kw_only=True)
class PlanUpdateEvent:
    """Event indicating plan state has changed."""

    entries: list[PlanEntry]
    """Current plan entries."""
    tool_call_id: str | None = None
    """Tool call ID for ACP notifications."""
    event_kind: Literal["plan_update"] = "plan_update"
    """Event type identifier."""


@dataclass(kw_only=True)
class SubAgentEvent:
    """Event wrapping activity from a subagent or team member.

    Used to propagate events from delegated agents/teams into the parent stream,
    allowing the consumer (UI/server) to decide how to render nested activity.
    """

    source_name: str
    """Name of the agent or team that produced this event."""
    source_type: SubAgentType
    """Type of source: agent, parallel team, or sequential team."""
    event: RichAgentStreamEvent[Any]
    """The actual event from the subagent/team."""
    depth: int = 1
    """Nesting depth (1 = direct child, 2 = grandchild, etc.)."""
    parent_tool_call_id: str | None = None
    """Tool call ID of the parent task tool that spawned this subagent."""
    event_kind: Literal["subagent"] = "subagent"
    """Event type identifier."""


@dataclass(kw_only=True)
class CompactionEvent:
    """Event indicating context compaction is starting or completed.

    This is a semantic event that consumers (ACP, OpenCode) handle differently:
    - ACP: Converts to a text message for display
    - OpenCode: Emits session.compacted SSE event
    """

    session_id: str
    """The session ID being compacted."""
    trigger: CompactionTrigger = "auto"
    """What triggered the compaction (auto = context overflow, manual = slash command)."""
    phase: CompactionPhase = "starting"
    """Current phase of compaction."""
    pre_tokens: int | None = None
    """Token count before compaction (available on completed phase from Claude Code)."""
    event_kind: Literal["compaction"] = "compaction"
    """Event type identifier."""

    def format(self) -> str:
        token_info = f" ({self.pre_tokens:,} tokens)" if self.pre_tokens is not None else ""
        if self.trigger == "auto":
            return (
                f"\n\n---\n\n📦 **Context compaction** triggered{token_info}."
                " Summarizing...\n\n---\n\n"
            )
        return (
            f"\n\n---\n\n📦 **Manual compaction** requested{token_info}. Summarizing...\n\n---\n\n"
        )


HandleResponseEvent = Annotated[
    FunctionToolCallEvent
    | FunctionToolResultEvent
    | BuiltinToolCallEvent  # pyright: ignore[reportDeprecated]  # ty:ignore[deprecated]
    | BuiltinToolResultEvent,  # pyright: ignore[reportDeprecated]  # ty:ignore[deprecated]
    pydantic.Discriminator("event_kind"),
]
"""An event yielded when handling a model response, indicating tool calls and results."""

ModelResponseStreamEvent = Annotated[
    PyAIPartStartEvent | PyAIPartDeltaEvent | PartEndEvent | FinalResultEvent,
    pydantic.Discriminator("event_kind"),
]
AgentStreamEvent = Annotated[
    ModelResponseStreamEvent | HandleResponseEvent, pydantic.Discriminator("event_kind")
]
"""An event in the agent stream: model response stream events and response-handling events."""


type RichAgentStreamEvent[OutputDataT] = (
    AgentStreamEvent
    | StreamCompleteEvent[OutputDataT]
    | RunStartedEvent
    | RunErrorEvent
    | ToolCallStartEvent
    | ToolCallProgressEvent
    | ToolCallCompleteEvent
    | PlanUpdateEvent
    | CompactionEvent
    | SubAgentEvent
    | CustomEvent[Any]
)


type StreamWithCommandsEvent[OutputDataT] = (
    RichAgentStreamEvent[OutputDataT] | CommandOutputEvent | CommandCompleteEvent
)
