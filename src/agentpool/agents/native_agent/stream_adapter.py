"""Stream adapter for converting pydantic-ai events to agentpool events.

Iterates over an AgentRun's graph nodes, streaming events from each
ModelRequestNode/CallToolsNode and converting tool call/result pairs
into ToolCallCompleteEvents.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic_ai import CallToolsNode, ModelRequestNode
from pydantic_graph import End

from agentpool.agents.native_agent.helpers import forward_supported_events, process_tool_event
from agentpool.utils.streams import merge_queue_into_iterator
from agentpool.utils.streams.streamed_response import StreamedResponse
from agentpool.utils.time_utils import get_now


if TYPE_CHECKING:
    from asyncio import Queue
    from collections.abc import AsyncIterator
    from datetime import datetime

    from pydantic_ai import AgentRun, BaseToolCallPart

    from agentpool.agents.events import RichAgentStreamEvent


@dataclass(kw_only=True)
class PydanticAiStreamedResponse(StreamedResponse):
    """Streamed pydantic-ai response."""

    stream: AgentRun[Any, Any]
    tool_metadata: dict[str, dict[str, Any]]
    agent_name: str
    message_id: str
    _timestamp: datetime = field(default_factory=get_now)
    _model_name: str | None = None
    _event_queue: Queue[RichAgentStreamEvent[Any]]

    async def _get_event_iterator(self) -> AsyncIterator[RichAgentStreamEvent[Any]]:
        pending_tcs: dict[str, BaseToolCallPart] = {}
        async for node in self.stream:
            match node:
                case End():
                    break
                case ModelRequestNode() | CallToolsNode():
                    async with (
                        node.stream(self.stream.ctx) as stream,
                        merge_queue_into_iterator(
                            forward_supported_events(stream), self._event_queue
                        ) as merged,
                    ):
                        async for event in merged:
                            yield event
                            if combined := process_tool_event(
                                self.agent_name,
                                event,
                                pending_tcs,
                                self.message_id,
                            ):
                                yield combined

    @property
    def model_name(self) -> str:
        """Get the model name of the response."""
        assert self._model_name
        return self._model_name

    @property
    def timestamp(self) -> datetime:
        """Get the timestamp of the response."""
        return self._timestamp
