"""MCP protocol server implementation using FastMCP decorators."""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, Any

import mcp
from mcp import types

import agentpool
from agentpool.log import get_logger
from agentpool.mcp_server import constants
from agentpool_server import BaseServer


if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractAsyncContextManager

    from fastmcp import FastMCP
    from fastmcp.experimental.events import EventBatch, EventPoll
    from mcp.server.context import ServerRequestContext
    from mcp.server.lowlevel.server import LifespanResultT

    from agentpool import AgentPool
    from agentpool.sessions.activity import ActivityKind
    from agentpool_config.pool_server import MCPPoolServerConfig

    LifespanHandler = Callable[
        [FastMCP[LifespanResultT]],
        AbstractAsyncContextManager[LifespanResultT],
    ]

logger = get_logger(__name__)

ACTIVITY_EVENTS: dict[ActivityKind, str] = {
    "state_update": (
        "A session's foreground state changed: running, requires_action (waiting for the "
        "user), or idle (with stop_reason end_turn / cancelled / error, usage, error)"
    ),
    "info_update": "A session was created or got its title (title, parent_id)",
}
ACTIVITY_ARGUMENTS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "agent": {"type": "string", "description": "Only this agent's sessions"},
        "session_id": {"type": "string", "description": "Only this session"},
    },
}
ACTIVITY_POLL_MS = 500


class MCPServer(BaseServer):
    """MCP protocol server implementation using FastMCP decorators.

    This server uses FastMCP's native @tool and @prompt decorators for
    cleaner, more maintainable code. Tools and prompts from the pool are
    registered dynamically using the same decorator pattern.
    """

    def __init__(
        self,
        pool: AgentPool[Any],
        config: MCPPoolServerConfig,
        lifespan: LifespanHandler | None = None,
        instructions: str | None = None,
        name: str = "agentpool-server",
        raise_exceptions: bool = False,
    ) -> None:
        """Initialize server with agent pool.

        Args:
            pool: AgentPool to expose through MCP
            config: Server configuration
            name: Server name for MCP protocol
            lifespan: Lifespan context manager
            instructions: Instructions for server usage
            raise_exceptions: Whether to raise exceptions during server start
        """
        from fastmcp import FastMCP

        from agentpool.resource_providers.pool import PoolResourceProvider

        super().__init__(pool, name=name, raise_exceptions=raise_exceptions)
        match config.serve_nodes:
            case True:
                nodes: list[str] | None = None
            case False:
                nodes = []
            case list() as names:
                nodes = names
        self.provider = PoolResourceProvider(pool, nodes=nodes)
        self.config = config

        self._subscriptions: defaultdict[str, set[mcp.ServerSession]] = defaultdict(set)
        self._tools_registered = False

        # Create FastMCP instance
        self.fastmcp = FastMCP(
            instructions=instructions,
            lifespan=lifespan,
            version=agentpool.__version__,
        )
        self.server = self.fastmcp._mcp_server
        if config.background_tasks:
            try:
                from fastmcp_tasks import TasksExtension
            except ImportError as e:
                msg = "background_tasks needs the mcp-tasks extra: pip install agentpool[mcp-tasks]"
                raise ImportError(msg) from e

            extension = TasksExtension(url=config.tasks_url, execution_ttl=config.tasks_ttl)
            self.fastmcp.add_extension(extension)
        if config.serve_events:
            self._register_activity_events()
        if config.serve_skills:
            from agentpool_server.mcp_server.skills_provider import PoolSkillsProvider

            self.fastmcp.add_provider(PoolSkillsProvider(pool.skills))

        # Register static handlers
        self._register_logging_handlers()
        self._register_resource_handlers()

    def _register_activity_events(self) -> None:
        """Publish the pool's activity feed as the event types ``session.<kind>``."""
        from fastmcp.experimental.events import EventsExtension

        extension = EventsExtension()
        self.fastmcp.add_extension(extension)
        for kind, description in ACTIVITY_EVENTS.items():
            extension.event(
                name=f"session.{kind}",
                description=description,
                input_schema=ACTIVITY_ARGUMENTS,
            )(self._activity_check(kind))

    def _activity_check(self, kind: ActivityKind) -> Callable[[EventPoll], EventBatch]:
        """The check function of the event type for ``kind`` entries of the activity feed."""
        from fastmcp.experimental.events import Event, EventBatch

        def check(poll: EventPoll) -> EventBatch:
            feed = self.pool.activity
            if poll.cursor is None or not poll.cursor.isdigit():  # From now
                return EventBatch(
                    cursor=str(feed.position),
                    truncated=poll.cursor is not None,
                    next_poll_ms=ACTIVITY_POLL_MS,
                )
            window = feed.since(int(poll.cursor))
            agent, session_id = poll.arguments.get("agent"), poll.arguments.get("session_id")
            matching = [
                entry
                for entry in window.entries
                if entry.kind == kind
                and agent in (None, entry.agent)
                and session_id in (None, entry.session_id)
            ]
            limit = poll.max_events
            has_more = limit is not None and len(matching) > limit
            batch = matching[:limit] if has_more else matching
            return EventBatch(
                events=[
                    Event(data=e.to_payload(), event_id=str(e.seq), timestamp=e.timestamp)
                    for e in batch
                ],
                cursor=str(batch[-1].seq) if has_more else str(window.position),
                truncated=window.truncated,
                has_more=has_more,
                next_poll_ms=ACTIVITY_POLL_MS,
            )

        return check

    async def _register_pool_tools(self) -> None:
        """Register all pool tools using FastMCP's @tool decorator."""
        if self._tools_registered:
            return

        tools = await self.provider.get_tools()

        for tool in tools:
            # TODO: previously there was code to filter private attributes using a wrapper.
            # comment said it was to remove _meta params, but not sure if it was required at all.
            tool_annotations = tool.hints.to_mcp()
            # Optional task mode: clients that opt in get a task, all others a normal call
            self.fastmcp.tool(annotations=tool_annotations, task=self.config.background_tasks)(
                tool.fn
            )

        self._tools_registered = True
        logger.info("Registered MCP tools", count=len(tools))

    def _register_resource_handlers(self) -> None:
        """Register resource subscription handlers."""

        async def handle_subscribe(
            ctx: ServerRequestContext[Any], params: types.SubscribeRequestParams
        ) -> types.EmptyResult:
            """Subscribe to resource updates."""
            self._subscriptions[params.uri].add(ctx.session)
            logger.debug("Added subscription", uri=params.uri)
            return types.EmptyResult()

        async def handle_unsubscribe(
            ctx: ServerRequestContext[Any], params: types.UnsubscribeRequestParams
        ) -> types.EmptyResult:
            """Unsubscribe from resource updates."""
            if (uri := params.uri) in self._subscriptions:
                self._subscriptions[uri].discard(ctx.session)
                if not self._subscriptions[uri]:
                    del self._subscriptions[uri]
                logger.debug("Removed subscription", uri=uri)
            return types.EmptyResult()

        self.server.add_request_handler(
            "resources/subscribe", types.SubscribeRequestParams, handle_subscribe
        )
        self.server.add_request_handler(
            "resources/unsubscribe", types.UnsubscribeRequestParams, handle_unsubscribe
        )

    def _register_logging_handlers(self) -> None:
        """Register logging-related handlers.

        The set-level handler wraps FastMCP's own handler (which stores the
        client's requested level on its connection) and additionally adjusts
        the local logger level.
        """
        fastmcp_set_level = self.server.get_request_handler("logging/setLevel")

        async def handle_set_level(
            ctx: ServerRequestContext[Any], params: types.SetLevelRequestParams
        ) -> types.EmptyResult:
            """Handle logging level changes."""
            level = params.level
            try:
                if fastmcp_set_level is not None:
                    await fastmcp_set_level.handler(ctx, params)
                logger.setLevel(constants.MCP_TO_LOGGING[level])
                data = f"Log level set to {level}"
                # Logging is deprecated in MCP 2026-07-28 (SEP-2577), still used by older clients
                await ctx.session.send_log_message(level, data, logger=self.name)  # ty: ignore[deprecated]
            except Exception as exc:
                msg = "Error setting log level"
                raise mcp.MCPError(types.INTERNAL_ERROR, msg, str(exc)) from exc
            return types.EmptyResult()

        async def handle_progress(
            ctx: ServerRequestContext[Any], params: types.ProgressNotificationParams
        ) -> None:
            """Handle progress notifications from client."""
            logger.debug(
                "Progress notification",
                token=params.progress_token,
                progress=params.progress,
                total=params.total,
                message=params.message,
            )

        self.server.add_request_handler(
            "logging/setLevel", types.SetLevelRequestParams, handle_set_level
        )
        self.server.add_notification_handler(
            "notifications/progress", types.ProgressNotificationParams, handle_progress
        )

    async def _start_async(self) -> None:
        """Start the server (blocking async - runs until stopped)."""
        # Register pool tools before starting
        await self._register_pool_tools()

        # Start FastMCP server
        if self.config.transport == "stdio":
            await self.fastmcp.run_async(transport=self.config.transport)
        else:
            await self.fastmcp.run_async(
                transport=self.config.transport,
                host=self.config.host,
                port=self.config.port,
            )

    @property
    def current_session(self) -> mcp.ServerSession:
        """Get current session from the active FastMCP request context."""
        from fastmcp.server.dependencies import get_context

        return get_context().session

    async def report_progress(
        self,
        progress: float,
        total: float | None = None,
        message: str | None = None,
    ) -> None:
        """Report progress for the current operation."""
        from fastmcp.server.dependencies import get_context

        await get_context().report_progress(progress, total=total, message=message)

    @property
    def client_info(self) -> mcp.Implementation | None:
        """Get client info from current session."""
        session = self.current_session
        if not session.client_params:
            return None
        return session.client_params.client_info

    async def notify_tool_list_changed(self) -> None:
        """Notify clients about tool list changes."""
        try:
            self.task_manager.create_task(self.current_session.send_tool_list_changed())
        except RuntimeError:
            self.log.debug("No active session for notification")
        except Exception:
            self.log.exception("Failed to send tool list change notification")
