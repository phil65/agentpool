"""The user messages of a running Claude Code turn: its prompt and the ones steered into it.

The prompt and every steered message are sent with a ``uuid``, and the CLI names the
messages each result answers in ``user_message_uuids``. A run ends at the result that
answers the last of them: background work can keep the CLI busy past that point (a
background subagent, and the cycle the CLI starts on its own once it finishes, whose
result names none of the run's messages). That belongs to no run; the idle watcher reports
it. A CLI that does not name messages in results ends the run at ``idle`` as before.

A steered message goes in at the next step boundary (``priority`` next: after the running
tool call, folded into the same cycle) or at once (``now``: it ends the generation in
progress, whose cycle reports a result of its own). The CLI reports each message's progress
as ``command_lifecycle`` frames. A result names only the messages its cycle took in last: a
cycle a steer was folded into names the steer, not the prompt. So a result that names one of
the run's messages answers every message the CLI had taken in by then.
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from clawd_code_sdk.models import ResultErrorMessage, ResultSuccessMessage


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from clawd_code_sdk import ClaudeSDKClient, Message
    from clawd_code_sdk.models import CommandLifecycleMessage
    from clawd_code_sdk.models.messages import MessagePriority, ResultMessage

    from agentpool.agents.events.reconstructor import MessageReconstructor


@dataclass
class RunSteering:
    """The messages of one run that wait for an answer, and where steered ones join history."""

    client: ClaudeSDKClient
    """The client of the run (a fork client for runs that keep no history)."""

    reconstructor: MessageReconstructor
    """The run's history, which a steered message joins once the CLI takes it in."""

    unanswered: set[str] = field(default_factory=set)
    """Uuids of the run's messages (prompt and steered) that no result answered yet."""

    taken_in: set[str] = field(default_factory=set)
    """Uuids of the run's messages the CLI took in (the prompt, and steers once started)."""

    pending: dict[str, str] = field(default_factory=dict)
    """Text of the steered messages sent but not taken in yet, by uuid."""

    closed: bool = False
    """Whether the run got its last answer: later messages are not part of it."""

    async def send(self, message: str, *, priority: MessagePriority) -> None:
        """Send ``message`` into the running query."""
        message_uuid = await self.client.steer(message, priority=priority)
        self.pending[message_uuid] = message
        self.unanswered.add(message_uuid)

    def on_lifecycle(self, frame: CommandLifecycleMessage) -> None:
        """Follow a steered message through the CLI's queue."""
        match frame.state:
            case "started":
                self.taken_in.add(frame.command_uuid)
                if (text := self.pending.pop(frame.command_uuid, None)) is not None:
                    self.reconstructor.add_user_prompt(text)
            case "cancelled" | "discarded" | "refused":
                # No result will answer it
                self.pending.pop(frame.command_uuid, None)
                self.unanswered.discard(frame.command_uuid)
            case "queued" | "completed":
                pass

    def answers_run(self, result: ResultMessage) -> bool:
        """Whether ``result`` answers the last unanswered message of the run."""
        named = set(result.user_message_uuids or [])
        if not named & (self.unanswered | self.taken_in):  # Not about the run's messages
            return False
        self.unanswered -= named | self.taken_in
        self.closed = not self.unanswered
        return self.closed


async def run_messages(client: ClaudeSDKClient, steering: RunSteering) -> AsyncIterator[Message]:
    """The CLI's messages of a run: up to the result that answers it, else up to ``idle``."""
    async for message in client.receive_response():
        yield message
        match message:
            case ResultSuccessMessage() | ResultErrorMessage() if steering.answers_run(message):
                return
            case _:
                pass
