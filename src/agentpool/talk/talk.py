"""Manages message flow between agents/groups."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Self, overload

from anyenv.signals import Signal

from agentpool.log import get_logger
from agentpool.messaging import ChatMessage
from agentpool.talk.stats import AggregatedTalkStats, TalkStats
from agentpool.utils.inspection import execute
from agentpool.utils.time_utils import get_now


if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Iterator
    from datetime import datetime, timedelta

    from evented.event_data import EventData

    from agentpool.common_types import (
        AnyFilterFn,
        AnyTransformFn,
        ProcessorCallback,
        PromptCompatible,
        QueueStrategy,
    )
    from agentpool.messaging import MessageNode
    from agentpool.messaging.events import ConnectionEventData
    from agentpool_config.events import ConnectionEventType
    from agentpool_config.forward_targets import ConnectionType

logger = get_logger(__name__)


class Talk[TTransmittedData = Any]:
    """Manages message flow between agents/groups."""

    @dataclass(frozen=True)
    class ConnectionProcessed:
        """Event emitted when a message flows through a connection."""

        message: ChatMessage[Any]
        source: MessageNode[Any, Any]
        targets: list[MessageNode[Any, Any]]
        queued: bool
        connection_type: ConnectionType
        timestamp: datetime = field(default_factory=get_now)

    # Original message "coming in"
    message_received = Signal[ChatMessage[Any]]()
    # After any transformation (one for each message, not per target)
    message_forwarded = Signal[ChatMessage[Any]]()
    # Comprehensive signal capturing all information about one "message handling process"
    connection_processed = Signal[ConnectionProcessed]()

    def __init__(
        self,
        source: MessageNode[Any, Any],
        targets: Sequence[MessageNode[Any, Any]],
        group: TeamTalk | None = None,
        *,
        name: str | None = None,
        connection_type: ConnectionType = "run",
        wait_for_connections: bool = False,
        priority: int = 0,
        delay: timedelta | None = None,
        queued: bool = False,
        queue_strategy: QueueStrategy = "latest",
        transform: AnyTransformFn[ChatMessage[TTransmittedData]] | None = None,
        filter_condition: AnyFilterFn | None = None,
        stop_condition: AnyFilterFn | None = None,
        exit_condition: AnyFilterFn | None = None,
    ) -> None:
        """Initialize talk connection.

        Args:
            source: Agent sending messages
            targets: Agents receiving messages
            group: Optional group this talk belongs to
            name: Optional name for this talk
            connection_type: How to handle messages:
                - "run": Execute message as a new run in target
                - "context": Add message as context to target
                - "forward": Forward message to target's outbox
            wait_for_connections: Whether to wait for all targets to complete
            priority: Task priority (lower = higher priority)
            delay: Optional delay before processing
            queued: Whether messages should be queued for manual processing
            queue_strategy: How to process queued messages:
                - "concat": Combine all messages with newlines
                - "latest": Use only the most recent message
                - "buffer": Process all messages individually
            transform: Optional function to transform messages
            filter_condition: Optional condition for filtering messages
            stop_condition: Optional condition for disconnecting
            exit_condition: Optional condition for stopping the event loop
        """
        self.source = source
        self.targets = list(targets)
        # Could perhaps better be an auto-inferring property
        self.name = name or f"{source.name}->{[t.name for t in targets]}"
        self.group = group
        self.priority = priority
        self.delay = delay
        self.active = True
        self.connection_type: ConnectionType = connection_type
        self.wait_for_connections = wait_for_connections
        self.queued = queued
        self.queue_strategy = queue_strategy
        self._pending_messages = defaultdict[str, list[ChatMessage[TTransmittedData]]](list)
        names = {t.name for t in targets}
        self._stats = TalkStats(source_name=source.name, target_names=names)
        self.transform_fn = transform
        self.filter_condition = filter_condition
        self.stop_condition = stop_condition
        self.exit_condition = exit_condition

    def __repr__(self) -> str:
        targets = [t.name for t in self.targets]
        return f"<Talk({self.connection_type}) {self.source.name} -> {targets}>"

    @overload
    def __rshift__(
        self,
        other: MessageNode[Any, str]
        | ProcessorCallback[str]
        | Sequence[MessageNode[Any, str] | ProcessorCallback[str]],
    ) -> TeamTalk[str]: ...

    @overload
    def __rshift__(
        self,
        other: MessageNode[Any, Any]
        | ProcessorCallback[Any]
        | Sequence[MessageNode[Any, Any] | ProcessorCallback[Any]],
    ) -> TeamTalk[Any]: ...

    def __rshift__(
        self,
        other: MessageNode[Any, Any]
        | ProcessorCallback[Any]
        | Sequence[MessageNode[Any, Any] | ProcessorCallback[Any]],
    ) -> TeamTalk[Any]:
        """Add another node as target to the connection or group.

        Example:
            connection >> other_agent  # Connect to single agent
            connection >> (agent2 & agent3)  # Connect to group
        """
        from agentpool import Agent, MessageNode
        from agentpool.talk import TeamTalk

        match other:
            case Callable():  # ty: ignore[invalid-match-pattern]
                other = Agent.from_callback(other)  # ty: ignore[no-matching-overload]
                if pool := self.source.agent_pool:
                    other.agent_pool = pool
                    pool.register(other.name, other)
                return self.__rshift__(other)
            case Sequence():
                team_talks = [self.__rshift__(o) for o in other]  # ty: ignore[no-matching-overload]
                return TeamTalk([self, *team_talks])
            case MessageNode():
                talks = [t.__rshift__(other) for t in self.targets]
                return TeamTalk([self, *talks])
            case _:
                raise TypeError(f"Invalid agent type: {type(other)}")

    async def _evaluate_condition(
        self,
        condition: Callable[..., bool | Awaitable[bool]] | None,
        message: ChatMessage[Any],
        target: MessageNode[Any, Any],
        *,
        default_return: bool = False,
    ) -> bool:
        """Evaluate a condition with flexible parameter handling."""
        from agentpool.talk.registry import EventContext

        if not condition:
            return default_return
        registry = pool.connection_registry if (pool := self.source.agent_pool) else None
        ctx = EventContext(
            message=message,
            target=target,
            stats=self.stats,
            registry=registry,
            talk=self,
        )
        return await execute(condition, ctx)  # ty: ignore[invalid-return-type]

    def on_event(
        self,
        event_type: ConnectionEventType,
        callback: Callable[[ConnectionEventData[TTransmittedData]], None | Awaitable[None]],
    ) -> Self:
        """Register callback for connection events."""
        from agentpool.messaging.events import ConnectionEventData

        async def wrapped_callback(event: EventData) -> None:
            if isinstance(event, ConnectionEventData) and event.event_type == event_type:
                await execute(callback, event)

        self.source._events.add_callback(wrapped_callback)
        return self

    async def _emit_connection_event(
        self,
        event_type: ConnectionEventType,
        message: ChatMessage[TTransmittedData] | None,
    ) -> None:
        from agentpool.messaging.events import ConnectionEventData

        event = ConnectionEventData[Any](
            connection=self,
            source="connection",
            connection_name=self.name,
            event_type=event_type,
            message=message,
            timestamp=get_now(),
        )
        # Propagate to all event managers through registry
        if pool := self.source.agent_pool:
            for connection in pool.connection_registry.values():
                await connection.source._events.emit_event(event)

    async def _handle_message(
        self,
        message: ChatMessage[TTransmittedData],
        prompt: str | None = None,
    ) -> list[ChatMessage[Any]]:
        """Handle message forwarding based on connection configuration."""
        # 2. Early exit checks
        if not (self.active and (not self.group or self.group.active)):
            return []

        # 3. Check exit condition for any target
        for target in self.targets:
            # Exit if condition returns True
            if await self._evaluate_condition(self.exit_condition, message, target):
                raise SystemExit

        # 4. Check stop condition for any target
        for target in self.targets:
            # Stop if condition returns True
            if await self._evaluate_condition(self.stop_condition, message, target):
                self.disconnect()
                return []

        # 5. Transform if configured
        processed_message = message
        if self.transform_fn:
            processed_message = await execute(self.transform_fn, message)
        # 6. First pass: Determine target list
        target_list = [
            target
            for target in self.targets
            if await self._evaluate_condition(
                self.filter_condition,
                processed_message,  # ty:ignore[invalid-argument-type]
                target,
                default_return=True,
            )
        ]
        # 7. emit connection processed event
        await self.connection_processed.emit(
            self.ConnectionProcessed(
                message=processed_message,  # ty:ignore[invalid-argument-type]
                source=self.source,
                targets=target_list,
                queued=self.queued,
                connection_type=self.connection_type,
            )
        )
        # 8. if we have targets, update stats and emit message forwarded
        if target_list:
            messages = [*self._stats.messages, processed_message]
            self._stats = replace(self._stats, messages=messages)
            await self.message_forwarded.emit(processed_message)

        # 9. Second pass: Actually process for each target
        responses: list[ChatMessage[Any]] = []
        for target in target_list:
            if self.queued:
                self._pending_messages[target.name].append(processed_message)  # ty:ignore[invalid-argument-type]
                continue
            if response := await self._process_for_target(processed_message, target, prompt):  # ty:ignore[invalid-argument-type]
                responses.append(response)

        return responses

    async def _process_for_target(
        self,
        message: ChatMessage[Any],
        target: MessageNode[Any, Any],
        prompt: PromptCompatible | None = None,
    ) -> ChatMessage[Any] | None:
        """Process message for a single target."""
        from agentpool.agents.base_agent import BaseAgent
        from agentpool.delegation.base_team import BaseTeam

        match self.connection_type:
            case "run":
                # Use run_message to handle ChatMessage routing
                # It extracts content, preserves session_id, and applies forwarding
                return await target.run_message(message)

            case "context":

                async def add_context() -> None:
                    match target:
                        case BaseTeam():
                            # Add context to all team members
                            for agent in target.iter_agents():
                                agent.staged_content.add_text(str(message.content))
                        case BaseAgent():
                            target.staged_content.add_text(str(message.content))

                if self.delay is not None or self.priority != 0:
                    coro = add_context()
                    target.task_manager.run_background(
                        coro, priority=self.priority, delay=self.delay
                    )
                else:
                    await add_context()
                return None

            case "forward":
                if self.delay is not None or self.priority != 0:

                    async def delayed_emit() -> None:
                        await target.connections.route_message(message)

                    coro = delayed_emit()
                    target.task_manager.run_background(
                        coro, priority=self.priority, delay=self.delay
                    )
                else:
                    await target.connections.route_message(message)
                return None

    async def trigger(
        self, prompt: PromptCompatible | None = None
    ) -> list[ChatMessage[TTransmittedData]]:
        """Process queued messages."""
        if not self._pending_messages:
            return []
        match self.queue_strategy:
            case "buffer":
                results: list[ChatMessage[TTransmittedData]] = []
                # Process each agent's queue
                for target in self.targets:
                    queue = self._pending_messages[target.name]
                    for msg in queue:
                        if resp := await self._process_for_target(msg, target, prompt):
                            results.append(resp)  # noqa: PERF401
                    queue.clear()
                return results

            case "latest":
                results = []
                # Get latest message for each agent
                for target in self.targets:
                    if queue := self._pending_messages[target.name]:
                        latest = queue[-1]
                        if resp := await self._process_for_target(latest, target, prompt):
                            results.append(resp)
                        queue.clear()
                return results

            case "concat":
                results = []
                # Concat messages per agent
                for target in self.targets:
                    queue = self._pending_messages[target.name]
                    if not queue:
                        continue

                    base = queue[-1]
                    contents = [str(m.content) for m in queue]
                    meta = {
                        **base.metadata,
                        "merged_count": len(queue),
                        "queue_strategy": self.queue_strategy,
                    }
                    content = "\n\n".join(contents)
                    merged = replace(base, content=content, metadata=meta)  # type: ignore[arg-type]

                    if response := await self._process_for_target(merged, target, prompt):
                        results.append(response)
                    queue.clear()

                return results
            case _:
                raise ValueError(f"Invalid queue strategy: {self.queue_strategy}")

    def when(self, condition: AnyFilterFn) -> Self:
        """Add condition for message forwarding."""
        self.filter_condition = condition
        return self

    def transform[TNewData](
        self,
        transformer: Callable[
            [ChatMessage[TTransmittedData]],
            ChatMessage[TNewData] | Awaitable[ChatMessage[TNewData]],
        ],
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> Talk[TNewData]:
        """Chain a new transformation after existing ones.

        Args:
            transformer: Function to transform messages
            name: Optional name for debugging
            description: Optional description

        Returns:
            New Talk instance with chained transformation

        Example:
            ```python
            talk = (agent1 >> agent2)
                .transform(parse_json)      # str -> dict
                .transform(extract_values)  # dict -> list
            ```
        """
        new_talk = Talk[TNewData](
            source=self.source,
            targets=self.targets,
            connection_type=self.connection_type,
        )

        if self.transform_fn is not None:
            oldtransform_fn = self.transform_fn

            async def chainedtransform_fn(
                data: ChatMessage[TTransmittedData],
            ) -> ChatMessage[TNewData]:
                intermediate = await execute(oldtransform_fn, data)
                return await execute(transformer, intermediate)  # ty: ignore[invalid-return-type]

            new_talk.transform_fn = chainedtransform_fn  # type: ignore[assignment]  # ty:ignore[invalid-assignment]
        else:
            new_talk.transform_fn = transformer  # type: ignore[assignment]  # ty:ignore[invalid-assignment]

        return new_talk

    @asynccontextmanager
    async def paused(self) -> AsyncIterator[Self]:
        """Temporarily set inactive."""
        previous = self.active
        self.active = False
        try:
            yield self
        finally:
            self.active = previous

    def disconnect(self) -> None:
        """Permanently disconnect the connection."""
        self.active = False

    @property
    def stats(self) -> TalkStats:
        """Get current connection statistics."""
        return self._stats


class TeamTalk[TTransmittedData = Any](list["Talk | TeamTalk"]):
    """Group of connections with aggregate operations."""

    def __init__(
        self, talks: Sequence[Talk[TTransmittedData] | TeamTalk[TTransmittedData]]
    ) -> None:
        super().__init__(talks)
        self.filter_condition: AnyFilterFn | None = None
        self.active = True

    def __repr__(self) -> str:
        return f"TeamTalk({list(self)})"

    def __rshift__(
        self,
        other: MessageNode[Any, Any]
        | ProcessorCallback[Any]
        | Sequence[MessageNode[Any, Any] | ProcessorCallback[Any]],
    ) -> TeamTalk[Any]:
        """Add another node as target to the connection or group.

        Example:
            connection >> other_agent  # Connect to single agent
            connection >> (agent2 & agent3)  # Connect to group
        """
        from agentpool import Agent, MessageNode
        from agentpool.talk import TeamTalk

        match other:
            case Callable():  # ty: ignore[invalid-match-pattern]
                other = Agent.from_callback(other)  # ty: ignore[no-matching-overload]
                for talk_ in self.iter_talks():
                    if pool := talk_.source.agent_pool:
                        other.agent_pool = pool
                        pool.register(other.name, other)
                        break
                return self.__rshift__(other)
            case Sequence():
                team_talks = [self.__rshift__(o) for o in other]  # ty: ignore[invalid-argument-type]
                return TeamTalk([self, *team_talks])
            case MessageNode():
                talks = [t.connect_to(other) for t in self.targets]
                return TeamTalk([self, *talks])
            case _:
                raise TypeError(f"Invalid agent type: {type(other)}")

    @property
    def targets(self) -> list[MessageNode[Any, Any]]:
        """Get all targets from all connections."""
        return [t for talk in self for t in talk.targets]

    def iter_talks(self) -> Iterator[Talk]:
        """Get all contained talks."""
        for t in self:
            match t:
                case Talk():
                    yield t
                case TeamTalk():
                    yield from t.iter_talks()

    async def _handle_message(self, message: ChatMessage[Any], prompt: str | None = None) -> None:
        for talk in self:
            await talk._handle_message(message, prompt)

    async def trigger(self, prompt: PromptCompatible | None = None) -> list[ChatMessage[Any]]:
        messages = []
        for talk in self:
            messages.extend(await talk.trigger(prompt))
        return messages

    @classmethod
    def from_nodes(
        cls,
        agents: Sequence[MessageNode[Any, Any]],
        targets: list[MessageNode[Any, Any]] | None = None,
    ) -> Self:
        """Create TeamTalk from a collection of agents."""
        return cls([Talk(agent, targets or []) for agent in agents])

    @asynccontextmanager
    async def paused(self) -> AsyncIterator[Self]:
        """Temporarily set inactive."""
        previous = self.active
        self.active = False
        try:
            yield self
        finally:
            self.active = previous

    def has_active_talks(self) -> bool:
        """Check if any contained talks are active."""
        return any(talk.active for talk in self)

    def get_active_talks(self) -> list[Talk | TeamTalk]:
        """Get list of currently active talks."""
        return [talk for talk in self if talk.active]

    @property
    def stats(self) -> AggregatedTalkStats:
        """Get aggregated statistics for all connections."""
        return AggregatedTalkStats(stats=[talk.stats for talk in self])

    def when(self, condition: AnyFilterFn) -> Self:
        """Add condition to all connections in group."""
        for talk in self:
            talk.when(condition)
        return self

    def disconnect(self) -> None:
        """Disconnect all connections in group."""
        for talk in self:
            talk.disconnect()
