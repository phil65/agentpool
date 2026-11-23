"""A node's view of its connections, which its router owns."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, Self

from agentpool.talk import AggregatedTalkStats, Talk, TeamTalk


if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from datetime import timedelta

    from agentpool.common_types import AgentName, AnyTransformFn, AsyncFilterFn, QueueStrategy
    from agentpool.messaging import ChatMessage, MessageNode
    from agentpool_config.forward_targets import ConnectionSessionPolicy, ConnectionType


class ConnectionManager:
    """The connections from one node (agent or team), as its router holds them."""

    def __init__(self, owner: MessageNode[Any, Any]) -> None:
        self.owner = owner

    def __repr__(self) -> str:
        return f"ConnectionManager({self.owner})"

    @property
    def _talks(self) -> list[Talk[Any]]:
        return self.owner.router.outgoing(self.owner)

    def set_wait_state(self, target: MessageNode[Any, Any] | AgentName, wait: bool = True) -> None:
        """Whether a turn of the owner waits for ``target`` to finish what it routed to it."""
        self.owner.router.set_wait(self.owner, target, wait)

    async def wait_for_connections(self) -> None:
        """Wait for the owner and every node downstream to finish their routed work."""
        await self.owner.router.wait_for(self.owner)

    def get_targets(self, recursive: bool = False) -> set[MessageNode[Any, Any]]:
        """Nodes the owner sends to over active connections (and theirs, if ``recursive``)."""
        return self.owner.router.targets(self.owner, recursive=recursive)

    def has_connection_to(self, target: MessageNode[Any, Any]) -> bool:
        """Whether an active connection goes from the owner to ``target``."""
        return any(target in talk.targets for talk in self._talks if talk.active)

    def create_connection(
        self,
        source: MessageNode[Any, Any],
        target: MessageNode[Any, Any] | Sequence[MessageNode[Any, Any]],
        *,
        connection_type: ConnectionType = "run",
        delay: timedelta | None = None,
        queued: bool = False,
        queue_strategy: QueueStrategy = "latest",
        transform: AnyTransformFn[Any] | None = None,
        filter_condition: AsyncFilterFn | None = None,
        stop_condition: AsyncFilterFn | None = None,
        exit_condition: AsyncFilterFn | None = None,
        name: str | None = None,
        session: ConnectionSessionPolicy = "target",
    ) -> Talk[Any] | TeamTalk:
        """Connect ``source`` to one target, or to each of a sequence of targets.

        Args:
            source: Source agent or team
            target: Single target or sequence of targets
            connection_type: How to handle messages
            delay: Optional delay before processing
            queued: Whether to queue messages for manual processing
            queue_strategy: How to process queued messages
            transform: Optional message transformation
            filter_condition: When to filter messages
            stop_condition: When to disconnect
            exit_condition: When to exit application
            name: Name for the connection (single target only; else auto-generated)
            session: Which session of the target the messages run in
        """
        if isinstance(target, Sequence):
            talks = [
                self.create_connection(
                    source,
                    t,
                    connection_type=connection_type,
                    delay=delay,
                    queued=queued,
                    queue_strategy=queue_strategy,
                    transform=transform,
                    filter_condition=filter_condition,
                    stop_condition=stop_condition,
                    exit_condition=exit_condition,
                    session=session,
                )
                for t in target
            ]
            return TeamTalk(talks)
        talk = Talk(
            source=source,
            targets=[target],
            connection_type=connection_type,
            name=name,
            delay=delay,
            queued=queued,
            queue_strategy=queue_strategy,
            transform=transform,
            filter_condition=filter_condition,
            stop_condition=stop_condition,
            exit_condition=exit_condition,
            session=session,
        )
        return source.router.add(talk, name=name)

    async def trigger_all(self) -> dict[AgentName, list[ChatMessage[Any]]]:
        """Process the queued messages of every queued connection."""
        return {talk.source.name: await talk.trigger() for talk in self._talks if talk.queued}

    async def trigger_for(
        self, target: AgentName | MessageNode[Any, Any]
    ) -> list[ChatMessage[Any]]:
        """Process the queued messages of the queued connections to ``target``."""
        target_name = target if isinstance(target, str) else target.name
        results: list[ChatMessage[Any]] = []
        for talk in self._talks:
            if talk.queued and any(t.name == target_name for t in talk.targets):
                results.extend(await talk.trigger())
        return results

    def disconnect_all(self) -> None:
        """Remove every connection from the owner."""
        self.owner.router.disconnect_all(self.owner)

    def disconnect(self, node: MessageNode[Any, Any]) -> None:
        """Remove the connections from the owner that reach ``node``."""
        self.owner.router.disconnect(self.owner, node)

    @asynccontextmanager
    async def paused_routing(self) -> AsyncGenerator[Self]:
        """Hold back the owner's routing (its connections stay, inactive)."""
        async with self.owner.router.paused(self.owner):
            yield self

    @property
    def stats(self) -> AggregatedTalkStats:
        """Aggregated statistics of the owner's connections."""
        return AggregatedTalkStats(stats=[talk.stats for talk in self._talks])

    def get_connections(self, recursive: bool = False) -> list[Talk[Any]]:
        """Connections from the owner (and from every node downstream, if ``recursive``)."""
        return self.owner.router.connections(self.owner, recursive=recursive)

    def get_mermaid_diagram(self, include_details: bool = True, recursive: bool = True) -> str:
        """Mermaid flowchart of the owner's connections (and downstream, if ``recursive``)."""
        talks = self.get_connections(recursive=recursive)
        return self.owner.router.mermaid(talks, include_details=include_details)
