"""Manages message flow between agents/groups."""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass, field
from functools import cached_property
from typing import TYPE_CHECKING, Any

from agentpool.utils.time_utils import get_now


if TYPE_CHECKING:
    from collections.abc import MutableSequence, Sequence
    from datetime import datetime

    from agentpool.messaging.messages import ChatMessage, FormatStyle
    from agentpool.tools import ToolCallInfo


@dataclass(frozen=True, kw_only=True)
class MessageStats:
    """Statistics for a single connection."""

    messages: MutableSequence[ChatMessage[Any]] = field(default_factory=list)
    start_time: datetime = field(default_factory=get_now)

    @property
    def message_count(self) -> int:
        """Number of messages transmitted."""
        return len(self.messages)

    @property
    def last_message_time(self) -> datetime | None:
        """When the last message was sent."""
        return self.messages[-1].timestamp if self.messages else None

    @property
    def token_count(self) -> int:
        """Total tokens used."""
        return sum(m.usage.total_tokens for m in self.messages)

    @property
    def tool_calls(self) -> list[ToolCallInfo]:
        """Accumulated tool calls going through this connection."""
        return [call for msg in self.messages for call in msg.get_tool_calls()]

    @property
    def byte_count(self) -> int:
        """Total bytes transmitted."""
        return sum(len(str(msg.content).encode()) for msg in self.messages)

    def count_from(self, name: str | None) -> int:
        """Number of messages sent by ``name``."""
        return sum(1 for m in self.messages if m.name == name)

    def format(self, style: FormatStyle = "simple", **kwargs: Any) -> str:
        """Format the conversation that happened on this connection."""
        return "\n".join(msg.format(style, **kwargs) for msg in self.messages)

    @property
    def total_cost(self) -> float:
        """Total cost in USD."""
        return sum(float(m.cost_info.total_cost) for m in self.messages if m.cost_info)


TALK_STATS_HISTORY = 100
"""Messages a connection's stats keep by default (the totals count all)."""


@dataclass
class StatsTotals:
    """Running totals over every message a connection carried."""

    messages: int = 0
    tokens: int = 0
    cost: float = 0.0
    bytes: int = 0
    senders: Counter[str | None] = field(default_factory=Counter)

    def add(self, message: ChatMessage[Any]) -> None:
        self.messages += 1
        self.tokens += message.usage.total_tokens
        if message.cost_info:
            self.cost += float(message.cost_info.total_cost)
        self.bytes += len(str(message.content).encode())
        self.senders[message.name] += 1


@dataclass(frozen=True, kw_only=True)
class TalkStats(MessageStats):
    """Statistics for a single connection.

    ``messages`` keeps the last ``history`` messages (None: all), so a long-lived
    connection doesn't hold every message it carried. The counts, tokens, cost and
    bytes are totals over all of them. Add messages with :meth:`record`.
    """

    source_name: str | None
    target_names: set[str]
    history: int | None = TALK_STATS_HISTORY
    totals: StatsTotals = field(default_factory=StatsTotals, init=False, compare=False)

    def __post_init__(self) -> None:
        for message in self.messages:
            self.totals.add(message)
        object.__setattr__(self, "messages", deque(self.messages, maxlen=self.history))

    def record(self, message: ChatMessage[Any]) -> None:
        """Add a message the connection carried."""
        self.messages.append(message)
        self.totals.add(message)

    @property
    def message_count(self) -> int:
        """Number of messages transmitted (all, not only the kept ones)."""
        return self.totals.messages

    @property
    def token_count(self) -> int:
        """Total tokens used."""
        return self.totals.tokens

    @property
    def total_cost(self) -> float:
        """Total cost in USD."""
        return self.totals.cost

    @property
    def byte_count(self) -> int:
        """Total bytes transmitted."""
        return self.totals.bytes

    def count_from(self, name: str | None) -> int:
        """Number of messages sent by ``name`` (all, not only the kept ones)."""
        return self.totals.senders[name]


@dataclass(kw_only=True)
class AggregatedMessageStats:
    """Statistics aggregated from multiple connections."""

    stats: Sequence[MessageStats | AggregatedMessageStats] = field(default_factory=list)

    # def __init__(self, stats: list[TalkStats | AggregatedTalkStats]):
    #     self.stats = stats

    @property
    def messages(self) -> list[ChatMessage[Any]]:
        """All messages across all connections, flattened."""
        return [msg for stat in self.stats for msg in stat.messages]

    @property
    def message_count(self) -> int:
        """Total messages across all connections."""
        return sum(stat.message_count for stat in self.stats)

    @property
    def tool_calls(self) -> list[ToolCallInfo]:
        """Accumulated tool calls going through this connection."""
        return [call for msg in self.messages for call in msg.get_tool_calls()]

    @property
    def start_time(self) -> datetime:
        """Total messages across all connections."""
        return self.stats[0].start_time

    @property
    def num_connections(self) -> int:
        """Number of active connections."""
        return len(self.stats)

    @property
    def token_count(self) -> int:
        """Total tokens across all connections."""
        return sum(stat.token_count for stat in self.stats)

    @property
    def total_cost(self) -> float:
        """Total cost in USD."""
        return sum(stat.total_cost for stat in self.stats)

    @property
    def byte_count(self) -> int:
        """Total bytes transmitted."""
        return sum(stat.byte_count for stat in self.stats)

    @property
    def last_message_time(self) -> datetime | None:
        """Most recent message time."""
        if not self.messages:
            return None
        return max(msg.timestamp for msg in self.messages)

    def format(self, style: FormatStyle = "simple", **kwargs: Any) -> str:
        """Format all conversations in the team."""
        return "\n".join(msg.format(style, **kwargs) for msg in self.messages)


@dataclass(kw_only=True)
class AggregatedTalkStats(AggregatedMessageStats):
    """Statistics aggregated from multiple connections."""

    stats: Sequence[TalkStats | AggregatedTalkStats] = field(default_factory=list)

    @cached_property
    def source_names(self) -> set[str]:
        """Set of unique source names recursively."""

        def _collect_source_names(stat: TalkStats | AggregatedTalkStats) -> set[str]:
            """Recursively collect source names."""
            if isinstance(stat, TalkStats):
                return {stat.source_name} if stat.source_name else set()
            # It's a AggregatedTalkStats, recurse
            names = set()
            for s in stat.stats:
                names.update(_collect_source_names(s))
            return names

        names = set()
        for stat in self.stats:
            names.update(_collect_source_names(stat))
        return names

    @cached_property
    def target_names(self) -> set[str]:
        """Set of all target names across connections."""
        return {name for s in self.stats for name in s.target_names}
