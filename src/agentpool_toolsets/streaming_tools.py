"""Streaming Tools - Base classes for tools that use the current agent for streaming.

This module provides a pattern for creating tools that:
1. Take a description/intent as input from the outer agent
2. Use the SAME agent with forked history for streaming
3. Process that stream with custom logic (UI updates, incremental writes, etc.)

The key insight is that this sidesteps the limitation of pydantic-ai where tool
execution happens AFTER the model finishes streaming. By using the same agent
with forked history, we get:
- Streaming "for free" while maintaining the tool abstraction
- Full conversation context preserved (the agent knows what was discussed)
- Cache benefits from the shared conversation history

Cache behavior: The forked conversation shares the same cache as the outer agent
since it uses identical message history. The fork's new messages are NOT persisted
back to the main conversation.

Example usage:

    @streaming_tool(
        system_prompt="Output the complete file content.",
    )
    async def edit_file(
        ctx: AgentContext,
        stream: AsyncIterator[StreamChunk],
        path: str,
    ) -> str:
        content = ""
        async for chunk in stream:
            content += chunk.text
            await ctx.events.file_edit_progress(path, content, "in_progress")

        await write_file(path, content)
        return f"Edited {path}"

    # Exposed to outer agent as: edit_file(description: str, path: str) -> str
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
import functools
from typing import TYPE_CHECKING, Any

from pydantic_ai import PartDeltaEvent, TextPartDelta


if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from pydantic_ai import ModelRequest, ModelResponse

    from agentpool.agents.base_agent import BaseAgent
    from agentpool.agents.context import AgentContext
    from agentpool.messaging import MessageHistory


@dataclass
class StreamChunk:
    """A chunk of streamed text from the agent."""

    text: str
    """The text content of this chunk."""

    accumulated: str
    """All text accumulated so far."""

    is_complete: bool = False
    """Whether this is the final chunk."""


@dataclass
class StreamingToolConfig:
    """Configuration for a streaming tool."""

    prompt_template: str | None = None
    """Optional prompt template. Use {description} and {kwargs} placeholders."""

    include_file_content: bool = False
    """Whether to include file content in the prompt (for edit tools)."""


class StreamingToolBase(ABC):
    """Abstract base class for streaming tools.

    Subclass this to create tools that use the current agent for streaming.
    """

    config: StreamingToolConfig

    @abstractmethod
    async def process_stream(
        self,
        ctx: AgentContext,
        stream: AsyncIterator[StreamChunk],
        **kwargs: Any,
    ) -> str:
        """Process the streamed output, return final result for outer agent."""
        ...

    @abstractmethod
    def build_prompt(self, description: str, **kwargs: Any) -> str:
        """Build the prompt for the streaming request."""
        ...

    async def execute(self, ctx: AgentContext, description: str, **kwargs: Any) -> str:
        """Execute the streaming tool.

        This is called by the outer agent. It:
        1. Forks the current conversation history
        2. Streams a response using the same agent
        3. Passes the stream to process_stream()
        """
        agent = ctx.native_agent
        fork_history = _create_forked_history(agent)
        prompt = self.build_prompt(description, **kwargs)
        stream = agent.run_stream(prompt, message_history=fork_history, store_history=False)
        chunk_stream = _create_chunk_stream(stream)
        return await self.process_stream(ctx, chunk_stream, **kwargs)


def streaming_tool(
    prompt_template: str | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator that wraps a stream-processing function into a streaming tool.

    The decorated function should have the signature:
        async def fn(ctx: AgentContext, stream: AsyncIterator[StreamChunk], **kwargs) -> str

    The resulting tool will have the signature:
        async def fn(ctx: AgentContext, description: str, **kwargs) -> str

    Args:
        prompt_template: Optional template for building the prompt.
            Use {description} for the description and {key} for kwargs.

    Example:
        @streaming_tool()
        async def generate_code(
            ctx: AgentContext,
            stream: AsyncIterator[StreamChunk],
            language: str,
        ) -> str:
            code = ""
            async for chunk in stream:
                code = chunk.accumulated
                await ctx.events.progress(code)
            return f"Generated {len(code)} chars of {language} code"
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        async def wrapper(ctx: AgentContext, description: str, **kwargs: Any) -> str:
            agent = ctx.native_agent
            fork_history = _create_forked_history(agent)
            # Build prompt
            if prompt_template:
                prompt = prompt_template.format(description=description, **kwargs)
            else:
                prompt = _build_default_prompt(description, **kwargs)
            # Stream using the same agent with forked history
            stream = agent.run_stream(prompt, message_history=fork_history, store_history=False)
            chunk_stream = _create_chunk_stream(stream)
            return await fn(ctx, chunk_stream, **kwargs)  # type: ignore[no-any-return]

        # Mark as streaming tool for introspection
        wrapper._streaming_tool = True  # type: ignore[attr-defined]  # ty:ignore[missing-attribute]
        wrapper._prompt_template = prompt_template  # type: ignore[attr-defined]  # ty:ignore[missing-attribute]
        return wrapper

    return decorator


def _create_forked_history(agent: BaseAgent) -> MessageHistory:
    """Create a forked message history from the current conversation.

    The fork preserves full context for cache benefits while isolating
    new messages from the main conversation.
    """
    from agentpool.messaging import ChatMessage, MessageHistory

    if current_history := agent.conversation.get_history():
        # Get pydantic-ai messages and inject cache point
        all_messages: list[ModelRequest | ModelResponse] = []
        for msg in current_history:
            all_messages.extend(msg.messages)
        # Wrap in a single ChatMessage for the forked history
        msg = ChatMessage(messages=all_messages, role="user", content="")
        return MessageHistory(messages=[msg])

    return MessageHistory()


async def _create_chunk_stream(stream: Any) -> AsyncIterator[StreamChunk]:
    """Create an async iterator of StreamChunk from an agent stream."""
    accumulated = ""

    async for node in stream:
        match node:
            case PartDeltaEvent(delta=TextPartDelta(content_delta=text)):
                accumulated += text
                yield StreamChunk(text=text, accumulated=accumulated, is_complete=False)

    yield StreamChunk(text="", accumulated=accumulated, is_complete=True)


def _build_default_prompt(description: str, **kwargs: Any) -> str:
    """Build a default prompt from description and kwargs."""
    parts = [description]
    # Add context from kwargs if relevant
    for key, value in kwargs.items():
        if value is not None:
            parts.append(f"{key}: {value}")

    return "\n\n".join(parts)


# --- Example Usage ---
#
# @streaming_tool()
# async def streaming_write_file(
#     ctx: AgentContext,
#     stream: AsyncIterator[StreamChunk],
#     path: str,
# ) -> str:
#     """Write a file with streaming progress updates."""
#     content = ""
#
#     async for chunk in stream:
#         content = chunk.accumulated
#         await ctx.events.file_edit_progress(
#             path=path,
#             old_text="",
#             new_text=content,
#             status="streaming" if not chunk.is_complete else "completed",
#         )
#
#     # Write the file
#     from pathlib import Path
#     Path(path).write_text(content)
#     return f"Successfully created {path}"
