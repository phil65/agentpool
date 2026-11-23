"""FastMCP-based client implementation for AgentPool.

This module provides a client for communicating with MCP servers using FastMCP.
It includes support for contextual progress handlers that extend FastMCP's
standard progress callbacks with tool execution context (tool name, call ID, and input).
Elicitation is handled via a forwarding callback pattern: a stable callback is
registered at connection time, but it delegates to a mutable handler that can
be swapped per tool call (allowing AgentContext.handle_elicitation to be used).
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import functools
import importlib
from importlib.metadata import version
import importlib.util
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
    from fastmcp.client._sdk_context_shim import RequestContext
    from fastmcp.client.elicitation import ElicitationHandler
    from fastmcp.client.logging import LogMessage
    from fastmcp.client.messages import MessageHandler, MessageHandlerT
    from fastmcp.skills import MCPSkill
    from mcp.client.extension import ClientExtension
    from mcp.shared.subscriptions import ServerEvent
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

    from agentpool.ui.base import InputProvider
    from agentpool_config.mcp_server import MCPServerConfig


logger = get_logger(__name__)

MAX_LISTEN_RECONNECTS = 5
"""Times a lost change stream is reopened in a row before giving up."""


@functools.cache
def _enable_task_support() -> None:
    """Let clients drive servers' background tasks (SEP-2663) if fastmcp-tasks is installed.

    Importing ``fastmcp_tasks`` registers its client half for every FastMCP client
    created afterwards: tools a server runs only as tasks can then be called, and
    ``call_tool`` waits for their result.
    """
    if importlib.util.find_spec("fastmcp_tasks") is not None:
        importlib.import_module("fastmcp_tasks")


MetaDict = dict[str, Any]


class MCPClient:
    """FastMCP-based client for communicating with MCP servers."""

    def __init__(
        self,
        config: MCPServerConfig,
        message_handler: MessageHandlerT | MessageHandler | None = None,
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
        # Input providers that handled URL elicitations, to route completion notifications
        self._url_elicitations: dict[str, InputProvider] = {}
        self.config = config
        # Store message handler or mark for lazy creation
        self._message_handler = message_handler
        self._tool_change_callback = tool_change_callback
        self._prompt_change_callback = prompt_change_callback
        self._resource_change_callback = resource_change_callback
        self._client_name = client_name
        self._client_title = client_title
        self._client_website_url = client_website_url
        self._client_icon_path = client_icon_path
        self._client = self._get_client(self.config)
        self._listen_task: asyncio.Task[None] | None = None

    @property
    def fastmcp_client(self) -> fastmcp.Client[Any]:
        """The underlying FastMCP client (for extensions this class doesn't wrap)."""
        self._ensure_connected()
        return self._client

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
        self._start_listening()
        return self

    async def __aexit__(self, *args: object) -> None:
        """Exit context manager and cleanup."""
        if (task := self._listen_task) is not None:
            self._listen_task = None
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        try:
            await self._client.__aexit__(None, None, None)  # type: ignore[no-untyped-call]
        except Exception as e:  # noqa: BLE001
            logger.warning("Error during FastMCP client cleanup", error=e)

    def _start_listening(self) -> None:
        """Follow the server's list changes on a ``subscriptions/listen`` stream.

        On 2026-07-28 connections a change a server announces outside a request (say,
        tools reloaded in the background) reaches only a stream the client opens.
        Notifications sent within a request, and every notification on earlier protocol
        versions, go to the message handler as before. Only the changes someone handles
        are asked for.
        """
        wanted = (
            self._tool_change_callback,
            self._prompt_change_callback,
            self._resource_change_callback,
        )
        if any(callback is not None for callback in wanted):
            name = f"mcp-listen-{self.config.client_id}"
            self._listen_task = asyncio.create_task(self._listen_for_changes(), name=name)

    async def _listen_for_changes(self) -> None:
        from mcp.client.subscriptions import ListenNotSupportedError, SubscriptionLost

        lost = 0
        while True:
            try:
                async with self._client.listen(
                    tools_list_changed=self._tool_change_callback is not None,
                    prompts_list_changed=self._prompt_change_callback is not None,
                    resources_list_changed=self._resource_change_callback is not None,
                ) as subscription:
                    lost = 0
                    async for event in subscription:
                        await self._on_change(event)
            except ListenNotSupportedError:
                return  # Handshake-era connection: the message handler gets the changes
            except SubscriptionLost:
                lost += 1
                if lost > MAX_LISTEN_RECONNECTS:
                    logger.warning("MCP change stream lost for good", server=self.config.client_id)
                    return
                await self._on_all_changed()  # Changes may have been missed: refetch
                await asyncio.sleep(min(2**lost, 30))
            except Exception:
                logger.exception("MCP change stream failed", server=self.config.client_id)
                return
            else:
                return  # The server closed the stream gracefully

    async def _on_change(self, event: ServerEvent) -> None:
        from mcp.shared.subscriptions import (
            PromptsListChanged,
            ResourcesListChanged,
            ResourceUpdated,
            ToolsListChanged,
        )

        match event:
            case ToolsListChanged() if (callback := self._tool_change_callback) is not None:
                logger.info("MCP tool list changed", server=self.config.client_id)
                await callback({})
            case PromptsListChanged() if (callback := self._prompt_change_callback) is not None:
                logger.info("MCP prompt list changed", server=self.config.client_id)
                await callback({})
            case ResourcesListChanged() if (callback := self._resource_change_callback) is not None:
                logger.info("MCP resource list changed", server=self.config.client_id)
                await callback({})
            case ResourceUpdated(uri=uri):
                logger.info("MCP resource updated", server=self.config.client_id, uri=uri)
            case _:
                pass

    async def _on_all_changed(self) -> None:
        for callback in (
            self._tool_change_callback,
            self._prompt_change_callback,
            self._resource_change_callback,
        ):
            if callback is not None:
                await callback({})

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

    async def complete_url_elicitation(self, elicitation_id: str) -> None:
        """Forward an MCP URL elicitation completion to the provider that handled it."""
        if provider := self._url_elicitations.pop(elicitation_id, None):
            await provider.elicitation_complete(elicitation_id)

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
                website_url=self._client_website_url,
                icons=icons,
            )

        _enable_task_support()
        extensions: list[ClientExtension] = []
        if config.skills:
            from fastmcp.skills import SkillsClientExtension

            extensions.append(SkillsClientExtension())
        if config.events:
            from fastmcp.experimental.events import EventsClientExtension

            extensions.append(EventsClientExtension())

        return fastmcp.Client(
            transport,
            extensions=extensions,
            log_handler=self._log_handler,
            timeout=config.timeout,
            elicitation_handler=self._forwarding_elicitation_callback,
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

    async def read_resource_bytes(self, uri: str) -> bytes:
        """Read a resource and return its first content item as bytes.

        Text content is encoded as UTF-8; blob content is base64-decoded.

        Args:
            uri: URI of the resource to read

        Raises:
            RuntimeError: If not connected or read fails
            FileNotFoundError: If the server returned no content
        """
        from mcp.types import BlobResourceContents, TextResourceContents

        contents = await self.read_resource(uri)
        if not contents:
            raise FileNotFoundError(f"Resource {uri!r} has no content")
        match contents[0]:
            case TextResourceContents(text=text):
                return text.encode("utf-8")
            case BlobResourceContents(blob=blob):
                return base64.b64decode(blob)

    @property
    def server_name(self) -> str | None:
        """Name the connected server reports for itself, if any."""
        info = self._client.server_info if self.connected else None
        return info.name if info and info.name else None

    @property
    def supports_skills(self) -> bool:
        """Whether skills were negotiated: enabled in config and advertised by the server."""
        from fastmcp.skills import SkillsClientExtension

        if not self.config.skills or not self.connected:
            return False
        capabilities = self._client.server_capabilities
        extensions = capabilities.extensions if capabilities else None
        return extensions is not None and SkillsClientExtension.identifier in extensions

    async def list_skills(self) -> list[MCPSkill]:
        """List the skills the server publishes through the Skills extension.

        Returns:
            Every skill entry across all pages, or an empty list when the
            extension was not negotiated.

        Raises:
            RuntimeError: If listing fails
        """
        self._ensure_connected()
        if not self.supports_skills:
            return []
        try:
            return await self._client.list_skills()
        except Exception as e:
            raise RuntimeError(f"Failed to list skills: {e}") from e

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
            schema_props = tool.input_schema.get("properties", {})
            required_props = set(tool.input_schema.get("required", []))
            filtered_kwargs = {
                k: v
                for k, v in kwargs.items()
                if k in required_props or (k in schema_props and v is not None)
            }
            return await self.call_tool(tool.name, ctx, filtered_kwargs, agent_ctx)

        # Set proper signature and annotations with both RunContext and AgentContext
        schema = mcp_tool_to_input_schema(tool)
        fn_schema = FunctionSchema.from_dict(schema, output_schema=tool.output_schema)
        sig = fn_schema.to_python_signature()
        tool_callable.__signature__ = create_modified_signature(  # type: ignore[attr-defined]  # ty:ignore[missing-attribute]
            sig, inject={"ctx": RunContext, "agent_ctx": AgentContext}
        )
        annotations = fn_schema.get_annotations()
        annotations["ctx"] = RunContext
        annotations["agent_ctx"] = AgentContext
        # Update return annotation to support multiple types
        if not tool.output_schema:
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
                context: RequestContext[Any, Any],
            ) -> T | dict[str, Any] | Any:
                from fastmcp.client.elicitation import ElicitResult
                from mcp.types import (
                    ElicitRequestURLParams,
                    ElicitResult as MCPElicitResult,
                    ErrorData,
                )

                if isinstance(params, ElicitRequestURLParams) and params.elicitation_id:
                    provider = agent_ctx.get_input_provider()
                    self._url_elicitations[params.elicitation_id] = provider
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
