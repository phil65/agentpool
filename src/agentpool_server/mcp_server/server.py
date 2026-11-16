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
from agentpool.log import get_logger
from agentpool.mcp_server import constants
from agentpool_server import BaseServer


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from contextlib import AbstractAsyncContextManager

    from fastmcp import FastMCP
    from mcp.server.context import ServerRequestContext
    from mcp.server.lowlevel.server import LifespanResultT

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

        from agentpool.resource_providers.pool import PoolResourceProvider

        super().__init__(pool, name=name, raise_exceptions=raise_exceptions)
        self.provider = PoolResourceProvider(pool, zed_mode=config.zed_mode)
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

        tools = await self.provider.get_tools()

        for tool in tools:
            # TODO: previously there was code to filter private attributes using a wrapper.
            # comment said it was to remove _meta params, but not sure if it was required at all.
            tool_annotations = tool.hints.to_mcp()
            # TODO: set task=True?
            self.fastmcp.tool(annotations=tool_annotations)(tool.fn)

        self._tools_registered = True
        logger.info("Registered MCP tools", count=len(tools))

    async def _register_pool_prompts(self) -> None:
        """Register all pool prompts using FastMCP's @prompt decorator."""
        if self._prompts_registered:
            return

        prompts = await self.provider.get_prompts()

        for prompt in prompts:
            # Create handler with closure to capture prompt instance
            def make_handler(prompt_instance: BasePrompt) -> Callable[..., Awaitable[str]]:
                async def handler(**kwargs: Any) -> str:
                    """Dynamically generated prompt handler."""
                    from agentpool.mcp_server import conversions

                    try:
                        assert prompt_instance.name
                        parts = await self.provider.get_request_parts(
                            prompt_instance.name, kwargs or {}
                        )
                        messages = [msg for p in parts for msg in conversions.to_mcp_messages(p)]

                        # Format messages as text
                        result_lines = []
                        for msg in messages:
                            if isinstance(msg.content, types.TextContent):
                                result_lines.append(f"{msg.role}: {msg.content.text}")
                            else:
                                result_lines.append(f"{msg.role}: {msg.content}")

                        return "\n".join(result_lines)
                    except Exception as exc:
                        logger.exception("Prompt execution failed", name=prompt_instance.name)
                        return f"Prompt execution failed: {exc}"

                # Set proper function metadata for FastMCP
                assert prompt_instance.name
                handler.__name__ = prompt_instance.name
                handler.__doc__ = (
                    prompt_instance.description or f"Get prompt {prompt_instance.name}"
                )
                return handler

            # Register using FastMCP's prompt decorator
            prompt_handler = make_handler(prompt)
            self.fastmcp.prompt()(prompt_handler)

        self._prompts_registered = True
        logger.info("Registered MCP prompts", count=len(prompts))

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

    async def notify_prompt_list_changed(self) -> None:
        """Notify clients about prompt list changes."""
        try:
            self.task_manager.create_task(self.current_session.send_prompt_list_changed())
        except RuntimeError:
            self.log.debug("No active session for notification")
        except Exception:
            self.log.exception("Failed to send prompt list change notification")
