"""Messages Claude Code sends between runs (background task ends, turns of its own)."""

import asyncio
from typing import TYPE_CHECKING, Any

from clawd_code_sdk.models import message_adapter

from agentpool.agents.claude_code_agent import ClaudeCodeAgent
from agentpool.agents.claude_code_agent.idle_watch import IdleWatcher
from agentpool.agents.events import (
    PartDeltaEvent,
    RunStartedEvent,
    StreamCompleteEvent,
    TaskFinishedEvent,
)


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from clawd_code_sdk import Message

    from agentpool.agents.base_agent import BaseAgent


TIMEOUT = 5


def _frame(data: dict[str, Any]) -> Message:
    message: Message = message_adapter.validate_python({"session_id": "s", "uuid": "u", **data})
    return message


def _state(state: str) -> Message:
    return _frame({"type": "system", "subtype": "session_state_changed", "state": state})


def _text(text: str) -> list[Message]:
    start = {
        "type": "content_block_start",
        "index": 0,
        "content_block": {"type": "text", "text": ""},
    }
    delta = {
        "type": "content_block_delta",
        "index": 0,
        "delta": {"type": "text_delta", "text": text},
    }
    return [
        _frame({"type": "stream_event", "parent_tool_use_id": None, "event": event})
        for event in (start, delta)
    ]


NOTIFICATION = _frame({
    "type": "system",
    "subtype": "task_notification",
    "task_id": "bg1",
    "status": "completed",
    "output_file": "/tmp/bg1.output",
    "summary": "Background command finished",
})


class FakeClient:
    """The part of ClaudeSDKClient the watcher uses."""

    def __init__(self) -> None:
        self.session_state = "idle"
        self.queue: asyncio.Queue[Message] = asyncio.Queue()

    async def receive_messages(self) -> AsyncIterator[Message]:
        while True:
            message = await self.queue.get()
            if message.type == "system" and message.subtype == "session_state_changed":
                self.session_state = message.state
            yield message


async def test_turn_of_its_own_is_read_and_reported() -> None:
    agent = ClaudeCodeAgent(name="cc")
    received: list[Any] = []

    async def on_background(background: BaseAgent.BackgroundEvent) -> None:
        received.append(background.event)

    agent.background_event.connect(on_background)
    watcher = IdleWatcher(agent)
    client = FakeClient()
    watcher.start(client)  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]
    for message in [NOTIFICATION, _state("running"), *_text("Done, exit code 0.")]:
        client.queue.put_nowait(message)
    await asyncio.sleep(0.05)
    # A run waits for the CLI's own turn to end
    stopping = asyncio.create_task(watcher.stop())
    await asyncio.sleep(0.05)
    assert not stopping.done()
    client.queue.put_nowait(_state("idle"))
    async with asyncio.timeout(TIMEOUT):
        await stopping
    # The task's end, then the CLI's own turn framed like a run
    finished, started, *turn, complete = received
    assert isinstance(finished, TaskFinishedEvent)
    assert (finished.task_id, finished.status) == ("bg1", "completed")
    assert isinstance(started, RunStartedEvent)
    assert started.agent_name == "cc"
    assert any(isinstance(event, PartDeltaEvent) for event in turn)
    assert isinstance(complete, StreamCompleteEvent)
    assert complete.message.content == "Done, exit code 0."


async def test_stop_while_idle_leaves_later_messages_for_the_run() -> None:
    agent = ClaudeCodeAgent(name="cc")
    watcher = IdleWatcher(agent)
    client = FakeClient()
    watcher.start(client)  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]
    await asyncio.sleep(0.01)
    async with asyncio.timeout(TIMEOUT):
        await watcher.stop()
    message = _state("running")
    client.queue.put_nowait(message)
    assert client.queue.get_nowait() is message


async def test_tail_of_the_last_run_is_read_before_the_next_run() -> None:
    # A run ends at the result that answers it: its lifecycle frame and the CLI's idle
    # are still to come when the watcher starts, and the next run must not read them
    agent = ClaudeCodeAgent(name="cc")
    received: list[Any] = []

    async def on_background(background: BaseAgent.BackgroundEvent) -> None:
        received.append(background.event)

    agent.background_event.connect(on_background)
    watcher = IdleWatcher(agent)
    client = FakeClient()
    client.session_state = "running"
    watcher.start(client)  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]
    stopping = asyncio.create_task(watcher.stop())
    await asyncio.sleep(0.05)
    assert not stopping.done()  # The next run waits for the tail
    completed = _frame({
        "type": "command_lifecycle",
        "command_uuid": "p",
        "state": "completed",
    })
    for message in [completed, _state("idle")]:
        client.queue.put_nowait(message)
    async with asyncio.timeout(TIMEOUT):
        await stopping
    assert client.queue.empty()
    assert received == []  # The tail is no turn of the CLI's own
