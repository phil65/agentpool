"""Events of connections between nodes."""

from dataclasses import dataclass, field
from datetime import datetime  # noqa: TC003
from typing import TYPE_CHECKING, Any, Literal

from agentpool.utils.time_utils import get_now


if TYPE_CHECKING:
    from agentpool.messaging import ChatMessage
    from agentpool.talk.talk import Talk
    from agentpool_config.events import ConnectionEventType


ChangeType = Literal["added", "modified", "deleted"]


@dataclass(frozen=True, kw_only=True)
class ConnectionEventData[TTransmittedData]:
    """An event of a connection (``Talk``), as connection triggers and callbacks get it."""

    connection_name: str
    """Name of the connection which fired an event."""

    connection: Talk[TTransmittedData]
    """The connection which fired the event."""

    event_type: ConnectionEventType
    """Type of event that occurred."""

    message: ChatMessage[TTransmittedData] | None = None
    """The message at the stage of the event."""

    source: str = "connection"
    """What produced the event (as ``EventData.source``)."""

    timestamp: datetime = field(default_factory=get_now)
    """When the event occurred."""

    metadata: dict[str, Any] = field(default_factory=dict)
    """Additional data about the event."""

    def to_prompt(self) -> str:
        """Convert event to agent prompt."""
        base = f"Connection {self.connection_name!r} event: {self.event_type}"
        if self.message:
            return f"{base}\nMessage content: {self.message.content}"
        return base
