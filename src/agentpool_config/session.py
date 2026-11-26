"""Memory configuration for agent memory and history handling."""

from typing import TYPE_CHECKING, Literal, Self
from uuid import UUID

from pydantic import ConfigDict, Field
from schemez import Schema


MessageRole = Literal["user", "assistant"]


if TYPE_CHECKING:
    from datetime import datetime


class MemoryConfig(Schema):
    """Configuration for agent memory and history handling."""

    enable: bool = Field(default=True, title="Enable memory")
    """Whether to enable history tracking."""

    session: SessionQuery | None = Field(default=None, title="Session query")
    """Query configuration for loading previous session."""

    provider: str | None = Field(
        default=None,
        examples=["sqlite", "memory", "file"],
        title="Storage provider",
    )
    """Override default storage provider for this agent.
    If None, uses manifest's default provider or first available."""

    history_processors: list[str] | None = Field(
        default=None,
        examples=[["my_processors:keep_recent_messages", "my_module:summarize_old"]],
        title="History processors",
    )
    """List of import paths to history processor callables.

    History processors are applied by pydantic-ai before each model call to
    transform message history. They can:
    - Filter messages based on content or metadata
    - Truncate or summarize old messages
    - Make context-aware decisions using RunContext (usage, deps, model info)

    Each processor must be callable and accept one of these signatures:
    - def processor(messages: list[ModelMessage]) -> list[ModelMessage]
    - async def processor(messages: list[ModelMessage]) -> list[ModelMessage]
    - def processor(ctx: RunContext, messages: list[ModelMessage]) -> list[ModelMessage]
    - async def processor(ctx: RunContext, messages: list[ModelMessage]) -> list[ModelMessage]

    See: https://ai.pydantic.dev/history-processors/

    Execution Order
    ~~~~~~~~~~~~~~~
    When both CompactionPipeline and history processors are configured:
    1. CompactionPipeline (if configured) - Applied by MessageHistory.get_history()
       BEFORE agentlet creation
    2. History Processors (pydantic-ai native) - Applied by ModelRequestNode
       during model request preparation

    This allows CompactionPipeline to handle declarative transformations (filtering,
    truncating) while history processors implement context-aware logic (token-aware
    filtering, summarization, dynamic decisions).

    Security
    ~~~~~~~~
    History processors import arbitrary code from your project. Consider these risks:

    - Malicious imports: If you use configuration from untrusted sources, imported
      modules could execute harmful code. Always review import paths before deployment.
    - Side effects: Processors can modify state, make network calls, or access
      files. This may cause unexpected behavior or data leakage.
    - Denial-of-service: Slow or infinite-loop processors can block model calls.

    Best practices for security:
    - Use pure functions with no side effects when possible
    - Validate and sanitize processor inputs (message content, RunContext data)
    - Avoid network calls or file I/O within processors
    - Use isolated environments for untrusted configurations
    - Review and test all imported processor modules
    - Keep processors deterministic (same inputs = same outputs)

    Example processors:
    ```python
    # my_processors.py
    def keep_recent_messages(messages: list[ModelMessage]) -> list[ModelMessage]:
        return messages[-10:] if len(messages) > 10 else messages

    async def filter_thinking(messages: list[ModelMessage]) -> list[ModelMessage]:
        filtered = []
        for msg in messages:
            if isinstance(msg, ModelResponse):
                has_content = any(p for p in msg.parts if not p.is_thinking())
                if has_content:
                    filtered.append(msg)
            else:
                filtered.append(msg)
        return filtered

    def token_aware_filter(
        ctx: RunContext[None],
        messages: list[ModelMessage],
    ) -> list[ModelMessage]:
        if ctx.usage.total_tokens > 8000:
            return messages[-3:]
        return messages
    ```
    """

    model_config = ConfigDict(frozen=True)

    @classmethod
    def from_value(cls, value: bool | str | SessionQuery | UUID | None) -> Self:
        """Create MemoryConfig from any value."""
        match value:
            case False:
                return cls(enable=False)
            case str() | UUID():
                return cls(session=SessionQuery(name=str(value)))
            case SessionQuery():
                return cls(session=value)
            case None | True:
                return cls()
            case _:
                raise ValueError(f"Invalid memory configuration: {value}")


class SessionQuery(Schema):
    """Query configuration for session recovery."""

    name: str | None = Field(
        default=None,
        examples=["main_session", "user_123", "conversation_01"],
        title="Session name",
    )
    """Session identifier to match."""

    agents: set[str] | None = Field(default=None, title="Agent filter")
    """Filter by agent names."""

    since: str | None = Field(
        default=None,
        examples=["1h", "2d", "1w"],
        title="Time period lookback",
    )
    """Time period to look back (e.g. "1h", "2d")."""

    until: str | None = Field(default=None, examples=["1h", "2d", "1w"], title="Time period limit")
    """Time period to look up to."""

    contains: str | None = Field(
        default=None,
        examples=["error", "important", "task completed"],
        title="Content filter",
    )
    """Filter by message content."""

    roles: set[MessageRole] | None = Field(default=None, title="Role filter")
    """Only include specific message roles."""

    limit: int | None = Field(default=None, examples=[10, 50, 100], title="Message limit")
    """Maximum number of messages to return."""

    model_config = ConfigDict(frozen=True)

    def get_time_cutoff(self) -> datetime | None:
        """Get datetime from time period string."""
        from agentpool.utils.time_utils import get_now, parse_time_period

        if not self.since:
            return None
        delta = parse_time_period(self.since)
        return get_now() - delta
