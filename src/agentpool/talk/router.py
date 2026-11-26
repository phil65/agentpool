"""The router: owns the connections between nodes and routes messages along them."""

from __future__ import annotations

from collections import defaultdict
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from agentpool.log import get_logger
from agentpool.talk.registry import ConnectionRegistry
from agentpool.talk.sessions import SessionPicker
from agentpool.talk.triggers import Triggers
from agentpool.utils.inspection import get_fn_name
from agentpool.utils.tasks import run_all


if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable

    from agentpool.messaging import ChatMessage, MessageNode
    from agentpool.talk.talk import Talk


logger = get_logger(__name__)


class Router:
    """Owns the connections (``Talk``s) between nodes and routes messages along them.

    A pool has one (``pool.router``); a node outside a pool makes its own on first use
    (``MessageNode.router``) and hands its connections to the pool's when it joins one.
    Nodes don't hold connections: ``node.connections`` is a view of the router's.

    Nodes don't route either: they announce each response on ``message_sent``, and the
    router, subscribed there for every node with a connection, sends it on.
    """

    def __init__(self) -> None:
        self._outgoing: defaultdict[MessageNode[Any, Any], list[Talk[Any]]] = defaultdict(list)
        self.registry = ConnectionRegistry()
        """Every connection by name (named ones under theirs, the others auto-named)."""
        self.sessions = SessionPicker()
        """Sessions that connections and triggers with a session policy run in."""
        self.triggers = Triggers(self.sessions)
        """The nodes' event triggers (connection triggers get the connections' events)."""
        self._subscriptions: dict[
            MessageNode[Any, Any], Callable[[ChatMessage[Any]], Awaitable[None]]
        ] = {}

    def __repr__(self) -> str:
        return f"Router({sum(len(talks) for talks in self._outgoing.values())} connections)"

    # --- Connections ------------------------------------------------------------------

    def add(self, talk: Talk[Any], *, name: str | None = None) -> Talk[Any]:
        """Add a connection, registering it under ``name`` (else an auto-generated one)."""
        self._outgoing[talk.source].append(talk)
        talk._router = self
        self._subscribe(talk.source)
        if name:
            self.registry.register(name, talk)
        else:
            self.registry.register_auto(talk)
        return talk

    def remove(self, talk: Talk[Any]) -> None:
        """Remove a connection (it stops carrying messages)."""
        talk.active = False
        talks = self._outgoing.get(talk.source, [])
        if talk in talks:
            talks.remove(talk)
            if not talks:
                del self._outgoing[talk.source]
                self._unsubscribe(talk.source)
        if talk.name in self.registry and self.registry[talk.name] is talk:
            del self.registry[talk.name]
        talk._router = None

    def adopt(self, other: Router) -> None:
        """Take over the connections and triggers of ``other`` (a node's own router)."""
        for talk in [t for talks in other._outgoing.values() for t in talks]:
            other.remove(talk)
            talk.active = True
            self.add(talk, name=talk.name)
        self.triggers.adopt(other.triggers)

    def outgoing(self, source: MessageNode[Any, Any]) -> list[Talk[Any]]:
        """Connections from ``source``, in the order they were made."""
        return list(self._outgoing.get(source, []))

    def disconnect(self, source: MessageNode[Any, Any], node: MessageNode[Any, Any]) -> None:
        """Remove the connections from ``source`` that reach ``node``."""
        for talk in self.outgoing(source):
            if node in talk.targets:
                self.remove(talk)

    def disconnect_all(self, source: MessageNode[Any, Any]) -> None:
        """Remove every connection from ``source``."""
        for talk in self.outgoing(source):
            self.remove(talk)

    def targets(
        self, source: MessageNode[Any, Any], *, recursive: bool = False
    ) -> set[MessageNode[Any, Any]]:
        """Nodes ``source`` sends to over active connections (and theirs, if ``recursive``)."""
        direct = {t for talk in self.outgoing(source) if talk.active for t in talk.targets}
        if not recursive:
            return direct
        seen: set[MessageNode[Any, Any]] = {source}
        found: set[MessageNode[Any, Any]] = set()
        pending = list(direct)
        while pending:
            node = pending.pop()
            if node in seen:
                continue
            seen.add(node)
            found.add(node)
            pending.extend(node.router.targets(node))
        return found

    def set_wait(
        self, source: MessageNode[Any, Any], target: MessageNode[Any, Any] | str, wait: bool
    ) -> None:
        """Whether ``source`` waits for ``target`` to finish what it routed to it."""
        target_name = target if isinstance(target, str) else target.name
        for talk in self.outgoing(source):
            if any(t.name == target_name for t in talk.targets):
                talk.wait_for_connections = wait

    # --- Routing ----------------------------------------------------------------------

    def _subscribe(self, source: MessageNode[Any, Any]) -> None:
        if source in self._subscriptions:
            return

        async def on_sent(message: ChatMessage[Any]) -> None:
            await self.route(source, message)

        source.message_sent.connect(on_sent)
        self._subscriptions[source] = on_sent

    def _unsubscribe(self, source: MessageNode[Any, Any]) -> None:
        if (on_sent := self._subscriptions.pop(source, None)) is not None:
            source.message_sent.disconnect(on_sent)

    async def route(self, source: MessageNode[Any, Any], message: ChatMessage[Any]) -> None:
        """Send ``message`` from ``source`` along its connections.

        Connections are served in the order they were made; neighbouring ``parallel``
        ones (``connect_to([...], parallel=True)``) at once.
        Waits for the targets (and what they route on) when a connection asks for it.
        """
        talks = self.outgoing(source)
        logger.debug("Routing message", sender=message.name, connections=len(talks))
        batch: list[Talk[Any]] = []  # Neighbouring parallel connections run at once
        for talk in talks:
            if talk.parallel:
                batch.append(talk)
                continue
            if batch:
                await run_all([t._handle_message(message, None) for t in batch])
                batch = []
            await talk._handle_message(message, None)
        if batch:
            await run_all([t._handle_message(message, None) for t in batch])
        if any(talk.wait_for_connections for talk in talks if talk.active):
            await self.wait_for(source)

    async def wait_for(self, source: MessageNode[Any, Any]) -> None:
        """Wait until ``source`` and every node downstream finished their routed work."""
        await source.task_manager.complete_tasks()
        for node in self.targets(source, recursive=True):
            await node.task_manager.complete_tasks()

    @asynccontextmanager
    async def paused(self, source: MessageNode[Any, Any]) -> AsyncGenerator[None]:
        """Hold back the routing from ``source`` (its connections stay, inactive)."""
        active = [talk for talk in self.outgoing(source) if talk.active]
        for talk in active:
            talk.active = False
        try:
            yield
        finally:
            for talk in active:
                talk.active = True

    # --- Views ------------------------------------------------------------------------

    def connections(
        self, source: MessageNode[Any, Any], *, recursive: bool = False
    ) -> list[Talk[Any]]:
        """Connections from ``source`` (and from every node downstream, if ``recursive``)."""
        result = self.outgoing(source)
        if not recursive:
            return result
        seen: set[MessageNode[Any, Any]] = {source}
        pending = [t for talk in result for t in talk.targets]
        while pending:
            node = pending.pop()
            if node in seen:
                continue
            seen.add(node)
            talks = node.router.outgoing(node)
            result.extend(talks)
            pending.extend(t for talk in talks for t in talk.targets)
        return result

    def mermaid(self, talks: list[Talk[Any]], *, include_details: bool = True) -> str:
        """A mermaid flowchart of ``talks``."""
        lines = ["flowchart LR"]
        for talk in talks:
            for target in talk.targets:
                details: list[str] = [talk.connection_type] if include_details else []
                if include_details:
                    if talk.queued:
                        details.append(f"queued({talk.queue_strategy})")
                    if fn := talk.filter_condition:
                        details.append(f"filter:{get_fn_name(fn)}")
                    if fn := talk.stop_condition:
                        details.append(f"stop:{get_fn_name(fn)}")
                    if fn := talk.exit_condition:
                        details.append(f"exit:{get_fn_name(fn)}")
                label = f"|{' '.join(details)}|" if details else ""
                lines.append(f"    {talk.source.name}--{label}-->{target.name}")
        return "\n".join(lines)
