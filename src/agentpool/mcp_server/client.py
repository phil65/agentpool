"""FastMCP-based client implementation for AgentPool.

This module provides a client for communicating with MCP servers using FastMCP.
It includes support for contextual progress handlers that extend FastMCP's
standard progress callbacks with tool execution context (tool name, call ID, and input).
Elicitation is handled via a forwarding callback pattern: a stable callback is
registered at connection time, but it delegates to a mutable handler that can
be swapped per tool call (allowing AgentContext.handle_elicitation to be used).
"""

from __future__ import annotations

import contextlib
from importlib.metadata import version
import logging
from typing import TYPE_CHECKING, Any, Self, assert_never

from pydantic_ai import RunContext, ToolReturn
from schemez import FunctionSchema

from agentpool.agents.context import AgentContext
from agentpool.log import get_logger
from agentpool.mcp_server.constants import MCP_TO_LOGGING
from agentpool.mcp_server.helpers import extract_text_content, mcp_tool_to_input_schema
from agentpool.mcp_server.message_handler import MCPMessageHandler
from agentpool.tools.base import FunctionTool
from agentpool.utils.signatures import create_modified_signature
from agentpool_config.mcp_server import (
    SSEMCPServerConfig,
    StdioMCPServerConfig,
    StreamableHTTPMCPServerConfig,
)


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    import fastmcp
    from fastmcp.client import ClientTransport
    from fastmcp.client.elicitation import ElicitationHandler
    from fastmcp.client.logging import LogMessage
    from fastmcp.client.messages import MessageHandler, MessageHandlerT
    from fastmcp.client.sampling import SamplingHandler
    from mcp.shared.context import RequestContext
    from mcp.types import (
        BlobResourceContents,
        ElicitRequestParams,
        GetPromptResult,
        Icon,
        Implementation,
        Prompt as MCPPrompt,
        Resource as MCPResource,
        ResourceTemplate,
        TextResourceContents,
        Tool as MCPTool,
    )
    from upathtools.filesystems import MCPFileSystem, MCPToolsFileSystem

    from agentpool_config.mcp_server import MCPServerConfig


logger = get_logger(__name__)
MetaDict = dict[str, Any]


class MCPClient:
    """FastMCP-based client for communicating with MCP servers."""

    def __init__(
        self,
        config: MCPServerConfig,
        sampling_callback: SamplingHandler[Any, Any] | None = None,
        message_handler: MessageHandlerT | MessageHandler | None = None,
        accessible_roots: list[str] | None = None,
        tool_change_callback: Callable[[MetaDict], Awaitable[None]] | None = None,
        prompt_change_callback: Callable[[MetaDict], Awaitable[None]] | None = None,
        resource_change_callback: Callable[[MetaDict], Awaitable[None]] | None = None,
        client_name: str | None = None,
        client_title: str | None = None,
        client_website_url: str | None = None,
        client_icon_path: str | None = None,
    ) -> None:
        # Mutable handler swapped per call_tool for dynamic elicitation
        self._current_elicitation_handler: ElicitationHandler | None = None
        self.config = config
        self._sampling_callback = sampling_callback
        # Store message handler or mark for lazy creation
        self._message_handler = message_handler
        self._accessible_roots = accessible_roots or []
        self._tool_change_callback = tool_change_callback
        self._prompt_change_callback = prompt_change_callback
        self._resource_change_callback = resource_change_callback
        self._client_name = client_name
        self._client_title = client_title
        self._client_website_url = client_website_url
        self._client_icon_path = client_icon_path
        self._client = self._get_client(self.config)

    @property
    def connected(self) -> bool:
        """Check if client is connected by examining session state."""
        return self._client.is_connected()

    def _ensure_connected(self) -> None:
        """Ensure client is connected, raise RuntimeError if not."""
        if not self.connected:
            raise RuntimeError("Not connected to MCP server")

    async def __aenter__(self) -> Self:
        """Enter context manager."""
        try:
            # First attempt with configured auth
            await self._client.__aenter__()  # type: ignore[no-untyped-call]
        except Exception as first_error:
            # OAuth fallback for HTTP/SSE if not already using OAuth
            if not isinstance(self.config, StdioMCPServerConfig) and not self.config.auth.oauth:
                try:
                    with contextlib.suppress(Exception):
                        await self._client.__aexit__(None, None, None)  # type: ignore[no-untyped-call]
                    self._client = self._get_client(self.config, force_oauth=True)
                    await self._client.__aenter__()  # type: ignore[no-untyped-call]
                    logger.info("Connected with OAuth fallback")
                except Exception:  # noqa: BLE001
                    raise first_error from None
            else:
                raise

        return self

    async def __aexit__(self, *args: object) -> None:
        """Exit context manager and cleanup."""
        try:
            await self._client.__aexit__(None, None, None)  # type: ignore[no-untyped-call]
        except Exception as e:  # noqa: BLE001
            logger.warning("Error during FastMCP client cleanup", error=e)

    def get_resource_fs(self) -> MCPFileSystem:
        """Get a filesystem for accessing MCP resources."""
        from upathtools.filesystems import MCPFileSystem

        return MCPFileSystem(client=self._client)

    def get_tools_fs(self) -> MCPToolsFileSystem:
        """Get a filesystem for accessing MCP tools as code."""
        from upathtools.filesystems import MCPToolsFileSystem

        return MCPToolsFileSystem(client=self._client)

    async def _log_handler(self, message: LogMessage) -> None:
        """Handle server log messages."""
        level = MCP_TO_LOGGING.get(message.level, logging.INFO)
        logger.log(level, "MCP Server: ", data=message.data)

    async def _forwarding_elicitation_callback[T](
        self,
        message: str,
        response_type: type[T],
        params: ElicitRequestParams,
        context: RequestContext[Any, Any],
    ) -> T | dict[str, Any] | Any:
        """Forwarding callback that delegates to current handler.

        This callback is registered once at connection time, but delegates to
        _current_elicitation_handler which can be swapped per tool call.
        """
        from fastmcp.client.elicitation import ElicitResult

        # Try current handler first (set per call_tool)
        if self._current_elicitation_handler:
            return await self._current_elicitation_handler(message, response_type, params, context)
        # No handler available - decline by default
        return ElicitResult(action="decline")

    def _get_client(
        self, config: MCPServerConfig, force_oauth: bool = False
    ) -> fastmcp.Client[Any]:
        """Create FastMCP client based on config."""
        import fastmcp
        from fastmcp.client import SSETransport, StreamableHttpTransport
        from fastmcp.client.transports import StdioTransport
        from mcp.types import Icon, Implementation

        transport: ClientTransport
        # Create transport based on config type
        match config:
            case StdioMCPServerConfig(command=command, args=args):
                env = config.get_env_vars()
                transport = StdioTransport(command=command, args=args, env=env)
                oauth = False
                if force_oauth:
                    raise ValueError("OAuth is not supported for StdioMCPServerConfig")

            case SSEMCPServerConfig(url=url, headers=headers, auth=auth):
                transport = SSETransport(url=url, headers=headers)
                oauth = auth.oauth

            case StreamableHTTPMCPServerConfig(url=url, headers=headers, auth=auth):
                transport = StreamableHttpTransport(url=url, headers=headers)
                oauth = auth.oauth
            case _ as unreachable:
                assert_never(unreachable)

        # Create message handler if needed
        msg_handler = self._message_handler or MCPMessageHandler(
            self,
            self._tool_change_callback,
            self._prompt_change_callback,
            self._resource_change_callback,
        )

        # Build client_info if client_name is provided
        client_info: Implementation | None = None
        if self._client_name:
            icons: list[Icon] | None = None
            if self._client_icon_path:
                icons = [Icon(src=self._client_icon_path)]
            client_info = Implementation(
                name=self._client_name,
                version=version("agentpool"),
                title=self._client_title,
                websiteUrl=self._client_website_url,
                icons=icons,
            )

        return fastmcp.Client(
            transport,
            log_handler=self._log_handler,
            roots=self._accessible_roots,
            timeout=config.timeout,
            elicitation_handler=self._forwarding_elicitation_callback,
            sampling_handler=self._sampling_callback,
            message_handler=msg_handler,
            auth="oauth" if (force_oauth or oauth) else None,
            client_info=client_info,
        )

    async def list_tools(self) -> list[MCPTool]:
        """Get available enabled tools directly from the server."""
        self._ensure_connected()
        try:
            tools = await self._client.list_tools()
            filtered = [t for t in tools if self.config.is_tool_allowed(t.name)]
            logger.debug("Listed tools from MCP server", total=len(tools), filtered=len(filtered))
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to list tools", error=e)
            return []
        else:
            return filtered

    async def list_prompts(self) -> list[MCPPrompt]:
        """Get available prompts from the server."""
        self._ensure_connected()
        try:
            return await self._client.list_prompts()
        except Exception as e:  # noqa: BLE001
            logger.debug("Failed to list prompts", error=e)
            return []

    async def list_resources(self) -> list[MCPResource]:
        """Get available resources from the server."""
        self._ensure_connected()
        try:
            return await self._client.list_resources()
        except Exception as e:
            raise RuntimeError(f"Failed to list resources: {e}") from e

    async def list_resource_templates(self) -> list[ResourceTemplate]:
        """Get available resource templates from the server.

        Resource templates are URI patterns with placeholders that can be
        expanded to create concrete resource URIs.

        Example template: "file:///{path}" -> expand with path="config.json"
        -> "file:///config.json" which can then be read.

        TODO: Integrate resource templates into the ResourceInfo system.
        Currently templates are separate from resources - we need to decide:
        - Should templates appear in list_resources() with a flag?
        - Should ResourceInfo.read() accept kwargs for template expansion?
        - Should templates have their own ResourceTemplateInfo class?

        Returns:
            List of resource templates from the server
        """
        self._ensure_connected()
        try:
            return await self._client.list_resource_templates()
        except Exception as e:
            raise RuntimeError(f"Failed to list resource templates: {e}") from e

    async def read_resource(self, uri: str) -> list[TextResourceContents | BlobResourceContents]:
        """Read resource content by URI.

        Args:
            uri: URI of the resource to read

        Returns:
            List of resource contents (text or blob)

        Raises:
            RuntimeError: If not connected or read fails
        """
        self._ensure_connected()
        try:
            return await self._client.read_resource(uri)
        except Exception as e:
            raise RuntimeError(f"Failed to read resource {uri!r}: {e}") from e

    async def get_prompt(
        self, name: str, arguments: dict[str, str] | None = None
    ) -> GetPromptResult:
        """Get a specific prompt's content."""
        self._ensure_connected()
        try:
            return await self._client.get_prompt_mcp(name, arguments)
        except Exception as e:
            raise RuntimeError(f"Failed to get prompt {name!r}: {e}") from e

    def convert_tool(self, tool: MCPTool) -> FunctionTool:
        """Create a properly typed callable from MCP tool schema."""
        from agentpool_config.tools import ToolHints

        async def tool_callable(
            ctx: RunContext, agent_ctx: AgentContext[Any], **kwargs: Any
        ) -> str | Any | ToolReturn:
            """Dynamically generated MCP tool wrapper."""
            # Filter out None values for optional params
            schema_props = tool.inputSchema.get("properties", {})
            required_props = set(tool.inputSchema.get("required", []))
            filtered_kwargs = {
                k: v
                for k, v in kwargs.items()
                if k in required_props or (k in schema_props and v is not None)
            }
            return await self.call_tool(tool.name, ctx, filtered_kwargs, agent_ctx)

        # Set proper signature and annotations with both RunContext and AgentContext
        schema = mcp_tool_to_input_schema(tool)
        fn_schema = FunctionSchema.from_dict(schema, output_schema=tool.outputSchema)
        sig = fn_schema.to_python_signature()
        tool_callable.__signature__ = create_modified_signature(  # type: ignore[attr-defined]  # ty:ignore[unresolved-attribute]
            sig, inject={"ctx": RunContext, "agent_ctx": AgentContext}
        )
        annotations = fn_schema.get_annotations()
        annotations["ctx"] = RunContext
        annotations["agent_ctx"] = AgentContext
        # Update return annotation to support multiple types
        if not tool.outputSchema:
            annotations["return"] = str | Any | ToolReturn  # type: ignore[assignment]  # ty:ignore[invalid-assignment]
        tool_callable.__annotations__ = annotations
        tool_callable.__name__ = tool.name
        tool_callable.__doc__ = tool.description or "No description provided."
        hints = ToolHints.from_mcp(tool.annotations) if tool.annotations else None
        return FunctionTool.from_callable(tool_callable, source="mcp", hints=hints)

    async def call_tool(
        self,
        name: str,
        run_context: RunContext,
        arguments: dict[str, Any] | None = None,
        agent_ctx: AgentContext[Any] | None = None,
    ) -> ToolReturn | str | Any:
        """Call an MCP tool with full PydanticAI return type support."""
        from agentpool.mcp_server.conversions import from_mcp_content

        self._ensure_connected()
        # Create progress handler that bridges to AgentContext if available
        progress_handler = None
        if agent_ctx:

            async def fastmcp_progress_handler(
                progress: float,
                total: float | None,
                message: str | None,
            ) -> None:
                await agent_ctx.report_progress(progress, total, message or "")

            progress_handler = fastmcp_progress_handler

        # Set up per-call elicitation handler from AgentContext
        if agent_ctx:

            async def elicitation_handler[T](
                message: str,
                response_type: type[T] | None,
                params: ElicitRequestParams,
                context: RequestContext[Any, Any, Any],
            ) -> T | dict[str, Any] | Any:
                from fastmcp.client.elicitation import ElicitResult
                from mcp.types import ElicitResult as MCPElicitResult, ErrorData

                result = await agent_ctx.handle_elicitation(params)
                match result:
                    case MCPElicitResult(action="accept", content=content):
                        return content
                    case MCPElicitResult(action="cancel"):
                        return ElicitResult(action="cancel")
                    case MCPElicitResult(action="decline"):
                        return ElicitResult(action="decline")
                    case ErrorData(code=code, message=message, data=data):
                        logger.error("Elicitation error", code=code, message=message, data=data)
                        return ElicitResult(action="decline")
                    case _:
                        return ElicitResult(action="decline")

            self._current_elicitation_handler = elicitation_handler

        # Prepare metadata to pass tool_call_id to the MCP server
        meta = None
        # Use the same key that tool_bridge expects: "claudecode/toolUseId"
        # Ensure it's a string (handles both real values and mocks)
        if (
            agent_ctx
            and (ctx_id := agent_ctx.tool_call_id)
            and (tool_call_id := (str(ctx_id) if ctx_id else None))
        ):
            meta = {"claudecode/toolUseId": tool_call_id}

        try:
            result = await self._client.call_tool(
                name, arguments, progress_handler=progress_handler, meta=meta
            )
            content = await from_mcp_content(result.content)
            # Decision logic for return type
            if result.data is not None and content:
                return ToolReturn(return_value=result.data, content=content)
            if result.data is not None:
                return result.data
            if content:
                return ToolReturn(return_value="Tool executed successfully", content=content)
            return extract_text_content(result.content)
        except Exception as e:
            raise RuntimeError(f"MCP tool call failed: {e}") from e
        finally:
            # Clear per-call handler
            self._current_elicitation_handler = None


if __name__ == "__main__":
    import anyio

    path = "/home/phil65/dev/oss/agentpool/tests/mcp_server/server.py"
    # path = Path(__file__).parent / "test_mcp_server.py"
    config = StdioMCPServerConfig(command="uv", args=["run", str(path)])

    async def main() -> None:
        async with MCPClient(config=config) as mcp_client:
            # Create MCP filesystem
            fs = mcp_client.get_resource_fs()
            print(await fs._ls(""))

    anyio.run(main)
