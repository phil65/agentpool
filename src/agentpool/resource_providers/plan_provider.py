"""Plan provider for agent planning and task management."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from agentpool.agents.context import AgentContext  # noqa: TC001
from agentpool.agents.events import TextContentItem
from agentpool.resource_providers import ResourceProvider
from agentpool.tools.base import ToolResult
from agentpool.utils.todos import PRIORITY_LABELS, STATUS_ICONS


if TYPE_CHECKING:
    from collections.abc import Sequence

    from agentpool.tools.base import Tool
    from agentpool.utils.todos import PlanEntry, TodoPriority, TodoStatus, TodoTracker


PlanToolMode = Literal["granular", "declarative"]


@dataclass(kw_only=True)
class PlanUpdateEvent:
    """Event indicating plan state has changed."""

    entries: list[PlanEntry]
    """Current plan entries."""
    event_kind: Literal["plan_update"] = "plan_update"
    """Event type identifier."""


class PlanProvider(ResourceProvider):
    """Provides plan-related tools for agent planning and task management.

    This provider creates tools for managing agent plans and tasks,
    delegating storage to pool.todos and emitting domain events
    that can be handled by protocol adapters.
    """

    kind = "tools"

    def __init__(self, mode: PlanToolMode = "declarative") -> None:
        """Initialize plan provider.

        Args:
            mode: Tool mode - 'granular' for separate tools, 'declarative' for
                  single set_plan tool.
        """
        super().__init__(name="plan")
        self.mode = mode

    def _get_tracker(self, agent_ctx: AgentContext) -> TodoTracker | None:
        """Get the TodoTracker from the pool."""
        if agent_ctx.pool is not None:
            return agent_ctx.pool.todos
        return None

    async def get_tools(self) -> Sequence[Tool]:
        """Get plan management tools based on mode."""
        tools: list[Tool] = [self.create_tool(self.get_plan, category="read")]

        if self.mode == "declarative":
            # Single bulk tool for capable models
            tools.append(self.create_tool(self.set_plan, category="other"))
        else:
            # granular mode (default) - separate tools for simpler models
            tools.extend([
                self.create_tool(self.add_plan_entry, category="other"),
                self.create_tool(self.update_plan_entry, category="edit"),
                self.create_tool(self.remove_plan_entry, category="delete"),
            ])

        return tools

    async def get_plan(self, agent_ctx: AgentContext) -> ToolResult:
        """Get the current plan formatted as markdown.

        Args:
            agent_ctx: Agent execution context

        Returns:
            Markdown-formatted plan with all entries and their status
        """
        tracker = self._get_tracker(agent_ctx)
        if tracker is None or not tracker.entries:
            # Emit progress for empty plan
            item = TextContentItem(text="*No tasks in plan yet.*")
            await agent_ctx.events.tool_call_progress(title="Fetched plan (empty)", items=[item])
            return ToolResult(content="## Plan\n\n*No plan entries yet.")

        lines = ["## Plan", ""]

        for i, entry in enumerate(tracker.entries):
            icon = STATUS_ICONS.get(entry.status, "?")
            priority = PRIORITY_LABELS.get(entry.priority, "")
            lines.append(f"{i}. {icon} {priority} {entry.content} *({entry.status})*")
        # Count completed entries for summary
        completed = sum(1 for e in tracker.entries if e.status == "completed")
        total = len(tracker.entries)
        # Build title with summary
        title = "Fetched plan with 1 task" if total == 1 else f"Fetched plan with {total} tasks"
        if completed > 0:
            title += f" ({completed} completed)"
        # Emit progress with plan preview
        plan_text = "\n".join(lines)
        item = TextContentItem(text=plan_text)
        await agent_ctx.events.tool_call_progress(title=title, items=[item])
        return ToolResult(content=plan_text)

    async def set_plan(
        self,
        agent_ctx: AgentContext,
        entries: list[dict[str, Any]],
    ) -> ToolResult:
        """Replace the entire plan with new entries (declarative/bulk update).

        This is more efficient than multiple add/update calls when setting
        or significantly modifying the plan.

        Args:
            agent_ctx: Agent execution context
            entries: List of plan entries, each with:
                - content (str, required): Task description
                - priority (str, optional): "high", "medium", or "low" (default: "medium")
                - status (str, optional): "pending", "in_progress", or "completed"
                  (default: "pending")

        Returns:
            Success message indicating plan was updated
        """
        tracker = self._get_tracker(agent_ctx)
        if tracker is None:
            return ToolResult(
                content="Error: No pool available for plan tracking",
            )

        tracker.clear()  # Clear existing entries
        # Add all new entries
        for entry in entries:
            content = entry.get("content", "")
            if not content:
                continue
            priority = entry.get("priority", "medium")
            status = entry.get("status", "pending")
            tracker.add(content, priority=priority, status=status)

        await self._emit_plan_update(agent_ctx)
        # Build summary for user feedback
        entry_count = len(tracker.entries)
        match entry_count:
            case 0:
                title = "Cleared plan"
            case 1:
                title = "Set plan with 1 task"
            case _:
                title = f"Set plan with {entry_count} tasks"

        # Format entries list for details
        if tracker.entries:
            lines = ["**New Plan:**"]
            for i, e in enumerate(tracker.entries):
                priority_emoji = PRIORITY_LABELS.get(e.priority, "")
                status_emoji = STATUS_ICONS.get(e.status, "")
                lines.append(f"{i + 1}. {priority_emoji} {status_emoji} {e.content}")
            details = "\n".join(lines)
        else:
            details = "*Plan is empty*"

        await agent_ctx.events.tool_call_progress(
            title=title,
            items=[TextContentItem(text=details)],
        )

        return ToolResult(
            content=f"Plan updated with {entry_count} entries",
        )

    async def add_plan_entry(
        self,
        agent_ctx: AgentContext,
        content: str,
        priority: TodoPriority = "medium",
        index: int | None = None,
    ) -> str:
        """Add a new plan entry.

        Args:
            agent_ctx: Agent execution context
            content: Description of what this task aims to accomplish
            priority: Relative importance (high/medium/low)
            index: Optional position to insert at (default: append to end)

        Returns:
            Success message indicating entry was added
        """
        tracker = self._get_tracker(agent_ctx)
        if tracker is None:
            return "Error: No pool available for plan tracking"

        entry = tracker.add(content, priority=priority, index=index)
        entry_index = tracker.entries.index(entry)

        await self._emit_plan_update(agent_ctx)

        # User feedback
        priority_emoji = PRIORITY_LABELS.get(priority, "")
        title = f"Added task {entry_index + 1} {priority_emoji}"
        details = f"**Task {entry_index + 1}**: {content}"
        await agent_ctx.events.tool_call_progress(
            title=title,
            items=[TextContentItem(text=details)],
        )

        return f"Added plan entry at index {entry_index}: {content!r} (priority={priority!r})"

    async def update_plan_entry(
        self,
        agent_ctx: AgentContext,
        index: int,
        content: str | None = None,
        status: TodoStatus | None = None,
        priority: TodoPriority | None = None,
    ) -> str:
        """Update an existing plan entry.

        Args:
            agent_ctx: Agent execution context
            index: Position of entry to update (0-based)
            content: New task description
            status: New execution status
            priority: New priority level

        Returns:
            Success message indicating what was updated
        """
        tracker = self._get_tracker(agent_ctx)
        if tracker is None:
            return "Error: No pool available for plan tracking"

        if index < 0 or index >= len(tracker.entries):
            return f"Error: Index {index} out of range (0-{len(tracker.entries) - 1})"

        updates = []
        if content is not None:
            updates.append(f"content to {content!r}")
        if status is not None:
            updates.append(f"status to {status!r}")
        if priority is not None:
            updates.append(f"priority to {priority!r}")

        if not updates:
            return "No changes specified"

        tracker.update_by_index(index, content=content, status=status, priority=priority)

        await self._emit_plan_update(agent_ctx)

        # Build title with key info
        entry = tracker.entries[index]
        status_emoji = STATUS_ICONS.get(entry.status, "")
        title = f"Updated task {index + 1} {status_emoji}"

        # Send detailed content
        details = f"**Task {index + 1}**: {entry.content}\n\nChanges: {', '.join(updates)}"
        await agent_ctx.events.tool_call_progress(
            title=title,
            items=[TextContentItem(text=details)],
        )

        return f"Updated entry {index}: {', '.join(updates)}"

    async def remove_plan_entry(self, agent_ctx: AgentContext, index: int) -> str:
        """Remove a plan entry.

        Args:
            agent_ctx: Agent execution context
            index: Position of entry to remove (0-based)

        Returns:
            Success message indicating entry was removed
        """
        tracker = self._get_tracker(agent_ctx)
        if tracker is None:
            return "Error: No pool available for plan tracking"

        if index < 0 or index >= len(tracker.entries):
            return f"Error: Index {index} out of range (0-{len(tracker.entries) - 1})"

        removed_entry = tracker.remove_by_index(index)
        await self._emit_plan_update(agent_ctx)

        if removed_entry is None:
            return f"Error: Could not remove entry at index {index}"

        # User feedback
        remaining = len(tracker.entries)
        title = f"Removed task {index + 1}"
        details = f"**Removed**: {removed_entry.content}\n\nRemaining tasks: {remaining}"
        await agent_ctx.events.tool_call_progress(
            title=title,
            items=[TextContentItem(text=details)],
        )

        if tracker.entries:
            return f"Removed entry {index}: {removed_entry.content!r}, remaining entries reindexed"
        return f"Removed entry {index}: {removed_entry.content!r}, plan is now empty"

    async def _emit_plan_update(self, agent_ctx: AgentContext) -> None:
        """Emit plan update event."""
        tracker = self._get_tracker(agent_ctx)
        if tracker is None:
            return

        await agent_ctx.events.plan_updated(tracker.entries)
