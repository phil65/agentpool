"""Event triggers of a router's nodes: their sources run here, their events run the nodes."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Self

from agentpool.log import get_logger
from agentpool.utils.inspection import execute
from agentpool_config.events import ConnectionTriggerConfig


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Coroutine
    from types import TracebackType

    from evented.base import EventSource
    from evented.event_data import EventData

    from agentpool.messaging import MessageNode
    from agentpool.messaging.events import ConnectionEventData
    from agentpool_config.events import EventConfig

    ConnectionEventCallback = Callable[[ConnectionEventData[Any]], Awaitable[None] | None]


logger = get_logger(__name__)


class Triggers:
    """Runs the event triggers of a router's nodes and delivers each event to its node.

    A trigger is bound to the node whose config holds it. Event sources (file watches,
    webhooks, timers, email) run while the triggers are started: the pool starts them
    once its nodes are up, a node outside a pool starts its own. Connection triggers have
    no source: the router's connections report their events here (``connection_event``).
    """

    def __init__(self) -> None:
        self._configs: list[tuple[MessageNode[Any, Any], EventConfig]] = []
        self._sources: dict[tuple[MessageNode[Any, Any], str], EventSource] = {}
        self._pumps: set[asyncio.Task[None]] = set()
        self._callbacks: list[ConnectionEventCallback] = []
        self._running = 0

    def __repr__(self) -> str:
        return f"Triggers({len(self._configs)} configured, running={bool(self._running)})"

    @property
    def configs(self) -> list[tuple[MessageNode[Any, Any], EventConfig]]:
        """The triggers, each with the node it runs."""
        return list(self._configs)

    def of(self, node: MessageNode[Any, Any]) -> list[EventConfig]:
        """The triggers that run ``node``."""
        return [config for owner, config in self._configs if owner is node]

    # --- Configuration ----------------------------------------------------------------

    def add(self, node: MessageNode[Any, Any], config: EventConfig) -> None:
        """Bind a trigger to ``node`` (its source starts now if the triggers run)."""
        if any(owner is node and c.name == config.name for owner, c in self._configs):
            raise ValueError(f"Trigger {config.name!r} already exists for {node.name!r}")
        self._configs.append((node, config))
        if self._running and self._has_source(config):
            self._spawn(self._start(node, config), name=f"start-trigger-{config.name}")

    async def remove(self, node: MessageNode[Any, Any], name: str | None = None) -> None:
        """Unbind ``node``'s trigger ``name`` (all of its triggers without a name)."""
        removed = [
            (owner, c)
            for owner, c in self._configs
            if owner is node and (name is None or c.name == name)
        ]
        self._configs = [entry for entry in self._configs if entry not in removed]
        for owner, config in removed:
            await self._stop(owner, config.name)

    def adopt(self, other: Triggers) -> None:
        """Take over the triggers of ``other`` (a node's own, as the node joins a pool).

        Sources ``other`` runs for them stop; they start here if these triggers run.
        """
        for node, config in other.configs:
            other._configs.remove((node, config))
            if (node, config.name) in other._sources:
                other._spawn(other._stop(node, config.name), name=f"stop-{config.name}")
            self.add(node, config)

    # --- Lifecycle --------------------------------------------------------------------

    async def __aenter__(self) -> Self:
        """Start the event sources (nested entries count)."""
        self._running += 1
        if self._running == 1:
            for node, config in self.configs:
                if self._has_source(config):
                    await self._start(node, config)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Stop the event sources once the last entry exits."""
        self._running -= 1
        if self._running:
            return
        for node, name in list(self._sources):
            await self._stop(node, name)
        for task in list(self._pumps):
            task.cancel()
        await asyncio.gather(*self._pumps, return_exceptions=True)

    @staticmethod
    def _has_source(config: EventConfig) -> bool:
        return config.enabled and not isinstance(config, ConnectionTriggerConfig)

    def _spawn(self, coro: Coroutine[Any, Any, None], *, name: str) -> None:
        task = asyncio.create_task(coro, name=name)
        self._pumps.add(task)
        task.add_done_callback(self._pumps.discard)

    async def _start(self, node: MessageNode[Any, Any], config: EventConfig) -> None:
        from evented.base import EventSource

        key = (node, config.name)
        if key in self._sources or isinstance(config, ConnectionTriggerConfig):
            return
        try:
            source = EventSource.from_config(config)
            await source.__aenter__()
        except Exception:
            logger.exception("Failed to start trigger", node=node.name, trigger=config.name)
            return
        self._sources[key] = source
        self._spawn(self._pump(node, source), name=f"trigger-{node.name}-{config.name}")

    async def _stop(self, node: MessageNode[Any, Any], name: str) -> None:
        if (source := self._sources.pop((node, name), None)) is not None:
            await source.__aexit__(None, None, None)

    async def _pump(self, node: MessageNode[Any, Any], source: EventSource) -> None:
        async for event in source.events():
            await self._run(node, event)

    async def _run(
        self, node: MessageNode[Any, Any], event: EventData | ConnectionEventData[Any]
    ) -> None:
        if not (prompt := event.to_prompt()):
            return
        try:
            await node.run(prompt)
        except Exception:
            logger.exception("Triggered run failed", node=node.name, source=event.source)

    # --- Connection events ------------------------------------------------------------

    def on_connection_event(self, callback: ConnectionEventCallback) -> None:
        """Call ``callback`` with every connection event."""
        self._callbacks.append(callback)

    def remove_connection_callback(self, callback: ConnectionEventCallback) -> None:
        """Stop calling ``callback``."""
        self._callbacks.remove(callback)

    async def connection_event(self, event: ConnectionEventData[Any]) -> None:
        """Deliver an event of a connection: to the callbacks, and to matching triggers.

        A matching trigger runs its node in the node's background tasks, not inline: the
        event comes from routing in progress, which must not wait for (or re-enter) it.
        """
        for callback in list(self._callbacks):
            try:
                await execute(callback, event)
            except Exception:
                logger.exception("Connection event callback failed", event_type=event.event_type)
        for node, config in self.configs:
            if not (isinstance(config, ConnectionTriggerConfig) and config.enabled):
                continue
            if await config.matches_event(event):
                node.task_manager.create_task(
                    self._run(node, event), name=f"trigger-{node.name}-{config.name}"
                )
