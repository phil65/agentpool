from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from pydantic_ai import ModelMessage, RunContext


def keep_recent(messages: list[ModelMessage]) -> list[ModelMessage]:
    """Keep only the last 2 messages."""
    return messages[-2:] if len(messages) > 2 else messages


async def filter_thinking_async(messages: list[ModelMessage]) -> list[ModelMessage]:
    """Async processor that filters out thinking messages (mock)."""
    # In a real scenario, we might look for certain part types
    return [m for m in messages if not getattr(m, "is_thinking", False)]


def context_aware_sync(ctx: RunContext[Any], messages: list[ModelMessage]) -> list[ModelMessage]:
    """Context-aware processor that adds a prefix to the first message content."""
    # Mock implementation
    return messages


async def context_aware_async(
    ctx: RunContext[Any], messages: list[ModelMessage]
) -> list[ModelMessage]:
    """Async context-aware processor."""
    return messages


def invalid_processor_too_many(messages: Any, msgs: Any, extra: Any) -> Any:
    """Invalid signature (too many args)."""
    return messages


def invalid_processor_wrong_name(messages: Any, extra_arg: Any) -> Any:
    """Invalid signature (wrong name)."""
    return messages
