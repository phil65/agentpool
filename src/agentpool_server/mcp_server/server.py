"""MCP protocol server implementation using FastMCP decorators."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import mcp
from mcp import types

import agentpool
from agentpool.log import get_logger
from agentpool.mcp_server import constants
from agentpool_server import BaseServer
from agentpool_server.mcp_server.asking import ServedRuns


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from contextlib import AbstractAsyncContextManager

    from fastmcp import FastMCP
    from fastmcp.experimental.events import EventBatch, EventPoll
    from mcp.server.context import ServerRequestContext
    from mcp.server.lowlevel.server import LifespanResultT

    from agentpool import AgentPool, MessageNode
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
    "usage_update": (
        "Context fill after a response: used tokens of size (null when unknown), and the "
        "session's cost so far (cost, currency)"
    ),
    "notice": (
        "Information for the user, not part of the conversation (severity, title, description)"
    ),
    "compaction_update": (
        "A context compaction started or ended (compaction_id, status in_progress / completed / "
        "failed, trigger, pre_tokens, error)"
    ),
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
        self.runs = ServedRuns()
        """Runs of the served nodes, and the ones waiting for the user's answer."""
        self.config = config

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
        if config.serve_prompts is not False:
            from agentpool_server.mcp_server.prompts_provider import PoolPromptsProvider

            names = config.serve_prompts if isinstance(config.serve_prompts, list) else None
            self.fastmcp.add_provider(PoolPromptsProvider(pool.prompt_manager, names))
        if config.serve_skills:
            from agentpool_server.mcp_server.skills_provider import PoolSkillsProvider

            self.fastmcp.add_provider(PoolSkillsProvider(pool.skills))

        # Register static handlers
        self._register_logging_handlers()

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

        nodes = self.provider.served_nodes()
        for node in nodes:
            tool = node.to_tool()
            # Optional task mode: clients that opt in get a task, all others a normal call
            self.fastmcp.tool(
                name=tool.name,
                description=tool.description,
                annotations=tool.hints.to_mcp(),
                task=self.config.background_tasks,
            )(self._ask_function(node, tool.fn.__annotations__.get("return", Any)))

        self._tools_registered = True
        logger.info("Registered MCP tools", count=len(nodes))

    def _ask_function(
        self, node: MessageNode[Any, Any], returns: Any
    ) -> Callable[..., Awaitable[Any]]:
        """The tool function of ``node``: its runs can ask the client's user (``asking``)."""
        from fastmcp import Context

        async def ask(prompt: str, ctx: Context) -> Any:
            return await self.runs.call(node, prompt, ctx)

        ask.__annotations__ = {
            "prompt": str,
            "ctx": Context,
            "return": returns | types.InputRequiredResult,
        }
        return ask

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
                **self.http_options(),
            )

    def http_options(self) -> dict[str, Any]:
        """HTTP transport options: CORS for ``cors_origins``, which pass the origin check."""
        if not (origins := self.config.cors_origins):
            return {}
        from starlette.middleware import Middleware
        from starlette.middleware.cors import CORSMiddleware

        cors = Middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=["*"],
            expose_headers=["Mcp-Session-Id"],
        )
        return {"middleware": [cors], "allowed_origins": origins}

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
