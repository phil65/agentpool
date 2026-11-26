"""Stream processors for event pipelines.

This module provides composable processors that can transform, filter, or observe
event streams. Processors wrap AsyncIterators and can be chained together.

Example:
    ```python
    # Simple function processor
    async def log_events(stream):
        async for event in stream:
            print(f"Event: {type(event).__name__}")
            yield event

    # Compose into pipeline
    pipeline = StreamPipeline([log_events])

    async for event in pipeline(raw_events):
        yield event
    ```
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

from pydantic_ai import (
    PartDeltaEvent,
    TextPartDelta,
    ThinkingPartDelta,
    ToolCallPartDelta,
)


if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Coroutine

    from agentpool.agents.events.events import RichAgentStreamEvent


# Type alias for processor callables
type StreamProcessorCallable = Callable[
    [AsyncIterator[RichAgentStreamEvent[Any]]], AsyncIterator[RichAgentStreamEvent[Any]]
]
# Delta type identifiers for batching
type _DeltaType = Literal["text", "thinking", "tool_call"] | None


@runtime_checkable
class StreamProcessor(Protocol):
    """Protocol for stream processors.

    Processors can be:
    - Callables: `(AsyncIterator[RichAgentStreamEvent]) -> AsyncIterator[RichAgentStreamEvent]`
    - Classes with `__call__`: Same signature, but can hold state
    """

    def __call__(
        self, stream: AsyncIterator[RichAgentStreamEvent[Any]]
    ) -> AsyncIterator[RichAgentStreamEvent[Any]]:
        """Process an event stream.

        Args:
            stream: Input event stream

        Returns:
            Transformed/filtered event stream
        """
        ...


@dataclass
class StreamPipeline:
    """Composable pipeline for processing event streams.

    Chains multiple processors together, passing the output of each
    to the input of the next.

    Example:
        ```python
        tracker = FileTrackingProcessor()
        pipeline = StreamPipeline([
            tracker,
            event_handler_processor(handler),
        ])

        async for event in pipeline(raw_events):
            yield event

        # Access state directly from processor instances
        print(tracker.get_metadata())
        ```
    """

    processors: list[StreamProcessorCallable | StreamProcessor] = field(default_factory=list)

    def __call__(
        self, stream: AsyncIterator[RichAgentStreamEvent[Any]]
    ) -> AsyncIterator[RichAgentStreamEvent[Any]]:
        """Run events through all processors in sequence.

        Args:
            stream: Input event stream

        Returns:
            Processed event stream
        """
        result = stream
        for processor in self.processors:
            result = processor(result)
        return result

    def add(self, processor: StreamProcessorCallable | StreamProcessor) -> None:
        """Add a processor to the pipeline.

        Args:
            processor: Processor to add
        """
        self.processors.append(processor)


def event_handler_processor(
    handler: Callable[[Any, RichAgentStreamEvent[Any]], Coroutine[Any, Any, None]],
) -> StreamProcessorCallable:
    """Create a processor that calls an event handler for each event.

    The handler is called with (None, event) to match the existing
    MultiEventHandler signature.

    Args:
        handler: Async callable with signature (ctx, event) -> None

    Returns:
        Processor function that calls the handler

    Example:
        ```python
        pipeline = StreamPipeline([
            event_handler_processor(self.event_handler),
        ])
        ```
    """

    async def process(
        stream: AsyncIterator[RichAgentStreamEvent[Any]],
    ) -> AsyncIterator[RichAgentStreamEvent[Any]]:
        async for event in stream:
            await handler(None, event)
            yield event

    return process


async def batch_stream_deltas(  # noqa: PLR0915
    stream: AsyncIterator[RichAgentStreamEvent[Any]],
) -> AsyncIterator[RichAgentStreamEvent[Any]]:
    """Batch consecutive delta events, yielding when event type changes.

    This reduces UI update frequency by accumulating consecutive deltas of the same
    type and yielding them as a single event when the type changes.

    Batches:
    - TextPartDelta events (consecutive deltas combined into one)
    - ThinkingPartDelta events (consecutive deltas combined into one)
    - ToolCallPartDelta events (consecutive deltas combined into one)

    All other events pass through immediately and flush any pending batch.
    PartStartEvents pass through unchanged.

    Args:
        stream: Async iterator of stream events from agent.run_stream()

    Yields:
        Stream events with consecutive deltas batched together
    """
    pending_content: list[str] = []
    pending_type: _DeltaType = None
    pending_index: int = 0  # For PartDeltaEvent.index

    def _make_batched_event() -> PartDeltaEvent:
        """Create a synthetic PartDeltaEvent from accumulated content."""
        content = "".join(pending_content)
        delta: TextPartDelta | ThinkingPartDelta | ToolCallPartDelta
        match pending_type:
            case "text":
                delta = TextPartDelta(content_delta=content)
            case "thinking":
                delta = ThinkingPartDelta(content_delta=content)
            case "tool_call":
                delta = ToolCallPartDelta(args_delta=content)
            case _:
                raise ValueError(f"Unexpected pending type: {pending_type}")
        return PartDeltaEvent(index=pending_index, delta=delta)

    async for event in stream:
        match event:
            case PartDeltaEvent(delta=TextPartDelta(content_delta=content), index=idx):
                if pending_type == "text":
                    pending_content.append(content)
                else:
                    if pending_type is not None and pending_content:
                        yield _make_batched_event()
                    pending_content = [content]
                    pending_type = "text"
                    pending_index = idx

            case PartDeltaEvent(delta=ThinkingPartDelta(content_delta=content), index=idx):
                if content is None:
                    continue
                if pending_type == "thinking":
                    pending_content.append(content)
                else:
                    if pending_type is not None and pending_content:
                        yield _make_batched_event()
                    pending_content = [content]
                    pending_type = "thinking"
                    pending_index = idx

            case PartDeltaEvent(delta=ToolCallPartDelta(args_delta=args), index=idx) if isinstance(
                args, str
            ):
                if pending_type == "tool_call":
                    pending_content.append(args)
                else:
                    if pending_type is not None and pending_content:
                        yield _make_batched_event()
                    pending_content = [args]
                    pending_type = "tool_call"
                    pending_index = idx

            case _:
                # Any other event: flush pending batch and pass through
                if pending_type is not None and pending_content:
                    yield _make_batched_event()
                    pending_content = []
                    pending_type = None
                yield event

    # Flush any remaining batch at end of stream
    if pending_type is not None and pending_content:
        yield _make_batched_event()
