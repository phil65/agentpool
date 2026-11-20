"""Reads what Claude Code sends between runs.

When a background task finishes while no turn is running, the CLI reports it on its own
(``task_updated`` / ``task_notification``) and then starts a turn by itself to react to
it, ending with a result and ``session_state_changed: idle``. Left unread, those messages
would be taken as the start of the next run's response, shifting every following turn.

The watcher reads them while the agent is idle and emits everything as background events
(``BaseAgent.background_event``). The CLI's own turn is framed like a run: a
``RunStartedEvent``, its events (text, tool calls, steps), and a ``StreamCompleteEvent``
with the reconstructed message, so servers can show it as a turn of the session. A run of
the agent first waits until such a turn has finished.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any
import uuid

from clawd_code_sdk.models import SessionStateChangedMessage, TaskNotificationSystemMessage

from agentpool.agents.events import RunStartedEvent, StreamCompleteEvent
from agentpool.agents.events.reconstructor import MessageReconstructor
from agentpool.log import get_logger
from agentpool.messaging import ChatMessage


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from clawd_code_sdk import ClaudeSDKClient, Message

    from agentpool.agents.claude_code_agent.claude_code_agent import ClaudeCodeAgent
    from agentpool.agents.events import RichAgentStreamEvent


logger = get_logger(__name__)

TURN_EXPECTED_SECONDS = 10.0
"""After a task notification arrives while idle, how long a starting turn is awaited."""
TURN_TIMEOUT_SECONDS = 600.0
"""Longest a run waits for a turn the CLI started on its own."""


class IdleWatcher:
    """Consumes the CLI's messages while the agent runs no turn."""

    def __init__(self, agent: ClaudeCodeAgent[Any, Any]) -> None:
        self._agent = agent
        self._task: asyncio.Task[None] | None = None
        self._idle = asyncio.Event()
        self._idle.set()
        self._expect_turn: asyncio.TimerHandle | None = None
        self._turn: MessageReconstructor | None = None
        """The CLI's own turn in progress."""

    def start(self, client: ClaudeSDKClient) -> None:
        """Start reading (after a run, or once connected)."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(client), name="claude-code-idle-watch")

    async def stop(self) -> None:
        """Stop reading, after a turn the CLI started on its own has finished."""
        task, self._task = self._task, None
        if task is None:
            return
        try:
            async with asyncio.timeout(TURN_TIMEOUT_SECONDS):
                await self._idle.wait()
        except TimeoutError:
            logger.warning("Claude Code's own turn did not finish, starting the run anyway")
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _run(self, client: ClaudeSDKClient) -> None:
        from agentpool.agents.claude_code_agent.stream_adapter import ClaudeCodeStreamedResponse

        agent = self._agent
        adapter = ClaudeCodeStreamedResponse(
            provider_name="claude_code",
            stream=self._messages(client),
            tool_metadata=agent._tool_bridge.tool_metadata,
            agent_name=agent.name,
            session_id=agent.session_id or "",
            on_rate_limit=agent.record_rate_limit,
        )
        try:
            async for event in adapter:
                await self._handle(event)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Reading Claude Code's messages between runs failed")
        finally:
            self._set_idle()

    async def _messages(self, client: ClaudeSDKClient) -> AsyncIterator[Message]:
        """The client's messages, tracking whether the CLI is in a turn of its own."""
        async for message in client.receive_messages():
            if client.session_state != "idle":
                self._set_busy()
                await self._begin_turn()
            elif isinstance(message, TaskNotificationSystemMessage):
                # The CLI reacts with a turn of its own; it reports "running" right after.
                self._set_busy()
                loop = asyncio.get_running_loop()
                self._expect_turn = loop.call_later(TURN_EXPECTED_SECONDS, self._set_idle)
            yield message
            # Resumed once the adapter has handled the message
            if isinstance(message, SessionStateChangedMessage) and message.state == "idle":
                await self._end_turn()
                self._set_idle()

    def _set_busy(self) -> None:
        self._cancel_expect_turn()
        self._idle.clear()

    def _set_idle(self) -> None:
        self._cancel_expect_turn()
        self._idle.set()

    def _cancel_expect_turn(self) -> None:
        if self._expect_turn is not None:
            self._expect_turn.cancel()
            self._expect_turn = None

    async def _begin_turn(self) -> None:
        if self._turn is not None:
            return
        agent = self._agent
        self._turn = MessageReconstructor(model_name=agent.model_name, provider_name="claude_code")
        started = RunStartedEvent(
            session_id=agent.session_id or "", run_id=str(uuid.uuid4()), agent_name=agent.name
        )
        await agent.emit_event(started)

    async def _handle(self, event: RichAgentStreamEvent[Any]) -> None:
        if self._turn is not None:
            self._turn.observe(event)
        await self._agent.emit_event(event)

    async def _end_turn(self) -> None:
        turn, self._turn = self._turn, None
        if turn is None:
            return
        turn.flush()
        agent = self._agent
        message = ChatMessage[str](
            content=turn.text_content,
            role="assistant",
            name=agent.name,
            session_id=agent.session_id,
            model_name=agent.model_name,
            messages=turn.model_messages,
            finish_reason="stop",
        )
        await agent.emit_event(StreamCompleteEvent[str](message=message))
