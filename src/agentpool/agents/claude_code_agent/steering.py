"""Messages steered into a running Claude Code turn.

The CLI takes a steered message (a user message with ``priority`` and a ``uuid``) into
the running query: with ``now`` it ends the cycle that is generating, or moves a running
foreground tool to the background, and works on the message before it goes idle. So the
run's stream, which ends at ``idle``, carries the message's cycle too. The CLI reports
each message's progress as ``command_lifecycle`` frames.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from clawd_code_sdk import ClaudeSDKClient
    from clawd_code_sdk.models import CommandLifecycleMessage
    from clawd_code_sdk.models.messages import MessagePriority

    from agentpool.agents.events.reconstructor import MessageReconstructor


@dataclass
class RunSteering:
    """The messages steered into one run, and where they join its history."""

    client: ClaudeSDKClient
    """The client of the run (a fork client for runs that keep no history)."""

    reconstructor: MessageReconstructor
    """The run's history, which a message joins once the CLI takes it in."""

    pending: dict[str, str] = field(default_factory=dict)
    """Text of the messages sent but not taken in yet, by uuid."""

    async def send(self, message: str, *, priority: MessagePriority) -> None:
        """Send ``message`` into the running query."""
        message_uuid = await self.client.steer(message, priority=priority)
        self.pending[message_uuid] = message

    def on_lifecycle(self, frame: CommandLifecycleMessage) -> None:
        """Follow a steered message through the CLI's queue."""
        match frame.state:
            case "started":
                if (text := self.pending.pop(frame.command_uuid, None)) is not None:
                    self.reconstructor.add_user_prompt(text)
            case "cancelled" | "discarded" | "refused":
                self.pending.pop(frame.command_uuid, None)
            case "queued" | "completed":
                pass
