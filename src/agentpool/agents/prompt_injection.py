"""Prompt injection and queuing manager for agents.

Provides unified handling for:
- Immediate injection (consumed by agent hooks mid-run)
- Queued prompts (processed after current run completes)
- Fallback behavior (unconsumed injections become queued prompts)
"""

from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

from agentpool.log import get_logger


if TYPE_CHECKING:
    from collections.abc import Generator

    from agentpool.common_types import PromptCompatible

logger = get_logger(__name__)

_HELD_BACK: ContextVar[bool] = ContextVar("agentpool_injections_held_back", default=False)
"""Whether hooks in this context leave pending injections alone (see :func:`held_back`)."""


@contextmanager
def held_back(active: bool = True) -> Generator[None]:
    """Leave pending injections to the turn while the block runs (when ``active``).

    For a nested self-call: user input steered into the turn meanwhile is the turn's,
    not the nested run's (its tools' hooks would take it in otherwise). Restores the
    previous value by value, so leaving from another context can't fail.
    """
    if not active:
        yield
        return
    previous = _HELD_BACK.get()
    _HELD_BACK.set(True)
    try:
        yield
    finally:
        _HELD_BACK.set(previous)


class PromptInjectionManager:
    """Manages prompt injection and queuing for agents.

    This class handles two types of prompt scheduling:

    1. **Injections** (`inject`): Messages to be injected mid-run via agent hooks.
       If a tool executes, the hook consumes the injection and adds it as
       additional context. If no tool runs, injections fall back to queued prompts.

    2. **Queued prompts** (`queue`): Prompts to be processed after the current
       run completes. The run_stream loop continues with these.
    """

    def __init__(self) -> None:
        """Initialize the injection manager."""
        self._pending_injections: list[str] = []
        self._queued_prompts: list[tuple[PromptCompatible, ...]] = []

    def inject(self, message: str) -> None:
        """Queue a message for immediate injection.

        The message will be consumed by the next tool hook (if supported).
        If no tool executes before the run iteration completes, the message
        is automatically moved to the queued prompts.

        Args:
            message: Message to inject
        """
        self._pending_injections.append(message)
        logger.debug("Queued injection", message_len=len(message))

    def queue(self, *prompts: PromptCompatible) -> None:
        """Queue prompts to be processed after current run completes.

        Args:
            *prompts: Prompts to queue (same format as run/run_stream)
        """
        self._queued_prompts.append(prompts)
        logger.debug("Queued prompt", num_parts=len(prompts))

    async def consume(self) -> str | None:
        """Consume the next pending injection.

        Called by agent-specific hooks (e.g., post-tool hooks) to get
        the next message to inject into the conversation. The message
        is wrapped in XML tags for clear delineation.

        Returns:
            The next injection message wrapped in XML tags, or None if none pending (or
            held back for the turn, see :func:`held_back`)
        """
        if self._pending_injections and not _HELD_BACK.get():
            msg = self._pending_injections.pop(0)
            logger.debug("Consumed injection", message_len=len(msg))
            return f"<injected-context>\n{msg}\n</injected-context>"
        return None

    async def consume_all(self) -> list[str]:
        """Consume all pending injections.

        Returns:
            List of all pending injection messages wrapped in XML tags (may be empty)
        """
        if _HELD_BACK.get():
            return []
        result = [f"<injected-context>\n{i}\n</injected-context>" for i in self._pending_injections]
        self._pending_injections.clear()
        if result:
            logger.debug("Consumed all injections", count=len(result))
        return result

    def flush_pending_to_queue(self) -> None:
        """Move unconsumed injections to the queued prompts.

        Called at the end of each run iteration. Any injections that weren't
        consumed by tool hooks become regular queued prompts, ensuring they
        still get processed.
        """
        if not self._pending_injections:
            return
        logger.debug("Flushing unconsumed injections to queue", count=len(self._pending_injections))
        for msg in self._pending_injections:
            self._queued_prompts.append((msg,))
        self._pending_injections.clear()

    def pop_queued(self) -> tuple[PromptCompatible, ...] | None:
        """Get the next queued prompt group.

        Returns:
            Tuple of prompts, or None if queue is empty
        """
        return self._queued_prompts.pop(0) if self._queued_prompts else None

    def insert_queued(self, prompts: tuple[PromptCompatible, ...]) -> None:
        """Insert prompts at the front of the queue.

        Used to add the initial prompts from run_stream.

        Args:
            prompts: Prompts to insert at front
        """
        self._queued_prompts.insert(0, prompts)

    def has_queued(self) -> bool:
        """Check if there are queued prompts waiting."""
        return bool(self._queued_prompts)

    def has_pending(self) -> bool:
        """Check if there are pending injections."""
        return bool(self._pending_injections)

    def clear(self) -> None:
        """Clear all pending injections and queued prompts.

        Called when run_stream exits (normally, cancelled, or on error).
        """
        self._pending_injections.clear()
        self._queued_prompts.clear()

    def __repr__(self) -> str:
        return (
            f"PromptInjectionManager("
            f"pending={len(self._pending_injections)}, "
            f"queued={len(self._queued_prompts)})"
        )
