"""Event emitter delegate for AgentContext with fluent API."""

from typing import TYPE_CHECKING, Any, Literal

from agentpool.agents.events import (
    CustomEvent,
    LocationContentItem,
    PlanUpdateEvent,
    TextContentItem,
    ToolCallProgressEvent,
    ToolCallStartEvent,
)


if TYPE_CHECKING:
    from collections.abc import Sequence

    from agentpool.agents.context import AgentContext
    from agentpool.agents.events import RichAgentStreamEvent, ToolCallContentItem
    from agentpool.agents.events.events import ToolCallStatus
    from agentpool.tools.base import ToolKind
    from agentpool.utils.todos import PlanEntry


class StreamEventEmitter:
    """Event emitter delegate that automatically injects context.

    Provides a fluent API for emitting progress events with context
    (tool_call_id, etc.) automatically injected.

    UI content vs agent return values: Emit `tool_call_progress` with content
    items for UI display, return raw data for the agent. See `events.py` for
    the full pattern (aligned with ACP protocol).
    """

    def __init__(self, context: AgentContext) -> None:
        """Initialize event emitter with agent context.

        Args:
            context: Agent context to extract metadata from
        """
        self._context = context

    # =========================================================================
    # Core methods - the essential API
    # =========================================================================

    async def tool_call_start(
        self,
        title: str,
        kind: ToolKind = "other",
        content: list[ToolCallContentItem] | None = None,
        locations: list[str | LocationContentItem] | None = None,
    ) -> None:
        """Emit tool call start event with rich ACP metadata.

        Args:
            title: Human-readable title describing what the tool is doing
            kind: Tool kind (read, edit, delete, move, search, execute, think, fetch, other)
            content: Content produced by the tool call (terminals, diffs, text)
            locations: File paths or LocationContentItem objects affected by this tool call
        """
        location_items = [
            LocationContentItem(path=loc) if isinstance(loc, str) else loc
            for loc in (locations or [])
        ]
        event = ToolCallStartEvent(
            tool_call_id=self._context.tool_call_id or "",
            tool_name=self._context.tool_name or "",
            title=title,
            kind=kind,
            content=content or [],
            locations=location_items,
            raw_input=self._context.tool_input.copy(),
        )
        await self._emit(event)

    async def tool_call_progress(
        self,
        title: str,
        *,
        status: ToolCallStatus = "in_progress",
        items: Sequence[ToolCallContentItem | str] | None = None,
        replace_content: bool = False,
    ) -> None:
        """Emit a progress event.

        Args:
            title: Human-readable title describing the operation
            status: Execution status
            items: Rich content items (terminals, diffs, locations, text)
            replace_content: If True, items replace existing content instead of appending
        """
        # Diffs among the items are recorded as file changes by the turn (SessionRuntime)
        event = ToolCallProgressEvent(
            tool_call_id=self._context.tool_call_id or "",
            tool_name=self._context.tool_name,
            status=status,
            title=title,
            items=[TextContentItem(text=i) if isinstance(i, str) else i for i in items or []],
            replace_content=replace_content,
        )
        await self._emit(event)

    async def progress(self, progress: float, total: float | None, message: str) -> None:
        """Emit numeric progress event (for MCP progress notifications).

        Args:
            progress: Current progress value
            total: Total progress value
            message: Progress message
        """
        await self._context.report_progress(progress, total, message)

    async def emit_event(self, event: RichAgentStreamEvent[Any]) -> None:
        """Emit a typed event into the agent's event stream.

        This is the escape hatch for emitting any event type directly.

        Args:
            event: The event instance (PlanUpdateEvent, ToolCallProgressEvent, etc.)
        """
        await self._emit(event)

    # =========================================================================
    # Convenience methods - thin wrappers for common patterns
    # =========================================================================

    async def file_operation(
        self,
        operation: Literal["read", "write", "delete", "list", "edit"],
        path: str,
        success: bool,
        error: str | None = None,
        line: int = 0,
    ) -> None:
        """Emit file operation event.

        Args:
            operation: The filesystem operation performed
            path: The file/directory path that was operated on
            success: Whether the operation completed successfully
            error: Error message if operation failed
            line: Line number for navigation (0 = beginning)
        """
        event = ToolCallProgressEvent.file_operation(
            tool_call_id=self._context.tool_call_id or "",
            tool_name=self._context.tool_name,
            operation=operation,
            path=path,
            success=success,
            error=error,
            line=line,
        )
        await self._emit(event)

    async def file_edit_progress(
        self,
        path: str,
        old_text: str,
        new_text: str,
        status: ToolCallStatus,
    ) -> None:
        """Emit file edit progress event with diff information.

        Args:
            path: The file path being edited
            old_text: Original file content
            new_text: New file content
            status: Current status of the edit operation
        """
        # Recorded as a file change by the turn (SessionRuntime), like any diff
        event = ToolCallProgressEvent.file_edit(
            tool_call_id=self._context.tool_call_id or "",
            tool_name=self._context.tool_name,
            path=path,
            old_text=old_text,
            new_text=new_text,
            status=status,
        )
        await self._emit(event)

    async def process_started(
        self,
        process_id: str,
        command: str,
        success: bool = True,
        error: str | None = None,
    ) -> None:
        """Emit process start event.

        Args:
            process_id: Unique process identifier (terminal_id for ACP)
            command: Command being executed
            success: Whether the process started successfully
            error: Error message if start failed
        """
        event = ToolCallProgressEvent.process_started(
            tool_call_id=self._context.tool_call_id or "",
            tool_name=self._context.tool_name,
            process_id=process_id,
            command=command,
            success=success,
            error=error,
        )
        await self._emit(event)

    async def process_output(self, process_id: str, output: str, *, snapshot: bool = False) -> None:
        """Emit process output event.

        Args:
            process_id: Process identifier
            output: Process output: new output, or all of it so far with ``snapshot``
            snapshot: Whether ``output`` is all output so far
        """
        event = ToolCallProgressEvent.process_output(
            tool_call_id=self._context.tool_call_id or "",
            tool_name=self._context.tool_name,
            process_id=process_id,
            output=output,
            snapshot=snapshot,
        )
        await self._emit(event)

    async def process_exit(
        self,
        process_id: str,
        exit_code: int,
        final_output: str | None = None,
    ) -> None:
        """Emit process exit event.

        Args:
            process_id: Process identifier
            exit_code: Process exit code
            final_output: Final process output
        """
        event = ToolCallProgressEvent.process_exit(
            tool_call_id=self._context.tool_call_id or "",
            tool_name=self._context.tool_name,
            process_id=process_id,
            exit_code=exit_code,
            final_output=final_output,
        )
        await self._emit(event)

    async def process_killed(
        self,
        process_id: str,
        success: bool = True,
        error: str | None = None,
    ) -> None:
        """Emit process kill event.

        Args:
            process_id: Process identifier
            success: Whether the process was successfully killed
            error: Error message if kill failed
        """
        event = ToolCallProgressEvent.process_killed(
            tool_call_id=self._context.tool_call_id or "",
            tool_name=self._context.tool_name,
            process_id=process_id,
            success=success,
            error=error,
        )
        await self._emit(event)

    async def process_released(
        self,
        process_id: str,
        success: bool = True,
        error: str | None = None,
    ) -> None:
        """Emit process release event.

        Args:
            process_id: Process identifier
            success: Whether resources were successfully released
            error: Error message if release failed
        """
        event = ToolCallProgressEvent.process_released(
            tool_call_id=self._context.tool_call_id or "",
            tool_name=self._context.tool_name,
            process_id=process_id,
            success=success,
            error=error,
        )
        await self._emit(event)

    async def plan_updated(self, entries: Sequence[PlanEntry]) -> None:
        """Emit plan update event.

        Args:
            entries: Current plan entries
        """
        event = PlanUpdateEvent(
            entries=list(entries),
            tool_call_id=self._context.tool_call_id,
        )
        await self._emit(event)

    async def custom(
        self,
        event_data: Any,
        event_type: str = "custom",
        source: str | None = None,
    ) -> None:
        """Emit custom event.

        Args:
            event_data: The custom event data of any type
            event_type: Type identifier for the custom event
            source: Optional source identifier
        """
        event = CustomEvent(
            event_data=event_data,
            event_type=event_type,
            source=source or self._context.tool_name,
        )
        await self._emit(event)

    # =========================================================================
    # Private helpers
    # =========================================================================

    async def _emit(self, event: RichAgentStreamEvent[Any]) -> None:
        """Internal method to emit events into the stream of the context's turn."""
        await self._context.emit_event(event)
