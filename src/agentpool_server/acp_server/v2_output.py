"""ACP v2 output of a session.

v2 sessions reuse the v1 output: its updates reach the client through
``V2ClientAdapter``, which translates them. What differs is said here: v2 has no
``current_mode_update``, it has agent-owned terminals (``TerminalTracker``) and a
``cancelled`` tool call status.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any

from aclientp.schema import v2

from agentpool.agents.events import ProcessSignal, TextContentItem, ToolCallProgressEvent
from agentpool.agents.modes import ConfigOptionChanged, ModeInfo
from agentpool_server.acp_server.turns import TurnCancelled
from agentpool_server.acp_server.v1_output import V1SessionOutput, V1TurnOutput
from agentpool_server.acp_server.v2_client import V2ClientAdapter
from agentpool_server.acp_server.v2_terminals import TerminalTracker


if TYPE_CHECKING:
    from collections.abc import Sequence

    from agentpool.agents.events import RichAgentStreamEvent
    from agentpool_server.acp_server.output import AgentStateChange
    from agentpool_server.acp_server.session import ACPSession
    from agentpool_server.acp_server.turns import TurnOutcome


class V2SessionOutput(V1SessionOutput):
    """``SessionOutput`` for ACP v2 clients."""

    def __init__(self, session: ACPSession) -> None:
        super().__init__(session)
        self.terminals = TerminalTracker(cwd=session.cwd)

    async def send(self, updates: Sequence[v2.SessionUpdate]) -> None:
        """Send v2 updates directly (they have no v1 form)."""
        client = self._session.client
        assert isinstance(client, V2ClientAdapter), "v2 sessions have the v2 adapter"
        for update in updates:
            await client.send_update(self._session.session_id, update)

    def turn(self) -> V2TurnOutput:
        return V2TurnOutput(self)

    async def background_event(self, event: RichAgentStreamEvent[Any]) -> None:
        await self.send(self.terminals.updates(event))
        await super().background_event(event)

    async def state_changed(self, state: AgentStateChange) -> None:
        match state:
            case ModeInfo(value=mode_id, category_id="mode"):
                # v2 has no current_mode_update: the mode is a config option
                await super().state_changed(ConfigOptionChanged(config_id="mode", value_id=mode_id))
            case _:
                await super().state_changed(state)


class V2TurnOutput(V1TurnOutput):
    """A turn's events for a v2 client: with its terminals, cancelled tools as cancelled."""

    def __init__(self, output: V2SessionOutput) -> None:
        super().__init__(output)
        self._v2 = output

    async def event(self, event: RichAgentStreamEvent[Any]) -> None:
        await self._v2.send(self._v2.terminals.updates(event))
        match event:
            case ToolCallProgressEvent(process=ProcessSignal(exit_code=int()), items=items):
                # The final output is in the terminal already: not again as text
                items = [i for i in items if not isinstance(i, TextContentItem)]
                event = dataclasses.replace(event, items=items)
            case _:
                pass
        await super().event(event)

    async def finish(self, outcome: TurnOutcome) -> None:
        if isinstance(outcome, TurnCancelled) and (converter := self._converter) is not None:
            pending = converter.pending_tool_calls()
            cancelled: list[v2.SessionUpdate] = [
                v2.ToolCallSessionUpdate(tool_call_id=tool_call_id, status="cancelled")
                for tool_call_id in pending
            ]
            await self._v2.send([*self._v2.terminals.end(pending), *cancelled])
            converter.reset()  # Nothing left for the v1 cancellation to report
        await super().finish(outcome)
