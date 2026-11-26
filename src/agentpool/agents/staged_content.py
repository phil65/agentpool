"""Staged content."""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pydantic_ai import TextContent, UserPromptPart

from agentpool.log import get_logger


if TYPE_CHECKING:
    from pydantic_ai import SystemPromptPart


logger = get_logger(__name__)

INJECTED_KEY = "agentpool_injected"
"""Metadata key of prompt content agentpool added (rules, staged context), not the user."""


def injected_context(text: str) -> TextContent:
    """``text`` as prompt content that agentpool injected (the model sees it as text).

    Marked so that what shows the user's messages (history replay, titles) can leave it
    out; the mark survives storage (``TextContent.metadata``).
    """
    return TextContent(content=text, metadata={INJECTED_KEY: True})


def is_injected(item: object) -> bool:
    """Whether prompt content is agentpool's injected context rather than the user's.

    Also recognizes the plain-text form stored before content was marked.
    """
    match item:
        case TextContent(metadata={**meta}):
            return bool(meta.get(INJECTED_KEY))
        case str():
            return item.startswith("\n<context>\n") and item.endswith("\n</context>\n")
        case _:
            return False


@dataclass
class StagedContent:
    """Buffer for prompt parts to be injected into the next agent call.

    This allows commands (like /fetch-repo, /git-diff) to stage content that will
    be automatically included in the next prompt sent to the agent.
    """

    _parts: list[SystemPromptPart | UserPromptPart] = field(default_factory=list)

    def add(self, parts: list[SystemPromptPart | UserPromptPart]) -> None:
        """Add prompt parts to the staging area."""
        self._parts.extend(parts)

    def add_text(self, content: str) -> None:
        """Add text content to the staging area as a UserPromptPart."""
        self._parts.append(UserPromptPart(content=content))

    async def consume(self) -> list[SystemPromptPart | UserPromptPart]:
        """Return all staged parts and clear the buffer."""
        parts = self._parts.copy()
        self._parts.clear()
        return parts

    async def consume_as_text(self) -> str | None:
        """Return all staged content as a single string and clear the buffer.

        Returns:
            Combined text content, or None if nothing staged.
        """
        if not self._parts:
            return None
        texts = [part.content for part in self._parts if isinstance(part.content, str)]
        self._parts.clear()
        content = "\n\n".join(texts) if texts else None
        return f"\n<context>\n{content}\n</context>\n" if content else None

    async def consume_as_context(self) -> TextContent | None:
        """All staged content as injected context (see ``injected_context``), clearing it."""
        text = await self.consume_as_text()
        return injected_context(text) if text is not None else None

    def __len__(self) -> int:
        """Return count of staged parts."""
        return len(self._parts)
