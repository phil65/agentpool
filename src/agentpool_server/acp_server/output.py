"""Protocol-neutral output of an ACP session.

The session decides what to tell the client (a turn's events, an agent event
between turns, a changed mode or model, the command list, an error); a
``SessionOutput`` says it in the shapes of one ACP version. v1's is
``V1SessionOutput`` (``v1_output.py``); see ``ACP_V2_SERVER.md`` for v2.
"""

from typing import TYPE_CHECKING, Any, Literal, Protocol


if TYPE_CHECKING:
    from collections.abc import Sequence

    from aclientp.schema import AvailableCommandsUpdate
    from slashed import BaseCommand
    from tokonomics.model_discovery.model_info import ModelInfo

    from agentpool.agents.events import RichAgentStreamEvent
    from agentpool.agents.modes import ConfigOptionChanged, ModeInfo
    from agentpool_server.acp_server.turns import TurnOutput


type AgentStateChange = ModeInfo | ModelInfo | AvailableCommandsUpdate | ConfigOptionChanged
"""What an agent reports through its ``state_updated`` signal.

``AvailableCommandsUpdate`` comes from nested ACP agents (their command list).
"""


type CommandToolKind = Literal[
    "read", "edit", "delete", "move", "search", "execute", "think", "fetch", "other"
]
"""What a slash command's tool call does (the ACP tool kinds, in both versions)."""

type CommandToolStatus = Literal["in_progress", "completed", "failed"]


class SessionOutput(Protocol):
    """Everything an ACP session sends to its client, in one protocol version."""

    def turn(self) -> TurnOutput:
        """Output for one prompt turn."""
        ...

    async def background_event(self, event: RichAgentStreamEvent[Any]) -> None:
        """Report an agent event produced between turns (e.g. a task finishing)."""
        ...

    async def state_changed(self, state: AgentStateChange) -> None:
        """Report a changed mode, model, config option or nested agent's commands."""
        ...

    async def commands_changed(self, commands: Sequence[BaseCommand]) -> None:
        """Report the session's current slash commands."""
        ...

    async def error(self, message: str) -> None:
        """Show an error to the user."""
        ...

    async def text(self, text: str) -> None:
        """Show text to the user as agent output (e.g. a slash command's)."""
        ...

    async def command_started(self, call_id: str, title: str, kind: CommandToolKind) -> None:
        """Show the start of a slash command's work as a tool call."""
        ...

    async def command_updated(
        self,
        call_id: str,
        *,
        status: CommandToolStatus,
        title: str | None = None,
        text: str | None = None,
    ) -> None:
        """Update a slash command's tool call; ``text`` (markdown) becomes its content."""
        ...
