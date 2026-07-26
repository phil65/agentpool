"""MCP protocol server implementation using FastMCP decorators."""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, Any

from fastmcp.server.middleware.caching import ResponseCachingMiddleware
from key_value.aio.stores.disk import DiskStore
import mcp
from mcp import types
import platformdirs

import agentpool
from agentpool.capabilities.subagent_capability import SubagentCapability
from agentpool.log import get_logger
from agentpool.mcp_server import constants
from agentpool_server import BaseServer


if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractAsyncContextManager

    from fastmcp import FastMCP
    from mcp.server.lowlevel.server import LifespanResultT
    from pydantic import AnyUrl

    from agentpool import AgentPool
    from agentpool.prompts.prompts import BasePrompt
    from agentpool_config.pool_server import MCPPoolServerConfig

    LifespanHandler = Callable[
        [FastMCP[LifespanResultT]],
        AbstractAsyncContextManager[LifespanResultT],
    ]

logger = get_logger(__name__)


agentpool_dir = platformdirs.user_config_dir("agentpool")

store = DiskStore(directory=agentpool_dir)
middleware = ResponseCachingMiddleware(cache_storage=store)


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

        super().__init__(pool, name=name, raise_exceptions=raise_exceptions)
        self.provider = SubagentCapability()
        self.config = config

        self._subscriptions: defaultdict[str, set[mcp.ServerSession]] = defaultdict(set)
        self._tools_registered = False
        self._prompts_registered = False

        # Create FastMCP instance
        self.fastmcp = FastMCP(
            instructions=instructions,
            lifespan=lifespan,
            version=agentpool.__version__,
            middleware=[middleware],
        )
        self.server = self.fastmcp._mcp_server

        # Register static handlers
        self._register_logging_handlers()
        self._register_resource_handlers()

    async def _register_pool_tools(self) -> None:
        """Register all pool tools using FastMCP's @tool decorator."""
        if self._tools_registered:
            return

        toolset = self.provider.get_toolset()
        tool_count = 0
        if toolset is not None:
            # SubagentCapability provides a FunctionToolset with static methods
            # Create wrapper functions without generic type parameters to avoid
            # Pydantic TypeAdapter errors with unbound generics

            async def spawn_subagent_wrapper(name: str, prompt: str) -> str:
                """Delegate a task to a named subagent.

                Args:
                    name: Name of the agent to delegate to.
                    prompt: Task description to send to the subagent.

                Returns:
                    The subagent's response.
                """
                # Call the pool's run_agent method directly
                return await self.pool.run_agent(name, prompt)

            async def get_available_agents_wrapper() -> list[str]:
                """List all agents available for delegation.

                Returns:
                    Sorted list of agent names in the registry.
                """
                return sorted(self.pool.agent_configs.keys())

            # Register the wrapper functions
            self.fastmcp.tool()(spawn_subagent_wrapper)
            self.fastmcp.tool()(get_available_agents_wrapper)
            tool_count = 2

        self._tools_registered = True
        logger.info("Registered MCP tools", count=tool_count)

    async def _register_pool_prompts(self) -> None:
        """Register all pool prompts using FastMCP's @prompt decorator."""
        if self._prompts_registered:
            return

        # SubagentCapability does not provide prompts
        prompts: list[BasePrompt] = []

        self._prompts_registered = True
        logger.info("Registered MCP prompts", count=len(prompts))

    def _register_resource_handlers(self) -> None:
        """Register resource subscription handlers."""

        @self.server.subscribe_resource()  # type: ignore[no-untyped-call, untyped-decorator]
        async def handle_subscribe(uri: AnyUrl) -> None:
            """Subscribe to resource updates."""
            uri_str = str(uri)
            self._subscriptions[uri_str].add(self.current_session)
            logger.debug("Added subscription", uri=uri)

        @self.server.unsubscribe_resource()  # type: ignore[no-untyped-call, untyped-decorator]
        async def handle_unsubscribe(uri: AnyUrl) -> None:
            """Unsubscribe from resource updates."""
            if (uri_str := str(uri)) in self._subscriptions:
                self._subscriptions[uri_str].discard(self.current_session)
                if not self._subscriptions[uri_str]:
                    del self._subscriptions[uri_str]
                logger.debug("Removed subscription", uri=uri)

    def _register_logging_handlers(self) -> None:
        """Register logging-related handlers."""

        @self.server.set_logging_level()  # type: ignore[no-untyped-call, untyped-decorator]
        async def handle_set_level(level: mcp.LoggingLevel) -> None:
            """Handle logging level changes."""
            try:
                python_level = constants.MCP_TO_LOGGING[level]
                logger.setLevel(python_level)
                data = f"Log level set to {level}"
                await self.current_session.send_log_message(level, data, logger=self.name)
            except Exception as exc:
                error_data = mcp.ErrorData(
                    message="Error setting log level",
                    code=types.INTERNAL_ERROR,
                    data=str(exc),
                )
                raise mcp.McpError(error_data) from exc

        @self.server.progress_notification()  # type: ignore[no-untyped-call, untyped-decorator]
        async def handle_progress(
            token: str | int,
            progress: float,
            total: float | None,
            message: str | None = None,
        ) -> None:
            """Handle progress notifications from client."""
            logger.debug(
                "Progress notification",
                token=token,
                progress=progress,
                total=total,
                message=message,
            )

    async def _start_async(self) -> None:
        """Start the server (blocking async - runs until stopped)."""
        # Register pool tools and prompts before starting
        await self._register_pool_tools()
        await self._register_pool_prompts()

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
        """Get current session from request context."""
        try:
            return self.server.request_context.session
        except LookupError as exc:
            raise RuntimeError("No active request context") from exc

    async def report_progress(
        self,
        progress: float,
        total: float | None = None,
        message: str | None = None,
        related_request_id: str | None = None,
    ) -> None:
        """Report progress for the current operation."""
        progress_token = (
            self.server.request_context.meta.progressToken
            if self.server.request_context.meta
            else None
        )

        if progress_token is None:
            return

        await self.server.request_context.session.send_progress_notification(
            progress_token=progress_token,
            progress=progress,
            total=total,
            message=message,
            related_request_id=related_request_id,
        )

    @property
    def client_info(self) -> mcp.Implementation | None:
        """Get client info from current session."""
        session = self.current_session
        if not session.client_params:
            return None
        return session.client_params.clientInfo

    async def notify_tool_list_changed(self) -> None:
        """Notify clients about tool list changes."""
        try:
            self._task_group.start_soon(self.current_session.send_tool_list_changed)
        except RuntimeError:
            self.log.debug("No active session for notification")
        except Exception:
            self.log.exception("Failed to send tool list change notification")

    async def notify_prompt_list_changed(self) -> None:
        """Notify clients about prompt list changes."""
        try:
            self._task_group.start_soon(self.current_session.send_prompt_list_changed)
        except RuntimeError:
            self.log.debug("No active session for notification")
        except Exception:
            self.log.exception("Failed to send prompt list change notification")
