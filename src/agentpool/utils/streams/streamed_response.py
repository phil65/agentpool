"""Base class for stream adapters translating vendor streams into AgentPool events."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic_ai import RequestUsage


if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from datetime import datetime

    from pydantic_ai import FinishReason

    from agentpool.agents.events import RichAgentStreamEvent


@dataclass(kw_only=True)
class StreamedResponse(ABC):
    """Streamed response from an LLM when calling a tool."""

    provider_name: str
    provider_response_id: str | None = field(default=None, init=False)
    provider_details: dict[str, Any] | None = field(default=None, init=False)
    finish_reason: FinishReason | None = field(default=None, init=False)
    _usage: RequestUsage = field(default_factory=RequestUsage, init=False)

    def __aiter__(self) -> AsyncIterator[RichAgentStreamEvent[Any]]:
        """Stream the response as an async iterable of [`RichAgentStreamEvent`]."""
        return self._get_event_iterator()

    @abstractmethod
    async def _get_event_iterator(self) -> AsyncIterator[RichAgentStreamEvent[Any]]:
        """Return an async iterator of RichAgentStreamEvents.

        This method should be implemented by subclasses to translate the vendor-specific stream
        of events into agentpool-format events.

        It should update the `_usage` attribute as it goes.
        """
        raise NotImplementedError
        # noinspection PyUnreachableCode
        yield

    # TODO (v2): Make this a property
    def usage(self) -> RequestUsage:
        """Get the usage of the response so far.

        This will not be the final usage until the stream is exhausted.
        """
        return self._usage

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Get the model name of the response."""
        raise NotImplementedError

    @property
    @abstractmethod
    def timestamp(self) -> datetime:
        """Get the timestamp of the response."""
        raise NotImplementedError
