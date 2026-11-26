"""The TTS event handlers' modes against anyvoice's TTSStream (a fake stream here)."""

from typing import Any, Self

from agentpool.agents.events import RunStartedEvent
from agentpool.agents.events.tts_handlers import BaseTTSEventHandler


class FakeStream:
    """Records the mode it was created with and its cancel() calls."""

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.cancelled = 0

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def feed(self, text: str) -> None:
        return None

    async def cancel(self) -> None:
        self.cancelled += 1


class Handler(BaseTTSEventHandler):
    """A handler whose streams are fakes."""

    def __init__(self, mode: Any) -> None:
        super().__init__(mode=mode)
        self.streams: list[FakeStream] = []

    async def _create_stream(self) -> Any:
        stream = FakeStream(self._stream_mode)
        self.streams.append(stream)
        return stream


async def _run_started(handler: Handler) -> None:
    await handler(Any, RunStartedEvent(session_id="s", run_id="r"))  # ty: ignore[invalid-argument-type]


async def test_async_cancel_uses_async_queue_and_cancels_the_last_runs_audio():
    handler = Handler("async_cancel")
    await _run_started(handler)
    await handler._ensure_stream()
    first = handler.streams[0]
    assert first.mode == "async_queue"
    await handler._close_stream()  # the run ended: its audio still plays

    await _run_started(handler)
    assert first.cancelled == 1
    await _run_started(handler)
    assert first.cancelled == 1


async def test_other_modes_pass_through_and_never_cancel():
    handler = Handler("async_queue")
    await handler._ensure_stream()
    await handler._close_stream()
    await _run_started(handler)
    assert handler.streams[0].mode == "async_queue"
    assert handler.streams[0].cancelled == 0
