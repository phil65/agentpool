"""McpRun based toolset implementation."""

import os
from typing import TYPE_CHECKING, Any, Literal, Self, cast

from mcp.types import ToolListChangedNotification
from schemez import OpenAIFunctionDefinition

from agentpool.log import get_logger
from agentpool.resource_providers import ResourceProvider


if TYPE_CHECKING:
    from collections.abc import Sequence
    from contextlib import AbstractAsyncContextManager
    from types import TracebackType

    from mcp import ClientSession
    from mcp.types import CallToolResult, ServerNotification

    from agentpool.tools.base import Tool

logger = get_logger(__name__)


class McpRunTools(ResourceProvider):
    """Provider for MCP.run tools.

    Maintains a persistent SSE connection to MCP.run to receive tool change
    notifications. Use as an async context manager to ensure proper cleanup.

    Example:
        async with McpRunTools("default") as provider:
            tools = await provider.get_tools()
    """

    kind: Literal["mcp_run"] = "mcp_run"

    def __init__(self, entity_id: str, session_id: str | None = None) -> None:
        from mcp_run import Client, ClientConfig  # type: ignore[import-untyped]

        super().__init__(name=entity_id)
        id_ = session_id or os.environ.get("MCP_RUN_SESSION_ID")
        config = ClientConfig()
        self.client = Client(session_id=id_, config=config)
        self._tools: list[Tool] | None = None
        self._session: ClientSession | None = None
        # Context manager for persistent connection
        self._mcp_client_ctx: AbstractAsyncContextManager[ClientSession] | None = None

    async def __aenter__(self) -> Self:
        """Start persistent SSE connection."""
        mcp_client = self.client.mcp_sse()
        self._mcp_client_ctx = mcp_client.connect()
        self._session = await self._mcp_client_ctx.__aenter__()
        # Set up notification handler for tool changes.
        # mcp_run constructs the ClientSession without a message_handler, so we wrap
        # the session's handler to intercept ToolListChangedNotification.
        original_handler = self._session._message_handler

        async def notification_handler(message: ServerNotification | Exception) -> None:
            if isinstance(message, ToolListChangedNotification):
                logger.info("MCP.run tool list changed notification received")
                await self._on_tools_changed()
            await original_handler(message)

        self._session._message_handler = notification_handler

        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Close persistent SSE connection."""
        if self._mcp_client_ctx:
            await self._mcp_client_ctx.__aexit__(exc_type, exc_val, exc_tb)
            self._mcp_client_ctx = None
            self._session = None

    async def _on_tools_changed(self) -> None:
        """Handle tool list change notification."""
        logger.info("MCP.run tools changed, refreshing cache")
        self._tools = None
        await self.tools_changed.emit(self.create_change_event("tools"))

    async def get_tools(self) -> Sequence[Tool]:
        """Get tools from MCP.run."""
        # Return cached tools if available
        if self._tools is not None:
            return self._tools

        self._tools = []
        for name, tool in self.client.tools.items():

            async def run(tool_name: str = name, **input_dict: Any) -> CallToolResult:
                # Read the session at call time: tools may be fetched before __aenter__
                # and outlive __aexit__.
                if (session := self._session) is not None:
                    return await session.call_tool(tool_name, arguments=input_dict)
                # Fallback to creating a new connection (when not using context manager)
                async with self.client.mcp_sse().connect() as new_session:
                    return await new_session.call_tool(tool_name, arguments=input_dict)  # type: ignore[no-any-return]

            run.__name__ = name
            schema = cast(OpenAIFunctionDefinition, tool.input_schema)
            wrapped_tool = self.create_tool(run, schema_override=schema)
            self._tools.append(wrapped_tool)
        return self._tools

    async def refresh_tools(self) -> None:
        """Manually refresh tools from MCP.run and emit change event."""
        logger.info("Manually refreshing MCP.run tools")
        self._tools = None
        await self.get_tools()
        await self.tools_changed.emit(self.create_change_event("tools"))


if __name__ == "__main__":
    import anyio

    async def main() -> None:
        tools = McpRunTools("default")
        fns = await tools.get_tools()
        print(fns)

    anyio.run(main)
