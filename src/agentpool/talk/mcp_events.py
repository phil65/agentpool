"""MCP event triggers: subscriptions to events MCP servers publish (SEP-3415, experimental)."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import TYPE_CHECKING, Any, Self

from evented.base import EventSource
from evented.event_data import EventData
import platformdirs
from upathtools import to_upath

from agentpool.log import get_logger


if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from types import TracebackType

    from fastmcp.experimental.events import EventOccurrence, EventSubscription
    from upath import UPath

    from agentpool.mcp_server.client import MCPClient
    from agentpool.messaging import MessageNode
    from agentpool_config.events import MCPEventTriggerConfig


logger = get_logger(__name__)


def default_cursor_file() -> UPath:
    """Where the positions of resumable MCP event subscriptions are kept."""
    return to_upath(platformdirs.user_data_dir("agentpool")) / "mcp_event_cursors.json"


REMEMBERED_EVENT_IDS = 256
"""Handled event IDs kept with a position: its cursor may lie before them."""


@dataclass
class Position:
    """Where a subscription stands: the cursor, and the events handled since it may lag."""

    cursor: str | None = None
    handled: list[str] = field(default_factory=list)

    def note(self, event_id: str, cursor: str | None) -> None:
        """Record ``event_id`` as handled, with the subscription's cursor after it."""
        self.handled = [*self.handled, event_id][-REMEMBERED_EVENT_IDS:]
        self.cursor = cursor


class CursorStore:
    """Positions of MCP event subscriptions, so a restart resumes where they were."""

    def __init__(self, path: UPath | None = None) -> None:
        self.path = path if path is not None else default_cursor_file()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text())
        except OSError, ValueError:
            logger.warning("Unreadable MCP event cursor file, starting fresh", path=str(self.path))
            return {}
        return data if isinstance(data, dict) else {}

    def get(self, key: str) -> Position:
        """The stored position for ``key`` (empty: start from now)."""
        match self._load().get(key):
            case {"cursor": str() | None as cursor, "handled": list() as handled}:
                return Position(cursor=cursor, handled=[str(i) for i in handled])
            case _:
                return Position()

    def set(self, key: str, position: Position) -> None:
        """Store the position for ``key``."""
        positions = self._load()
        positions[key] = {"cursor": position.cursor, "handled": position.handled}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(positions, indent=2, sort_keys=True))


class MCPEventData(EventData):
    """An event an MCP server delivered to a subscription."""

    server: str
    """Name of the MCP server."""

    event: EventOccurrence
    """The event (name, data, ID, timestamp)."""

    prompt_template: str | None = None
    """Jinja2 template for the prompt (``event``, ``server``); else name and data."""

    def to_prompt(self) -> str:
        """The prompt the triggered node runs."""
        if self.prompt_template is not None:
            from jinja2 import Environment

            template = Environment().from_string(self.prompt_template)
            return template.render(event=self.event, server=self.server)
        data = json.dumps(self.event.data, indent=2, ensure_ascii=False)
        return f"MCP event {self.event.name!r} from {self.server!r}:\n{data}"


def _rebuild_event_data() -> None:
    from datetime import datetime

    from fastmcp.experimental.events import EventOccurrence

    namespace = {"EventOccurrence": EventOccurrence, "datetime": datetime, "Any": Any}
    MCPEventData.model_rebuild(_types_namespace=namespace)


_rebuild_event_data()


def find_server_client(node: MessageNode[Any, Any], server: str) -> MCPClient:
    """The MCP client of ``node``'s (else its pool's) server ``server`` (name or client ID)."""
    providers = list(node.mcp.get_mcp_providers())
    if (pool := node.agent_pool) is not None:
        providers.extend(pool.mcp.get_mcp_providers())
    for provider in providers:
        if server in (provider.server.name, provider.server.client_id):
            return provider.client
    known = [p.server.name or p.server.client_id for p in providers]
    msg = f"{node.name}: no MCP server {server!r} (connected: {known})"
    raise LookupError(msg)


class MCPEventSource(EventSource):
    """Subscription to one event type of one MCP server, for one trigger of a node.

    Validates the subscription on entry: the server must offer the event type, and the
    arguments must match its input schema. With ``resume``, the position after each event
    the node has handled is stored, so a restart delivers what arrived meanwhile.
    """

    def __init__(
        self,
        config: MCPEventTriggerConfig,
        node: MessageNode[Any, Any],
        cursors: CursorStore | None = None,
    ) -> None:
        self.config = config
        self.node = node
        self.cursors = cursors if cursors is not None else CursorStore()
        self._client: MCPClient | None = None
        self._subscription: EventSubscription | None = None
        self._unhandled = False
        """An event was yielded and its run hasn't come back: the cursor may be past it."""
        self._position = Position()

    @property
    def key(self) -> str:
        """Identifies the subscription among the stored positions."""
        c = self.config
        arguments = json.dumps(c.arguments, sort_keys=True)
        return f"{self.node.name}/{c.name}/{c.server}/{c.event}/{arguments}"

    async def __aenter__(self) -> Self:
        from fastmcp.experimental.events import list_events
        import jsonschema

        config = self.config
        client = find_server_client(self.node, config.server)
        if not client.config.events:
            msg = f"MCP server {config.server!r}: set `events: true` for trigger {config.name!r}"
            raise ValueError(msg)
        offered = {event.name: event for event in await list_events(client.fastmcp_client)}
        if (event_type := offered.get(config.event)) is None:
            msg = f"MCP server {config.server!r} has no event {config.event!r} ({sorted(offered)})"
            raise ValueError(msg)
        if event_type.input_schema is not None:
            jsonschema.validate(config.arguments, event_type.input_schema)
        self._client = client
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        subscription = self._subscription
        if not self.config.resume or subscription is None or self._unhandled:
            return
        if subscription.cursor is not None:  # Also the position before the first event
            self._position.cursor = subscription.cursor
        self.cursors.set(self.key, self._position)

    async def events(self) -> AsyncGenerator[EventData]:
        """The events, as they arrive (each is handled before the next is fetched)."""
        from fastmcp.experimental.events import EventBacklogFullError, subscribe_events

        if self._client is None:
            raise RuntimeError("MCPEventSource used before it was entered")
        config = self.config
        self._position = self.cursors.get(self.key) if config.resume else Position()
        delivery = None if config.delivery == "auto" else config.delivery
        while True:
            subscription = subscribe_events(
                self._client.fastmcp_client,
                config.event,
                config.arguments or None,
                cursor=self._position.cursor,
                min_poll_interval=config.min_poll_interval,
                delivery=delivery,
            )
            self._subscription = subscription
            try:
                async for occurrence in subscription:
                    if occurrence.event_id in self._position.handled:
                        continue  # Handled before a restart: the cursor lay before it
                    self._unhandled = True
                    yield MCPEventData(
                        source=config.name,
                        server=config.server,
                        event=occurrence,
                        prompt_template=config.prompt,
                    )
                    self._unhandled = False
                    self._position.note(occurrence.event_id, subscription.cursor)
                    if config.resume:  # Handled: resume after it
                        self.cursors.set(self.key, self._position)
            except EventBacklogFullError:  # Fell behind: resubscribe from the position
                logger.warning("MCP event backlog full, resubscribing", trigger=config.name)
                self._position.cursor = subscription.cursor
                continue
            return
