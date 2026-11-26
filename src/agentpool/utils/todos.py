"""Todo/plan entry models and tracker."""

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
import time
from typing import TYPE_CHECKING, Any, Literal


if TYPE_CHECKING:
    from collections.abc import Sequence


TodoPriority = Literal["high", "medium", "low"]
TodoStatus = Literal["pending", "in_progress", "completed", "cancelled"]


STATUS_ICONS = {"pending": "⬚", "in_progress": "◐", "completed": "✓", "cancelled": "✗"}
PRIORITY_LABELS = {"high": "🔴", "medium": "🟡", "low": "🟢"}


@dataclass(kw_only=True)
class PlanEntry:
    """A single entry in the execution plan.

    Represents a task or goal that the assistant intends to accomplish
    as part of fulfilling the user's request.
    """

    content: str
    """Human-readable description of what this task aims to accomplish."""

    priority: TodoPriority = "medium"
    """The relative importance of this task."""

    status: TodoStatus = "pending"
    """Current execution status of this task."""


@dataclass(kw_only=True)
class TodoEntry(PlanEntry):
    """A tracked todo/plan entry with ID and timestamp."""

    id: str
    """Unique identifier for this entry."""

    created_at: float = field(default_factory=time.time)
    """Unix timestamp when the entry was created."""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "content": self.content,
            "status": self.status,
            "priority": self.priority,
            "created_at": self.created_at,
        }


# Type for todo change callback (async coroutine)
TodoChangeCallback = Callable[["TodoTracker"], Coroutine[Any, Any, None]]


@dataclass
class TodoTracker:
    """Tracks todo/plan entries at the pool level.

    Provides a central place to manage todos that persists across
    agent runs and is accessible from any toolset or endpoint.
    """

    entries: list[TodoEntry] = field(default_factory=list)
    """List of all todo entries."""

    _id_counter: int = field(default=0, repr=False)
    """Counter for generating unique IDs."""

    on_change: TodoChangeCallback | None = field(default=None, repr=False)
    """Optional async callback invoked when todos change."""

    _pending_tasks: set[asyncio.Task[None]] = field(default_factory=set, repr=False)
    """Track pending notification tasks to prevent garbage collection."""

    def _notify_change(self) -> None:
        """Notify listener of changes (schedules async callback)."""
        if self.on_change is not None:
            task: asyncio.Task[None] = asyncio.create_task(self.on_change(self))
            self._pending_tasks.add(task)
            task.add_done_callback(self._pending_tasks.discard)

    def _next_id(self) -> str:
        """Generate next unique ID."""
        self._id_counter += 1
        return f"todo_{self._id_counter}"

    def add(
        self,
        content: str,
        *,
        priority: TodoPriority = "medium",
        status: TodoStatus = "pending",
        index: int | None = None,
    ) -> TodoEntry:
        """Add a new todo entry. Appends if no index is given."""
        id_ = self._next_id()
        entry = TodoEntry(id=id_, content=content, priority=priority, status=status)
        if index is None or index >= len(self.entries):
            self.entries.append(entry)
        else:
            self.entries.insert(max(0, index), entry)
        self._notify_change()
        return entry

    def get(self, entry_id: str) -> TodoEntry | None:
        """Get entry by ID, or None if not found."""
        return next((entry for entry in self.entries if entry.id == entry_id), None)

    def get_by_index(self, index: int) -> TodoEntry | None:
        """Get entry by index (0-based), or None if not found."""
        return self.entries[index] if 0 <= index < len(self.entries) else None

    def update(
        self,
        entry_id: str,
        *,
        content: str | None = None,
        status: TodoStatus | None = None,
        priority: TodoPriority | None = None,
    ) -> bool:
        """Update an existing entry.

        Args:
            entry_id: The entry ID to update
            content: New content (if provided)
            status: New status (if provided)
            priority: New priority (if provided)

        Returns:
            True if entry was found and updated, False otherwise
        """
        entry = self.get(entry_id)
        if entry is None:
            return False

        changed = False
        if content is not None and entry.content != content:
            entry.content = content
            changed = True
        if status is not None and entry.status != status:
            entry.status = status
            changed = True
        if priority is not None and entry.priority != priority:
            entry.priority = priority
            changed = True
        if changed:
            self._notify_change()
        return True

    def update_by_index(
        self,
        index: int,
        *,
        content: str | None = None,
        status: TodoStatus | None = None,
        priority: TodoPriority | None = None,
    ) -> bool:
        """Update an entry by index.

        Args:
            index: The 0-based index
            content: New content (if provided)
            status: New status (if provided)
            priority: New priority (if provided)

        Returns:
            True if entry was found and updated, False otherwise
        """
        entry = self.get_by_index(index)
        if entry is None:
            return False

        changed = False
        if content is not None and entry.content != content:
            entry.content = content
            changed = True
        if status is not None and entry.status != status:
            entry.status = status
            changed = True
        if priority is not None and entry.priority != priority:
            entry.priority = priority
            changed = True
        if changed:
            self._notify_change()
        return True

    def remove(self, entry_id: str) -> bool:
        """Remove an entry by ID. Returns True if found + removed."""
        for i, entry in enumerate(self.entries):
            if entry.id == entry_id:
                self.entries.pop(i)
                self._notify_change()
                return True
        return False

    def remove_by_index(self, index: int) -> TodoEntry | None:
        """Remove an entry by index (0-based) and return its entry if found."""
        if 0 <= index < len(self.entries):
            entry = self.entries.pop(index)
            self._notify_change()
            return entry
        return None

    def clear(self) -> None:
        """Clear all entries."""
        if self.entries:
            self.entries.clear()
            self._notify_change()

    def replace_all(self, entries: Sequence[PlanEntry]) -> None:
        """Replace all entries with new ones (single notification).

        More efficient than clear() + multiple add() calls since it only
        triggers one change notification.

        Args:
            entries: Plan entries to replace current entries with.
        """
        self.entries.clear()
        for entry in entries:
            todo = TodoEntry(
                id=self._next_id(),
                content=entry.content,
                priority=entry.priority,
                status=entry.status,
            )
            self.entries.append(todo)
        self._notify_change()

    def get_by_status(self, status: TodoStatus) -> list[TodoEntry]:
        """Get all entries with a specific status."""
        return [e for e in self.entries if e.status == status]

    def to_list(self) -> list[dict[str, Any]]:
        """Convert to list of dicts for JSON serialization."""
        return [e.to_dict() for e in self.entries]
