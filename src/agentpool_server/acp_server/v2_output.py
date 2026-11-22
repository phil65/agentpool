"""ACP v2 output of a session.

v2 sessions reuse the v1 output: its updates reach the client through
``V2ClientAdapter``, which translates them. What differs is said here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agentpool.agents.modes import ConfigOptionChanged, ModeInfo
from agentpool_server.acp_server.v1_output import V1SessionOutput


if TYPE_CHECKING:
    from agentpool_server.acp_server.output import AgentStateChange


class V2SessionOutput(V1SessionOutput):
    """``SessionOutput`` for ACP v2 clients."""

    async def state_changed(self, state: AgentStateChange) -> None:
        match state:
            case ModeInfo(value=mode_id, category_id="mode"):
                # v2 has no current_mode_update: the mode is a config option
                await super().state_changed(ConfigOptionChanged(config_id="mode", value_id=mode_id))
            case _:
                await super().state_changed(state)
