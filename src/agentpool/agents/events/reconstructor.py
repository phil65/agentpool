"""Reconstruct pydantic-ai ModelRequest/ModelResponse sequences from event streams.

This module provides a `MessageReconstructor` that observes `RichAgentStreamEvent`s
and builds the `list[ModelMessage]` sequence that a native pydantic-ai agent would
have produced. This eliminates per-agent duplication of response_parts / text_chunks /
model_messages tracking in ACP, Claude Code, Codex, and AG-UI agents.

Usage::

    reconstructor = MessageReconstructor(initial_prompts=prompts)

    async for event in raw_stream:
        reconstructor.observe(event)
        yield event

    # After stream ends:
    reconstructor.flush()
    messages = reconstructor.model_messages
    text = reconstructor.text_content
"""

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from pydantic_ai import (
    ModelRequest,
    ModelResponse,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
    ToolCallPart,
    ToolCallPartDelta,
    ToolReturnPart,
    UserPromptPart,
)

from agentpool.agents.events.events import ToolCallCompleteEvent, ToolCallStartEvent


if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic_ai import FinishReason, ModelMessage, ModelResponsePart, UserContent

    from agentpool.agents.events.events import RichAgentStreamEvent


@dataclass
class MessageReconstructor:
    """Reconstructs pydantic-ai ModelRequest/ModelResponse sequences from an event stream.

    Observes ``RichAgentStreamEvent`` instances and builds the ``ModelMessage`` list
    that a native pydantic-ai agent run would have produced.  Call :meth:`observe` for
    every event in the stream, then :meth:`flush` once the stream is complete.

    Attributes:
        model_messages: The accumulated message sequence.
        text_content: All assistant text concatenated (convenience for ``ChatMessage.content``).
        current_response_parts: Parts being accumulated for the current ``ModelResponse``.
    """

    model_messages: list[ModelMessage] = field(default_factory=list)
    """Accumulated ModelRequest / ModelResponse sequence."""

    current_response_parts: list[ModelResponsePart] = field(default_factory=list)
    """Parts of the in-progress ModelResponse (flushed on tool result or end of stream)."""

    all_response_parts: list[ModelResponsePart] = field(default_factory=list)
    """All response parts accumulated across the entire stream (never cleared by flush)."""

    _text_chunks: list[str] = field(default_factory=list)
    """Raw text deltas for rebuilding ``text_content``."""

    _thinking_chunks: list[str] = field(default_factory=list)
    """Raw thinking deltas accumulated for the current thinking part."""

    _model_name: str | None = field(default=None)
    """Model name to attach to ModelResponse objects."""

    _provider_name: str | None = field(default=None)
    """Provider name to attach to ModelResponse objects."""

    def __init__(
        self,
        *,
        initial_prompts: list[UserContent] | None = None,
        model_name: str | None = None,
        provider_name: str | None = None,
    ) -> None:
        """Create a new reconstructor.

        Args:
            initial_prompts: If provided, a ``ModelRequest`` with a
                ``UserPromptPart`` is prepended to ``model_messages``.
            model_name: Model name for ``ModelResponse`` objects.
            provider_name: Provider name for ``ModelResponse`` objects.
        """
        self.model_messages = []
        self.current_response_parts = []
        self.all_response_parts = []
        self._text_chunks = []
        self._thinking_chunks = []
        self._model_name = model_name
        self._provider_name = provider_name

        if initial_prompts is not None:
            initial_request = ModelRequest(parts=[UserPromptPart(content=initial_prompts)])
            self.model_messages.append(initial_request)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def text_content(self) -> str:
        """All accumulated assistant text joined together."""
        return "".join(self._text_chunks)

    def observe(self, event: RichAgentStreamEvent[Any]) -> None:
        """Observe a single stream event and update internal state.

        This should be called for every event yielded by the agent stream.
        Events that are not relevant to message reconstruction are silently ignored.
        """
        match event:
            # --- Part starts (full parts arriving at once) ---
            case PartStartEvent(part=TextPart(content=text)):
                self.current_response_parts.append(TextPart(content=text))
                self._text_chunks.append(text)

            case PartStartEvent(part=ThinkingPart() as part):
                self.current_response_parts.append(part)

            case PartStartEvent(part=ToolCallPart() as part):
                self._upsert_tool_call(part)

            # --- Deltas (streaming increments) ---
            case PartDeltaEvent(delta=TextPartDelta(content_delta=delta)):
                self._text_chunks.append(delta)
                # Merge into last TextPart or create a new one
                self._merge_text_delta(delta)

            case PartDeltaEvent(delta=ThinkingPartDelta(content_delta=delta)) if delta:
                self._merge_thinking_delta(delta)

            case PartDeltaEvent(delta=ToolCallPartDelta(args_delta=args, tool_call_id=tc_id)):
                self._merge_tool_call_delta(args, tc_id)

            # --- Tool call start (from external agents) ---
            # Repeated starts for the same call update it (see ToolCallStartEvent contract).
            case ToolCallStartEvent(tool_call_id=tc_id, tool_name=name, raw_input=raw_input):
                self._upsert_tool_call(
                    ToolCallPart(tool_name=name, args=raw_input, tool_call_id=tc_id)
                )

            # --- Tool call complete → flush response, add return ---
            case ToolCallCompleteEvent(tool_name=name, tool_call_id=tc_id, tool_result=result):
                self._flush_response()
                content = result if result is not None else ""
                return_part = ToolReturnPart(tool_name=name, content=content, tool_call_id=tc_id)
                self.model_messages.append(ModelRequest(parts=[return_part]))

            case _:
                pass  # Ignore events not relevant to message reconstruction

    def add_user_prompt(self, content: str | Sequence[UserContent]) -> None:
        """Add a user message that arrived during the run (e.g. one steered into it)."""
        self._flush_response()
        self.model_messages.append(ModelRequest(parts=[UserPromptPart(content=content)]))

    def _upsert_tool_call(self, part: ToolCallPart) -> None:
        """Add a tool call part, or update the pending part with the same call ID."""
        for index, existing in enumerate(self.current_response_parts):
            if isinstance(existing, ToolCallPart) and existing.tool_call_id == part.tool_call_id:
                args = part.args if part.args else existing.args
                self.current_response_parts[index] = ToolCallPart(
                    tool_name=part.tool_name or existing.tool_name,
                    args=args,
                    tool_call_id=part.tool_call_id,
                )
                return
        self.current_response_parts.append(part)

    def flush(self, *, finish_reason: FinishReason | None = None) -> list[ModelMessage]:
        """Flush remaining response parts into a final ``ModelResponse``.

        Should be called once after the stream ends.  Returns the complete
        ``model_messages`` list for convenience.

        Args:
            finish_reason: Optional finish reason for the final ``ModelResponse``.

        Returns:
            The complete list of ``ModelMessage`` objects.
        """
        self._flush_response(finish_reason=finish_reason)
        return self.model_messages

    def reset(self) -> None:
        """Reset all state for reuse."""
        self.model_messages.clear()
        self.current_response_parts.clear()
        self.all_response_parts.clear()
        self._text_chunks.clear()
        self._thinking_chunks.clear()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _flush_response(self, *, finish_reason: FinishReason | None = None) -> None:
        """Flush ``current_response_parts`` into a ``ModelResponse`` if non-empty."""
        if not self.current_response_parts:
            return
        parts = list(self.current_response_parts)
        response = ModelResponse(
            parts=parts,
            model_name=self._model_name,
            provider_name=self._provider_name,
            finish_reason=finish_reason,
        )
        self.model_messages.append(response)
        self.all_response_parts.extend(parts)
        self.current_response_parts.clear()

    def _merge_text_delta(self, delta: str) -> None:
        """Merge a text delta into the last TextPart, or create a new one."""
        for i in range(len(self.current_response_parts) - 1, -1, -1):
            part = self.current_response_parts[i]
            if isinstance(part, TextPart):
                self.current_response_parts[i] = TextPart(
                    content=part.content + delta,
                    id=part.id,
                    provider_name=part.provider_name,
                    provider_details=part.provider_details,
                )
                return
        # No existing TextPart — create one
        self.current_response_parts.append(TextPart(content=delta))

    def _merge_thinking_delta(self, delta: str) -> None:
        """Merge a thinking delta into the last ThinkingPart, or create a new one."""
        for i in range(len(self.current_response_parts) - 1, -1, -1):
            part = self.current_response_parts[i]
            if isinstance(part, ThinkingPart):
                self.current_response_parts[i] = ThinkingPart(
                    content=part.content + delta,
                    id=part.id,
                    signature=part.signature,
                    provider_name=part.provider_name,
                    provider_details=part.provider_details,
                )
                return
        self.current_response_parts.append(ThinkingPart(content=delta))

    def _merge_tool_call_delta(
        self, args: str | dict[str, Any] | None, tool_call_id: str | None
    ) -> None:
        """Merge a tool call args delta into the matching ToolCallPart (else the last one)."""
        if args is None:
            return
        parts = self.current_response_parts
        calls = [i for i, part in enumerate(parts) if isinstance(part, ToolCallPart)]
        matching = [i for i in calls if getattr(parts[i], "tool_call_id", None) == tool_call_id]
        if not (targets := matching or calls):
            return
        part = parts[targets[-1]]
        assert isinstance(part, ToolCallPart)
        parts[targets[-1]] = replace(part, args=_merge_args(part.args, args))


def _merge_args(
    current: str | dict[str, Any] | None, delta: str | dict[str, Any]
) -> str | dict[str, Any] | None:
    """Tool call args with ``delta`` applied: JSON text appends, dicts merge.

    A start without args (``None``, ``""``, ``{}``) takes the delta as it is; text and a
    dict can't be merged, so the args stay as they are.
    """
    if not current:  # None, "" or {}
        return delta
    match current, delta:
        case str(), str():
            return current + delta
        case dict(), dict():
            return {**current, **delta}
        case _:
            return current
