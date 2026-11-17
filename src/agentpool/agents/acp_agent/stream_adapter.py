"""Stream adapter for converting ACP session updates to agentpool events.

The ACP agent communicates via a subprocess running the ACP protocol. Session
updates are pushed into an ACPSessionState queue, and this adapter polls that
queue (gated by an asyncio event) until the prompt task completes.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from agentpool.utils.streams.streamed_response import StreamedResponse
from agentpool.utils.time_utils import get_now


if TYPE_CHECKING:
    import asyncio
    from collections.abc import AsyncIterator
    from datetime import datetime

    from acp.schema import SessionUpdate
    from agentpool.agents.acp_agent.client_handler import TimeoutableEvent
    from agentpool.agents.acp_agent.session_state import ACPSessionState
    from agentpool.agents.events import RichAgentStreamEvent


@dataclass(kw_only=True)
class AcpAgentStreamedResponse(StreamedResponse):
    """Streamed ACP response that polls session state for updates."""

    state: ACPSessionState
    update_event: TimeoutableEvent
    prompt_task: asyncio.Task[Any]
    agent_name: str
    session_id: str
    tool_metadata: dict[str, dict[str, Any]]
    _timestamp: datetime = field(default_factory=get_now)
    _model_name: str | None = None
    _compaction_ids: set[str] = field(default_factory=set)

    async def _get_event_iterator(self) -> AsyncIterator[RichAgentStreamEvent[str]]:
        """Poll raw updates from ACP state, convert to events, until prompt completes."""
        while not self.prompt_task.done():
            try:
                await self.update_event.wait_with_timeout(0.05)
                self.update_event.clear()
            except TimeoutError:
                pass
            while (update := self.state.pop_update()) is not None:
                if native_event := self._convert(update):
                    yield native_event
        # Drain any remaining updates after prompt completes
        while (update := self.state.pop_update()) is not None:
            if native_event := self._convert(update):
                yield native_event

    def _convert(self, update: SessionUpdate) -> RichAgentStreamEvent[str] | None:
        from acp.schema import CompactionUpdate
        from agentpool.agents.acp_agent.acp_converters import acp_to_native_event

        # Later in_progress updates only patch an already started compaction
        if isinstance(update, CompactionUpdate) and update.status == "in_progress":
            if update.compaction_id in self._compaction_ids:
                return None
            self._compaction_ids.add(update.compaction_id)
        if native_event := acp_to_native_event(update, session_id=self.session_id):
            return self._enrich(native_event)
        return None

    def _enrich(self, event: RichAgentStreamEvent[str]) -> RichAgentStreamEvent[str]:
        """Enrich ToolCallCompleteEvents with agent name and tool metadata."""
        from agentpool.agents.events import ToolCallCompleteEvent

        if not isinstance(event, ToolCallCompleteEvent):
            return event
        if not event.agent_name:
            event = replace(event, agent_name=self.agent_name)
        if event.metadata is None and event.tool_call_id in self.tool_metadata:
            event = replace(event, metadata=self.tool_metadata[event.tool_call_id])
        return event

    @property
    def model_name(self) -> str:
        """Get the model name of the response."""
        assert self._model_name
        return self._model_name

    @property
    def timestamp(self) -> datetime:
        """Get the timestamp of the response."""
        return self._timestamp
