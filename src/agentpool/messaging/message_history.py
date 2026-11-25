"""Conversation management for AgentPool."""

from __future__ import annotations

import asyncio
from collections import deque
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, Self

from upathtools import read_path, to_upath

from agentpool.log import get_logger
from agentpool.storage import StorageManager
from agentpool_config.session import SessionQuery


if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Coroutine, Sequence

    from pydantic_ai import UserContent
    from toprompt import AnyPromptType
    from upathtools import JoinablePathLike

    from agentpool.messaging import ChatMessage
    from agentpool.prompts.conversion_manager import ConversionManager
    from agentpool.prompts.prompts import PromptType
    from agentpool_config.session import MemoryConfig

logger = get_logger(__name__)


class MessageHistory:
    """Manages conversation state and system prompts."""

    def __init__(
        self,
        storage: StorageManager | None = None,
        converter: ConversionManager | None = None,
        *,
        messages: list[ChatMessage[Any]] | None = None,
        session_config: MemoryConfig | None = None,
        resources: Sequence[PromptType | str] = (),
    ) -> None:
        """Initialize conversation manager.

        Args:
            storage: Storage manager for persistence
            converter: Content converter for file processing
            messages: Optional list of initial messages
            session_config: Optional MemoryConfig
            resources: Optional paths to load as context
        """
        from agentpool.messaging import ChatMessageList
        from agentpool.prompts.conversion_manager import ConversionManager
        from agentpool_config.storage import MemoryStorageConfig, StorageConfig

        storage_cfg = StorageConfig(providers=[MemoryStorageConfig()])
        self._storage = storage or StorageManager(config=storage_cfg)
        self._converter = converter or ConversionManager([])
        self.chat_messages = ChatMessageList()
        self.chat_messages.extend(messages or [])
        self._last_messages: list[ChatMessage[Any]] = []
        self._pending_parts: deque[UserContent] = deque()
        self._config = session_config
        self._resources = list(resources)  # Store for async loading
        # Generate new ID if none provided
        if session_config and session_config.session:
            self._current_history = self.storage.filter_messages.sync(session_config.session)

    @property
    def storage(self) -> StorageManager:
        return self._storage

    def get_initialization_tasks(self) -> list[Coroutine[Any, Any, Any]]:
        """Get all initialization coroutines."""
        tasks: list[Coroutine[Any, Any, Any]] = [
            self.load_context_source(source) for source in self._resources
        ]
        self._resources = []  # Clear so we dont load again on async init
        return tasks

    async def __aenter__(self) -> Self:
        """Initialize when used standalone."""
        if tasks := self.get_initialization_tasks():
            await asyncio.gather(*tasks)
        return self

    async def __aexit__(self, *args: object) -> None:
        """Clean up any pending parts."""
        self._pending_parts.clear()

    def __prompt__(self) -> str:
        if not self.chat_messages:
            return "No conversation history"

        last_msgs = self.chat_messages[-2:]
        parts = ["Recent conversation:"]
        parts.extend(msg.format() for msg in last_msgs)
        return "\n".join(parts)

    def __contains__(self, item: Any) -> bool:
        """Check if item is in history."""
        return item in self.chat_messages

    def __len__(self) -> int:
        """Get length of history."""
        return len(self.chat_messages)

    async def format_history(
        self,
        *,
        max_tokens: int | None = None,
        format_template: str | None = None,
        num_messages: int | None = None,
    ) -> str:
        """Format conversation history as a single context message.

        Args:
            max_tokens: Optional limit to include only last N tokens
            format_template: Optional custom format (defaults to agent/message pairs)
            num_messages: Optional limit to include only last N messages

        Note:
            System prompts are stored as metadata (ModelRequest.instructions),
            not as separate messages with role="system". ChatMessage.role only
            supports "user" and "assistant".
        """
        template = format_template or "Agent {agent}: {content}\n"
        messages: list[str] = []
        token_count = 0

        # Get messages, optionally limited
        history: Sequence[ChatMessage[Any]] = self.chat_messages
        if num_messages:
            history = history[-num_messages:]

        if max_tokens:
            history = list(reversed(history))  # Start from newest when token limited

        for msg in history:
            name = msg.name or msg.role.title()
            formatted = template.format(agent=name, content=str(msg.content))

            if max_tokens:
                # Count tokens in this message
                msg_tokens = msg.get_token_count()
                if token_count + msg_tokens > max_tokens:
                    break
                token_count += msg_tokens
                # Add to front since we're going backwards
                messages.insert(0, formatted)
            else:
                messages.append(formatted)

        return "\n".join(messages)

    async def load_context_source(self, source: PromptType | str) -> None:
        """Load context from a single source."""
        from agentpool.prompts.prompts import BasePrompt

        try:
            match source:
                case str():
                    await self.add_context_from_path(source)
                case BasePrompt():
                    await self.add_context_from_prompt(source)
        except Exception:
            source = "file" if isinstance(source, str) else source.type
            logger.exception("Failed to load context", source=source)

    def get_history(self) -> list[ChatMessage[Any]]:
        """Get conversation history in chronological order."""
        return list(self.chat_messages)

    def get_pending_parts(self) -> list[UserContent]:
        """Get and clear pending content parts for the next interaction.

        Returns:
            List of pending UserContent parts, clearing the internal queue.
        """
        parts = list(self._pending_parts)
        self._pending_parts.clear()
        return parts

    def set_history(self, history: list[ChatMessage[Any]]) -> None:
        """Update conversation history after run."""
        self.chat_messages.clear()
        self.chat_messages.extend(history)

    async def clear(self) -> None:
        """Clear conversation history and prompts."""
        from agentpool.messaging import ChatMessageList

        self.chat_messages = ChatMessageList()
        self._last_messages = []

    @asynccontextmanager
    async def temporary_state(
        self,
        history: list[AnyPromptType] | SessionQuery | None = None,
        *,
        replace_history: bool = False,
    ) -> AsyncGenerator[Self]:
        """Temporarily set conversation history.

        Args:
            history: Optional list of prompts to use as temporary history.
                    Can be strings, BasePrompts, or other prompt types.
            replace_history: If True, only use provided history. If False, append
                    to existing history.
        """
        from toprompt import to_prompt

        from agentpool.messaging import ChatMessage, ChatMessageList

        old_history = self.chat_messages.copy()
        messages: Sequence[ChatMessage[Any]] = ChatMessageList()
        try:
            if history is not None:
                if isinstance(history, SessionQuery):
                    messages = await self.storage.filter_messages(history)
                else:
                    messages = [
                        ChatMessage.user_prompt(message=prompt)
                        for p in history
                        if (prompt := await to_prompt(p))
                    ]

            if replace_history:
                self.chat_messages = ChatMessageList(messages)
            else:
                self.chat_messages.extend(messages)

            yield self

        finally:
            self.chat_messages = old_history

    def add_chat_messages(self, messages: Sequence[ChatMessage[Any]]) -> None:
        """Add new messages to history and update last_messages."""
        self._last_messages = list(messages)
        self.chat_messages.extend(messages)

    @property
    def last_run_messages(self) -> list[ChatMessage[Any]]:
        """Get messages from the last run converted to our format."""
        return self._last_messages

    def add_context_message(self, content: str, source: str | None = None, **metadata: Any) -> None:
        """Add a text context message.

        Args:
            content: Text content to add
            source: Description of content source
            **metadata: Additional metadata to include with the message
        """
        meta_str = ""
        if metadata:
            meta_str = "\n".join(f"{k}: {v}" for k, v in metadata.items())
            meta_str = f"\nMetadata:\n{meta_str}\n"

        header = f"Content from {source}:" if source else "Additional context:"
        formatted = f"{header}{meta_str}\n{content}\n"
        self._pending_parts.append(formatted)

    async def add_context_from_path(
        self,
        path: JoinablePathLike,
        *,
        convert_to_md: bool = False,
        **metadata: Any,
    ) -> None:
        """Add file or URL content as context message.

        Args:
            path: Any UPath-supported path
            convert_to_md: Whether to convert content to markdown
            **metadata: Additional metadata to include with the message

        Raises:
            ValueError: If content cannot be loaded or converted
        """
        path_obj = to_upath(path)
        if convert_to_md:
            content = await self._converter.convert_file(path)
            source = f"markdown:{path_obj.name}"
        else:
            content = await read_path(path)
            source = f"{path_obj.protocol}:{path_obj.name}"
        self.add_context_message(content, source=source, **metadata)

    async def add_context_from_prompt(
        self,
        prompt: PromptType,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """Add rendered prompt content as context message.

        Args:
            prompt: AgentPool prompt (static, dynamic, or file-based)
            metadata: Additional metadata to include with the message
            kwargs: Optional kwargs for prompt formatting
        """
        try:
            messages = await prompt.format(kwargs)
            # Extract text content from all messages
            content = "\n\n".join(msg.get_text_content() for msg in messages)
            self.add_context_message(
                content,
                source=f"prompt:{prompt.name or prompt.type}",
                prompt_args=kwargs,
                **(metadata or {}),
            )
        except Exception as e:
            raise ValueError(f"Failed to format prompt: {e}") from e

    def get_last_message_id(self) -> str | None:
        """Get the message_id of the last message in history."""
        return msgs[-1].message_id if (msgs := self.chat_messages) else None


if __name__ == "__main__":
    import anyio

    from agentpool import Agent

    async def main() -> None:
        async with Agent(model="openai:gpt-5-nano") as agent:
            print(agent.conversation.get_history())

    anyio.run(main)
