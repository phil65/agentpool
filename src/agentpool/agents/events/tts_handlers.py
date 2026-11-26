"""TTS event handlers for streaming text-to-speech synthesis."""

from abc import abstractmethod
from typing import TYPE_CHECKING, Any, Literal

from pydantic_ai import PartDeltaEvent, PartStartEvent, TextPart, TextPartDelta


if TYPE_CHECKING:
    from anyvoice import TTSStream

    from agentpool.agents.context import AgentContext
    from agentpool.agents.events import RichAgentStreamEvent

TTSMode = Literal["sync_sentence", "sync_run", "async_queue", "async_cancel"]
TTSModel = Literal["tts-1", "tts-1-hd"]
TTSVoice = Literal["alloy", "echo", "fable", "onyx", "nova", "shimmer"]


class BaseTTSEventHandler:
    """Base TTS event handler with shared event handling logic.

    This base class handles the common pattern of:
    - Managing TTS stream lifecycle (create, close)
    - Translating pydantic-ai stream events to TTSStream.feed() calls
    - Handling different synchronization modes

    Subclasses only need to implement _create_stream() with their
    specific provider and session configuration.
    """

    def __init__(
        self,
        *,
        sample_rate: int = 24000,
        min_text_length: int = 20,
        mode: TTSMode = "sync_run",
    ) -> None:
        self._sample_rate = sample_rate
        self._min_text_length = min_text_length
        self._mode: TTSMode = mode
        self._tts_stream: TTSStream | None = None
        # async_cancel: the last run's stream, still playing in the background
        # after its context exited; the next run cancels it
        self._previous_stream: TTSStream | None = None

    @property
    def _stream_mode(self) -> Literal["sync_sentence", "sync_run", "async_queue"]:
        """The mode for anyvoice: async_cancel is async_queue plus cancel() on the next run."""
        return "async_queue" if self._mode == "async_cancel" else self._mode

    @abstractmethod
    async def _create_stream(self) -> TTSStream:
        """Create and return a configured TTS stream.

        Subclasses implement this to create their provider-specific stream.

        Returns:
            Configured TTSStream instance (not yet entered).
        """
        raise NotImplementedError

    async def _ensure_stream(self) -> TTSStream:
        """Get or create the TTS stream."""
        if self._tts_stream is None:
            self._tts_stream = await self._create_stream()
            await self._tts_stream.__aenter__()
        return self._tts_stream

    async def _close_stream(self) -> None:
        """Close the TTS stream if open."""
        if self._tts_stream is not None:
            await self._tts_stream.__aexit__(None, None, None)
            if self._mode == "async_cancel":
                self._previous_stream = self._tts_stream
            self._tts_stream = None

    async def __call__(self, ctx: AgentContext[Any], event: RichAgentStreamEvent[Any]) -> None:
        """Handle stream events and trigger TTS synthesis."""
        from agentpool.agents.events import RunStartedEvent, StreamCompleteEvent

        match event:
            case RunStartedEvent():
                # async_cancel: a new run cuts off the previous run's audio
                if self._mode == "async_cancel":
                    for stream in (self._previous_stream, self._tts_stream):
                        if stream is not None:
                            await stream.cancel()
                    self._previous_stream = None

            case (
                PartStartEvent(part=TextPart(content=delta))
                | PartDeltaEvent(delta=TextPartDelta(content_delta=delta))
            ):
                stream = await self._ensure_stream()
                await stream.feed(delta)

            case StreamCompleteEvent():
                await self._close_stream()


class OpenAITTSEventHandler(BaseTTSEventHandler):
    """TTS event handler using OpenAI's Text-to-Speech API.

    Translates pydantic-ai stream events to anyvoice TTSStream.feed() calls
    using the OpenAI TTS provider.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: TTSModel = "tts-1",
        voice: TTSVoice = "alloy",
        speed: float = 1.0,
        chunk_size: int = 1024,
        sample_rate: int = 24000,
        min_text_length: int = 20,
        mode: TTSMode = "sync_run",
    ) -> None:
        super().__init__(
            sample_rate=sample_rate,
            min_text_length=min_text_length,
            mode=mode,
        )
        self._api_key = api_key
        self._model: TTSModel = model
        self._voice: TTSVoice = voice
        self._speed = speed
        self._chunk_size = chunk_size

    async def _create_stream(self) -> TTSStream:
        """Create OpenAI TTS stream."""
        from anyvoice import OpenAITTSProvider, SoundDeviceSink, TTSStream

        provider = OpenAITTSProvider(api_key=self._api_key)
        session = provider.session(
            model=self._model,
            voice=self._voice,
            speed=self._speed,
            chunk_size=self._chunk_size,
        )
        sink = SoundDeviceSink(sample_rate=self._sample_rate)
        return TTSStream(
            session,
            sink=sink,
            mode=self._stream_mode,
            min_text_length=self._min_text_length,
        )


class EdgeTTSEventHandler(BaseTTSEventHandler):
    """TTS event handler using Microsoft Edge's free TTS service.

    Translates pydantic-ai stream events to anyvoice TTSStream.feed() calls
    using the Edge TTS provider (no API key required).
    """

    def __init__(
        self,
        *,
        voice: str = "en-US-AriaNeural",
        rate: str = "+0%",
        volume: str = "+0%",
        pitch: str = "+0Hz",
        sample_rate: int = 24000,
        min_text_length: int = 20,
        mode: TTSMode = "sync_run",
    ) -> None:
        super().__init__(
            sample_rate=sample_rate,
            min_text_length=min_text_length,
            mode=mode,
        )
        self._voice = voice
        self._rate = rate
        self._volume = volume
        self._pitch = pitch

    async def _create_stream(self) -> TTSStream:
        """Create Edge TTS stream."""
        from anyvoice import EdgeTTSProvider, SoundDeviceSink, TTSStream

        provider = EdgeTTSProvider(sample_rate=self._sample_rate)
        session = provider.session(
            voice=self._voice,
            rate=self._rate,
            volume=self._volume,
            pitch=self._pitch,
        )
        sink = SoundDeviceSink(sample_rate=self._sample_rate)
        return TTSStream(
            session,
            sink=sink,
            mode=self._stream_mode,
            min_text_length=self._min_text_length,
        )
