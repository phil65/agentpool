"""Manage model turn state: emit events and record ModelMessage history.

A TurnManager is the single point of contact for stream adapters. Instead of
manually constructing events *and* separately feeding a reconstructor, the
adapter calls methods like ``text_delta()`` or ``tool_call_start()`` and gets
back the events to yield. Internally the manager records the same information
into a ``list[ModelMessage]`` sequence identical to what a native pydantic-ai
agent run would produce.

Part lifecycle (start/end events, index tracking) is handled implicitly.
Calling ``text_delta`` when a thinking part is active will auto-close the
thinking part, emit a ``PartEndEvent``, start a new text part, and emit
a ``PartStartEvent`` — all transparently.

Usage::

    tm = TurnManager(provider_name="anthropic")
    tm.add_user_prompt("Hello")

    # In the adapter's stream loop — just yield from:
    yield from tm.text_delta("Hi there")
    yield from tm.tool_call_start(name="search", tool_call_id="tc_1")
    yield from tm.tool_call_delta(tool_call_id="tc_1", args_delta='{"q":')
    # ... tool executes ...
    yield from tm.tool_call_complete(name="search", tool_call_id="tc_1", result="42")

    messages = list(tm.finish())
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Literal

from pydantic_ai import (
    FunctionToolResultEvent,
    ModelRequest,
    ModelResponse,
    NativeToolCallPart,
    PartEndEvent,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai._utils import generate_tool_call_id as _generate_tool_call_id

from agentpool.agents.events.events import PartDeltaEvent, PartStartEvent


if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from pydantic_ai import FinishReason, ModelMessage, ModelResponsePart, UserContent


type _ActiveKind = Literal["text", "thinking", "tool"]

type TurnEvent = PartStartEvent | PartDeltaEvent | PartEndEvent | FunctionToolResultEvent


@dataclass
class TurnManager:
    """Emit stream events and record pydantic-ai ModelMessage history in one place.

    Each method yields the events the adapter should propagate and
    simultaneously updates the internal message list.  Part lifecycle
    (start / end / index tracking) is fully implicit.
    """

    provider_name: str
    model_name: str | None = None

    _messages: list[ModelMessage] = field(default_factory=list, init=False)
    _current_parts: list[ModelResponsePart] = field(default_factory=list, init=False)
    _all_parts: list[ModelResponsePart] = field(default_factory=list, init=False)
    _text_chunks: list[str] = field(default_factory=list, init=False)

    _part_index: int = field(default=0, init=False)
    _active_kind: _ActiveKind | None = field(default=None, init=False)

    # ------------------------------------------------------------------
    # User prompt
    # ------------------------------------------------------------------

    def add_user_prompt(self, content: str | Sequence[UserContent]) -> None:
        """Append a ``ModelRequest`` with a ``UserPromptPart``."""
        self._messages.append(ModelRequest(parts=[UserPromptPart(content=content)]))

    # ------------------------------------------------------------------
    # Text
    # ------------------------------------------------------------------

    def text_delta(self, content: str) -> Iterator[TurnEvent]:
        """Append text content, auto-starting a text part if needed."""
        yield from self._ensure_active("text")
        self._text_chunks.append(content)
        self._merge_text_delta(content)
        yield PartDeltaEvent.text(index=self._part_index, content=content)

    # ------------------------------------------------------------------
    # Thinking
    # ------------------------------------------------------------------

    def thinking_delta(self, content: str) -> Iterator[TurnEvent]:
        """Append thinking content, auto-starting a thinking part if needed."""
        yield from self._ensure_active("thinking")
        self._merge_thinking_delta(content)
        yield PartDeltaEvent.thinking(index=self._part_index, content=content)

    # ------------------------------------------------------------------
    # Tool calls
    # ------------------------------------------------------------------

    def tool_call_start(
        self,
        *,
        name: str,
        tool_call_id: str | None = None,
        args: str | dict[str, Any] | None = None,
        kind: Literal["default", "builtin"] = "default",
    ) -> Iterator[TurnEvent]:
        """Begin a new tool call part, closing any open text/thinking part.

        Args:
            name: Tool name.
            tool_call_id: Unique ID for the tool call.  Auto-generated if omitted.
            args: Initial arguments (empty dict/string if not yet known).
            kind: ``"default"`` for regular tools, ``"builtin"`` for pydantic-ai builtins.
        """
        yield from self._close_active()
        tc_id = tool_call_id or _generate_tool_call_id()
        if kind == "builtin":
            part: NativeToolCallPart | ToolCallPart = NativeToolCallPart(
                tool_name=name,
                args=args,
                tool_call_id=tc_id,
            )
        else:
            part = ToolCallPart(tool_name=name, args=args, tool_call_id=tc_id)
        self._current_parts.append(part)
        self._active_kind = "tool"
        yield PartStartEvent(index=self._part_index, part=part)

    def tool_call_delta(
        self,
        *,
        tool_call_id: str,
        args_delta: str,
    ) -> Iterator[TurnEvent]:
        """Append an args delta to a streaming tool call."""
        self._merge_tool_call_delta(args_delta, tool_call_id)
        yield PartDeltaEvent.tool_call(
            index=self._part_index, content=args_delta, tool_call_id=tool_call_id
        )

    def tool_call_complete(
        self,
        *,
        name: str,
        tool_call_id: str,
        result: Any,
    ) -> Iterator[TurnEvent]:
        """Signal that a tool call finished: flush current response, record result.

        Closes any active part, flushes the in-progress ``ModelResponse``,
        then appends a ``ModelRequest`` with a ``ToolReturnPart``.
        """
        yield from self._close_active()
        self._flush_response()
        return_part = ToolReturnPart(
            tool_name=name,
            content=result if result is not None else "",
            tool_call_id=tool_call_id,
        )
        self._messages.append(ModelRequest(parts=[return_part]))
        yield FunctionToolResultEvent(return_part)

    def tool_call_retry(
        self,
        *,
        name: str,
        tool_call_id: str,
        content: str,
    ) -> Iterator[TurnEvent]:
        """Signal that a tool call failed and should be retried.

        Closes any active part, flushes the in-progress ``ModelResponse``,
        then appends a ``ModelRequest`` with a ``ToolReturnPart`` whose
        ``outcome`` is ``"retried"``. Downstream consumers can distinguish retries
        from successes by matching on
        ``FunctionToolResultEvent(part=ToolReturnPart(outcome="retried"))``.
        """
        yield from self._close_active()
        self._flush_response()
        retry_part = ToolReturnPart(
            tool_name=name,
            content=content,
            tool_call_id=tool_call_id,
            outcome="retried",
        )
        self._messages.append(ModelRequest(parts=[retry_part]))
        yield FunctionToolResultEvent(retry_part)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def finish(self, *, finish_reason: FinishReason | None = None) -> Iterator[TurnEvent]:
        """Close active part, flush remaining parts, yield final events.

        The complete message sequence is available via :attr:`model_messages`
        after iteration.
        """
        yield from self._close_active()
        self._flush_response(finish_reason=finish_reason)

    def reset(self) -> None:
        """Clear all state for reuse."""
        self._messages.clear()
        self._current_parts.clear()
        self._all_parts.clear()
        self._text_chunks.clear()
        self._part_index = 0
        self._active_kind = None

    # ------------------------------------------------------------------
    # Read state
    # ------------------------------------------------------------------

    @property
    def text_content(self) -> str:
        """All accumulated assistant text."""
        return "".join(self._text_chunks)

    @property
    def model_messages(self) -> list[ModelMessage]:
        """Current message sequence (may still have unflushed parts)."""
        return self._messages

    @property
    def all_response_parts(self) -> list[ModelResponsePart]:
        """All response parts accumulated across the entire stream."""
        return self._all_parts

    @property
    def current_response_parts(self) -> list[ModelResponsePart]:
        """Parts of the in-progress ``ModelResponse``."""
        return self._current_parts

    # ------------------------------------------------------------------
    # Part lifecycle (implicit)
    # ------------------------------------------------------------------

    def _ensure_active(self, kind: _ActiveKind) -> Iterator[TurnEvent]:
        """Ensure a part of ``kind`` is active, closing/opening as needed."""
        if self._active_kind == kind:
            return
        yield from self._close_active()
        self._active_kind = kind
        if kind == "text":
            text_part = TextPart(content="")
            self._current_parts.append(text_part)
            yield PartStartEvent(index=self._part_index, part=text_part)
        elif kind == "thinking":
            thinking_part = ThinkingPart(content="")
            self._current_parts.append(thinking_part)
            yield PartStartEvent(index=self._part_index, part=thinking_part)

    def _close_active(self) -> Iterator[TurnEvent]:
        """Close the currently active part, if any."""
        if self._active_kind is None:
            return
        match self._active_kind:
            case "text":
                yield PartEndEvent(index=self._part_index, part=TextPart(content=""))
            case "thinking":
                yield PartEndEvent(index=self._part_index, part=ThinkingPart(content=""))
            case "tool":
                yield PartEndEvent(index=self._part_index, part=TextPart(content=""))
        self._part_index += 1
        self._active_kind = None

    # ------------------------------------------------------------------
    # Message recording helpers
    # ------------------------------------------------------------------

    def _flush_response(self, *, finish_reason: FinishReason | None = None) -> None:
        if not self._current_parts:
            return
        parts = list(self._current_parts)
        self._messages.append(
            ModelResponse(
                parts=parts,
                model_name=self.model_name,
                provider_name=self.provider_name,
                finish_reason=finish_reason,
            )
        )
        self._all_parts.extend(parts)
        self._current_parts.clear()

    def _merge_text_delta(self, delta: str) -> None:
        for i in range(len(self._current_parts) - 1, -1, -1):
            part = self._current_parts[i]
            if isinstance(part, TextPart):
                self._current_parts[i] = TextPart(
                    content=part.content + delta,
                    id=part.id,
                    provider_name=part.provider_name,
                    provider_details=part.provider_details,
                )
                return
        self._current_parts.append(TextPart(content=delta))

    def _merge_thinking_delta(self, delta: str) -> None:
        for i in range(len(self._current_parts) - 1, -1, -1):
            part = self._current_parts[i]
            if isinstance(part, ThinkingPart):
                self._current_parts[i] = ThinkingPart(
                    content=part.content + delta,
                    id=part.id,
                    signature=part.signature,
                    provider_name=part.provider_name,
                    provider_details=part.provider_details,
                )
                return
        self._current_parts.append(ThinkingPart(content=delta))

    def _merge_tool_call_delta(self, args_delta: str, tool_call_id: str) -> None:
        for i in range(len(self._current_parts) - 1, -1, -1):
            part = self._current_parts[i]
            if (
                isinstance(part, (ToolCallPart, NativeToolCallPart))
                and part.tool_call_id == tool_call_id
            ):
                if isinstance(part.args, str):
                    self._current_parts[i] = replace(part, args=part.args + args_delta)
                return
        # Fallback: update last tool call part regardless of ID
        for i in range(len(self._current_parts) - 1, -1, -1):
            part = self._current_parts[i]
            if isinstance(part, (ToolCallPart, NativeToolCallPart)):
                if isinstance(part.args, str):
                    self._current_parts[i] = replace(part, args=part.args + args_delta)
                return
