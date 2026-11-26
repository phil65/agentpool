"""Base class for text sharing services."""

import abc
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal


if TYPE_CHECKING:
    from agentpool.messaging.message_history import MessageHistory


Visibility = Literal["public", "unlisted", "private"]


@dataclass
class ShareResult:
    """Result of a text sharing operation."""

    url: str
    """URL to access the shared content."""

    raw_url: str | None = None
    """Direct URL to raw content, if available."""

    delete_url: str | None = None
    """URL to delete the content, if available."""

    id: str | None = None
    """Provider-specific ID of the shared content."""


class TextSharer(abc.ABC):
    """Base class for text sharing services."""

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Name of the sharing service."""

    @abc.abstractmethod
    async def share(
        self,
        content: str,
        *,
        title: str | None = None,
        syntax: str | None = None,
        visibility: Visibility = "unlisted",
        expires_in: int | None = None,
    ) -> ShareResult:
        """Share text content.

        Args:
            content: The text content to share
            title: Optional title/filename for the content
            syntax: Syntax highlighting hint (e.g. "python", "markdown")
            visibility: Visibility level (not all providers support all levels)
            expires_in: Expiration time in seconds (not supported by all providers)

        Returns:
            ShareResult with URL and metadata
        """

    async def share_conversation(
        self,
        conversation: MessageHistory,
        *,
        title: str | None = None,
        visibility: Visibility = "unlisted",
        expires_in: int | None = None,
        num_messages: int | None = None,
    ) -> ShareResult:
        """Share a conversation in structured format.

        Default implementation formats conversation as text and calls share().
        Providers that support structured conversations (e.g., OpenCode)
        should override this method.

        Args:
            conversation: MessageHistory object to share
            title: Optional title for the conversation
            visibility: Visibility level
            expires_in: Expiration time in seconds
            num_messages: Number of messages to include (None = all)

        Returns:
            ShareResult with URL and metadata
        """
        # Default: format as plain text
        content = await conversation.format_history(
            num_messages=num_messages,
        )
        return await self.share(
            content,
            title=title,
            syntax="markdown",
            visibility=visibility,
            expires_in=expires_in,
        )

    def share_sync(
        self,
        content: str,
        *,
        title: str | None = None,
        syntax: str | None = None,
        visibility: Visibility = "unlisted",
        expires_in: int | None = None,
    ) -> ShareResult:
        """Synchronous version of share."""
        from anyenv import run_sync

        async def wrapper() -> ShareResult:
            return await self.share(
                content,
                title=title,
                syntax=syntax,
                visibility=visibility,
                expires_in=expires_in,
            )

        return run_sync(wrapper())
