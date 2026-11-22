"""Turn the items of a T3 Code run into agentpool stream events.

A T3 run shows up as thread items (``t3_thread_read`` with the ``activity`` view):
the user's message, the agent's messages and reasoning, tool work (commands, file
changes, MCP and dynamic tool calls, web searches, ...), notices and checkpoints.
Items are updated in place while they run: their status changes and their text
grows. ``/mcp`` shows an assistant message only once complete; the native socket
(``live.py``) shows it growing. ``ItemTracker`` takes items from both (they share
item ids), compares each with what it reported before and yields the events for
what changed.

Upstream (https://github.com/pingdotgg/t3code): item kinds in
``packages/contracts/src/providerRuntime.ts`` and ``orchestrationV2.ts``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import BaseModel, Field, ValidationError
from pydantic_ai import PartDeltaEvent, PartStartEvent, TextPart, TextPartDelta, ThinkingPart
from pydantic_ai.messages import ThinkingPartDelta

from agentpool.agents.events import (
    NoticeEvent,
    ToolCallCompleteEvent,
    ToolCallProgressEvent,
    ToolCallStartEvent,
)


if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from agentpool.agents.events import RichAgentStreamEvent
    from agentpool.tools.base import ToolKind


FINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "interrupted", "rolled_back"})
"""Item statuses that do not change anymore."""

_IGNORED_TYPES = frozenset({
    "user_message",
    "checkpoint",
    "approval_request",
    "user_input_request",
})
"""The prompt itself, T3's own workspace snapshots, and requests: an approval or a
question reaches the user through the input provider (or a notice) instead."""

_TOOL_KINDS: dict[str, ToolKind] = {
    "command_execution": "execute",
    "file_change": "edit",
    "web_search": "search",
    "image_view": "read",
    "collab_agent_tool_call": "think",
}
"""ACP tool kinds of T3's tool-like items; other items are ``other``."""


class Item(Protocol):
    """A thread item: ``/mcp``'s ``Item`` or the native socket's ``TurnItem``."""

    @property
    def item_id(self) -> str: ...
    @property
    def type(self) -> str: ...
    @property
    def status(self) -> str: ...
    @property
    def title(self) -> str | None: ...
    @property
    def text(self) -> str | None: ...


@dataclass
class _Seen:
    """What was reported for an item so far."""

    status: str
    text: str
    part_index: int | None = None
    """Index of the text or thinking part, for message and reasoning items."""


@dataclass
class ItemTracker:
    """Events for the changes between successive reads of a run's items.

    Args:
        agent_name: Name of the agent, for the tool call events
        message_id: Message id of the agent's response, for the tool call events
    """

    agent_name: str
    message_id: str
    _seen: dict[str, _Seen] = field(default_factory=dict)
    _next_part: int = 0
    _replies: dict[str, str] = field(default_factory=dict)
    """Text of each assistant message by item id, in order of appearance."""

    @property
    def reply(self) -> str:
        """The agent's reply so far: its assistant messages, in order."""
        return "\n\n".join(text for text in self._replies.values() if text)

    @property
    def pending(self) -> bool:
        """Whether an item the tracker reported has not reached a final status."""
        return any(seen.status not in FINAL_STATUSES for seen in self._seen.values())

    def update(self, items: Sequence[Item]) -> Iterator[RichAgentStreamEvent[Any]]:
        """Yield the events for what changed since the last read."""
        for item in items:
            if item.type in _IGNORED_TYPES:
                continue
            match item.type:
                case "assistant_message":
                    yield from self._text(item, thinking=False)
                case "reasoning":
                    yield from self._text(item, thinking=True)
                case "system_notice":
                    yield from self._notice(item)
                case _:
                    yield from self._tool(item)

    def _text(self, item: Item, *, thinking: bool) -> Iterator[RichAgentStreamEvent[Any]]:
        text = item.text or ""
        seen = self._seen.get(item.item_id)
        if seen is None:
            if not thinking:
                self._replies[item.item_id] = text
            index = self._next_part
            self._next_part += 1
            self._seen[item.item_id] = _Seen(status=item.status, text=text, part_index=index)
            if text:
                part = ThinkingPart(content=text) if thinking else TextPart(content=text)
                yield PartStartEvent(index=index, part=part)
            return
        seen.status = item.status
        if seen.text.startswith(text):
            return  # Nothing new: the same text, or an older or cut-off copy of it
        if not thinking:
            self._replies[item.item_id] = text
        if text.startswith(seen.text):
            delta = text[len(seen.text) :]
            assert seen.part_index is not None
            if not seen.text:  # The part had no text yet: start it now
                part = ThinkingPart(content=text) if thinking else TextPart(content=text)
                yield PartStartEvent(index=seen.part_index, part=part)
            elif thinking:
                yield PartDeltaEvent(
                    index=seen.part_index, delta=ThinkingPartDelta(content_delta=delta)
                )
            else:
                yield PartDeltaEvent(
                    index=seen.part_index, delta=TextPartDelta(content_delta=delta)
                )
        seen.text = text

    def _notice(self, item: Item) -> Iterator[RichAgentStreamEvent[Any]]:
        if item.item_id in self._seen:
            return
        self._seen[item.item_id] = _Seen(status=item.status, text=item.text or "")
        severity = "error" if item.status == "failed" else "info"
        yield NoticeEvent(title=item.text or item.title or "Notice", severity=severity)

    def _tool(self, item: Item) -> Iterator[RichAgentStreamEvent[Any]]:
        text = item.text or ""
        seen = self._seen.get(item.item_id)
        tool_input = _tool_input(item)
        if seen is None:
            # Recorded as running: a still-running item needs no progress on top of its start
            self._seen[item.item_id] = seen = _Seen(status="running", text=text)
            yield ToolCallStartEvent(
                tool_call_id=item.item_id,
                tool_name=item.type,
                title=_tool_title(item),
                kind=_TOOL_KINDS.get(item.type, "other"),
                raw_input=tool_input,
            )
        if item.status in FINAL_STATUSES:
            if seen.status in FINAL_STATUSES:
                return
            seen.status, seen.text = item.status, text
            failed = item.status != "completed"
            yield ToolCallCompleteEvent(
                tool_name=item.type,
                tool_call_id=item.item_id,
                tool_input=tool_input,
                tool_result=text,
                agent_name=self.agent_name,
                message_id=self.message_id,
                error=f"{item.type} {item.status}" if failed else None,
            )
            return
        if text != seen.text or item.status != seen.status:
            seen.status, seen.text = item.status, text
            yield ToolCallProgressEvent(
                tool_call_id=item.item_id,
                tool_name=item.type,
                title=_tool_title(item),
                tool_input=tool_input,
            )


def _command(item: Item) -> str | None:
    """The command of a ``command_execution`` item: T3 writes it as ``$ <command>``."""
    first = (item.text or "").split("\n", 1)[0]
    return first.removeprefix("$ ") if first.startswith("$ ") else None


class _DynamicCall(BaseModel):
    """Text of a ``dynamic_tool`` item: the provider tool's name and input, as JSON."""

    tool_name: str = Field(alias="toolName")
    input: dict[str, Any] = Field(default_factory=dict)


def _dynamic_call(item: Item) -> _DynamicCall | None:
    """The tool call of a ``dynamic_tool`` item; None until its text is complete JSON."""
    if item.type != "dynamic_tool" or not (item.text or "").startswith("{"):
        return None
    try:
        return _DynamicCall.model_validate_json(item.text or "")
    except ValidationError:
        return None


def _tool_input(item: Item) -> dict[str, Any]:
    if item.type == "command_execution" and (command := _command(item)):
        return {"command": command}
    if call := _dynamic_call(item):
        return call.input
    return {}


def _tool_title(item: Item) -> str:
    if item.title:
        return item.title
    if item.type == "command_execution" and (command := _command(item)):
        return f"`{command}`"
    if call := _dynamic_call(item):
        return call.tool_name
    return item.type.replace("_", " ").capitalize()
