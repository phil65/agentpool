"""Stream adapter for converting pydantic-ai events to agentpool events.

Iterates over an AgentRun's graph nodes, streaming events from each
ModelRequestNode/CallToolsNode and converting tool call/result pairs
into the canonical ToolCallStartEvent / ToolCallCompleteEvent lifecycle.
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic_ai import CallToolsNode, ModelRequestNode
from pydantic_graph import End

from agentpool.agents.native_agent.helpers import ToolEventTracker, forward_supported_events
from agentpool.utils.streams import merge_queue_into_iterator
from agentpool.utils.streams.streamed_response import StreamedResponse
from agentpool.utils.time_utils import get_now


if TYPE_CHECKING:
    from asyncio import Queue
    from collections.abc import AsyncIterator
    from datetime import datetime

    from pydantic_ai import AgentRun

    from agentpool.agents.events import RichAgentStreamEvent


@dataclass(kw_only=True)
class PydanticAiStreamedResponse(StreamedResponse):
    """Streamed pydantic-ai response."""

    stream: AgentRun[Any, Any]
    agent_name: str
    message_id: str
    _timestamp: datetime = field(default_factory=get_now)
    _model_name: str | None = None
    _event_queue: Queue[RichAgentStreamEvent[Any]]

    async def _get_event_iterator(self) -> AsyncIterator[RichAgentStreamEvent[Any]]:
        tool_events = ToolEventTracker(agent_name=self.agent_name, message_id=self.message_id)
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
                            for tool_event in tool_events.process(event):
                                yield tool_event

    @property
    def model_name(self) -> str:
        """Get the model name of the response."""
        assert self._model_name
        return self._model_name

    @property
    def timestamp(self) -> datetime:
        """Get the timestamp of the response."""
        return self._timestamp
