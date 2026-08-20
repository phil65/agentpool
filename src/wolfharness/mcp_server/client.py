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
from typing import TYPE_CHECKING, Any, Self

import anyio
from fastmcp.exceptions import ClientError, FastMCPError
from mcp.shared.exceptions import McpError
from pydantic_ai import BinaryContent, RunContext, ToolReturn
from schemez import FunctionSchema

from wolfharness.agents.context import AgentContext
from wolfharness.log import get_logger
from wolfharness.mcp_server.constants import MCP_TO_LOGGING
from wolfharness.mcp_server.helpers import extract_text_content, mcp_tool_to_fn_schema
from wolfharness.mcp_server.message_handler import MCPMessageHandler
from wolfharness.tools import CallDeferred
from wolfharness.tools.base import FunctionTool
from wolfharness.utils.signatures import create_modified_signature
from wolfharness_config.mcp_server import (
    AcpMCPServerConfig,
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
        Completion,
        ElicitRequestParams,
        GetPromptResult,
        Icon,
        Implementation,
        ListResourcesResult,
        ListResourceTemplatesResult,
        Prompt as MCPPrompt,
        Resource as MCPResource,
        ResourceTemplate,
        TextResourceContents,
        Tool as MCPTool,
    )
    from upathtools.filesystems import MCPFileSystem, MCPToolsFileSystem

    from wolfharness_config.mcp_server import MCPServerConfig


logger = get_logger(__name__)


class MCPClient:
    """FastMCP-based client for communicating with MCP servers."""

    def __init__(
        self,
        config: MCPServerConfig,
        sampling_callback: SamplingHandler[Any, Any] | None = None,
        message_handler: MessageHandlerT | MessageHandler | None = None,
        accessible_roots: list[str] | None = None,
        tool_change_callback: Callable[[], Awaitable[None]] | None = None,
        prompt_change_callback: Callable[[], Awaitable[None]] | None = None,
        resource_list_changed_callback: Callable[[], Awaitable[None]] | None = None,
        resource_updated_callback: Callable[[str], Awaitable[None]] | None = None,
        client_name: str | None = None,
        client_title: str | None = None,
        client_website_url: str | None = None,
        client_icon_path: str | None = None,
        transport: ClientTransport | None = None,
    ) -> None:
        # Mutable handler swapped per call_tool for dynamic elicitation
        self._current_elicitation_handler: ElicitationHandler | None = None
        self.config = config
        self._sampling_callback = sampling_callback
        # Store message handler or mark for lazy creation
        self._message_handler = message_handler
        # Lazily-created wolfharness message handler (see _get_message_handler).
        self._wolfharness_message_handler: MCPMessageHandler | None = None
        self._accessible_roots = accessible_roots or []
        self._tool_change_callback = tool_change_callback
        self._prompt_change_callback = prompt_change_callback
        self._resource_list_changed_callback = resource_list_changed_callback
        self._resource_updated_callback = resource_updated_callback
        self._client_name = client_name
        self._client_title = client_title
        self._client_website_url = client_website_url
        self._client_icon_path = client_icon_path
        # If a pre-built transport is provided (e.g. AcpMcpTransport), use it directly.
        # Otherwise build the client from config as usual.
        self._external_transport: ClientTransport | None = None
        if transport is not None:
            self._external_transport = transport
            self._client = self._get_client_from_transport(transport)
        else:
            self._client = self._get_client(self.config)

    @property
    def connected(self) -> bool:
        """Check if client is connected by examining session state."""
        return self._client.is_connected()

    def set_notification_callbacks(
        self,
        *,
        tool_change_callback: Callable[[], Awaitable[None]] | None = None,
        prompt_change_callback: Callable[[], Awaitable[None]] | None = None,
        resource_list_changed_callback: Callable[[], Awaitable[None]] | None = None,
        resource_updated_callback: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        """Set server-notification callbacks after the client is connected.

        The ``MCPMessageHandler`` reads these callbacks dynamically from the
        client on each notification (rather than snapshotting them at
        construction), so callbacks set here apply immediately even if the
        message handler was already created.
        """
        self._tool_change_callback = tool_change_callback
        self._prompt_change_callback = prompt_change_callback
        self._resource_list_changed_callback = resource_list_changed_callback
        self._resource_updated_callback = resource_updated_callback

    def _get_message_handler(self) -> MessageHandlerT | MessageHandler:
        """Return the wolfharness message handler for this client.

        The handler reads notification callbacks dynamically from the client
        (see ``set_notification_callbacks``), so it can be created any time
        after construction and still see the latest callbacks.
        """
        if self._message_handler is not None:
            return self._message_handler
        if self._wolfharness_message_handler is None:
            self._wolfharness_message_handler = MCPMessageHandler(
                self,
                self._tool_change_callback,
                self._prompt_change_callback,
                self._resource_list_changed_callback,
                self._resource_updated_callback,
            )
        return self._wolfharness_message_handler

    @property
    def server_info(self) -> dict[str, str] | None:
        """Get server info (name and version) from the connected client.

        Returns a dict with ``name`` and ``version`` keys from the MCP
        ``initialize`` result's ``serverInfo`` field, or ``None`` if the
        initialize result is not yet available (e.g. before ``__aenter__``
        has completed).

        Does NOT trigger a connection — reads from the already-connected
        client's cached initialize result.
        """
        try:
            init_result = self._client.initialize_result
        except RuntimeError:
            # Session not active yet (FastMCP raises if session state is
            # accessed before __aenter__ completes).
            return None
        if init_result is None:
            return None
        info = init_result.serverInfo
        if info is None:
            return None
        return {"name": info.name, "version": info.version}

    def _ensure_connected(self) -> None:
        """Ensure client is connected, raise RuntimeError if not."""
        if not self.connected:
            raise RuntimeError("Not connected to MCP server")

    def _has_server_capability(self, capability: str) -> bool:
        """Check if the server declared a specific capability during initialization.

        Args:
            capability: Capability field name (e.g. 'resources', 'prompts', 'tools').

        Returns:
            True if the server declared this capability, False otherwise.
            Returns False if not yet connected or not yet initialized.
        """
        try:
            caps = self._client.session.get_server_capabilities()
        except RuntimeError:
            return False
        if caps is None:
            return False
        return getattr(caps, capability, None) is not None

    def supports_resources(self) -> bool:
        """Return whether the connected server advertised ``resources``."""
        self._ensure_connected()
        return self._has_server_capability("resources")

    async def __aenter__(self) -> Self:
        """Enter context manager."""
        try:
            # First attempt with configured auth
            await self._client.__aenter__()  # type: ignore[no-untyped-call]
        except Exception as first_error:
            # OAuth fallback for HTTP/SSE if not already using OAuth
            if (
                not isinstance(self.config, (StdioMCPServerConfig, AcpMCPServerConfig))
                and not self.config.auth.oauth
            ):
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

        # When a shared transport (e.g. SessionConnectionPool's stdio
        # owner-task) pre-connects before this MCPClient exists, fastmcp
        # reuses that session and our MCPMessageHandler is never bound.
        # Rebind so server notifications reach wolfharness callbacks.
        self._rebind_session_message_handler()
        return self

    async def __aexit__(self, *args: object) -> None:
        """Exit context manager and cleanup."""
        try:
            await self._client.__aexit__(None, None, None)  # type: ignore[no-untyped-call]
        except Exception as e:  # noqa: BLE001
            logger.warning("Error during FastMCP client cleanup", error=e)

    def _rebind_session_message_handler(self) -> None:
        """Rebind the underlying session's message handler to wolfharness's.

        ``SessionConnectionPool`` pre-connects stdio transports inside an
        owner task *before* this ``MCPClient`` exists, so fastmcp reuses
        that session and our ``MCPMessageHandler`` would otherwise never be
        bound. mcp SDK sessions read ``_message_handler`` dynamically for
        each notification, so setting it here takes effect immediately.
        """
        if not self._client.is_connected():
            return
        session = self._client.session
        handler = self._get_message_handler()
        if getattr(session, "_message_handler", None) is not handler:
            session._message_handler = handler

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

        **Critical**: This method must NEVER return ``mcp.types.ElicitResult``
        (``MCPElicitResult``). FastMCP's ``create_elicitation_callback`` wrapper
        checks ``isinstance(result, fastmcp.client.elicitation.ElicitResult)``
        — and since ``MCPElicitResult`` is NOT a subclass of fastmcp's
        ``ElicitResult``, the wrapper would wrap the entire ``MCPElicitResult``
        object as ``content``, producing ``{"content": {"_meta": null, "content": null}}``
        on the wire. This causes Zod validation errors on the MCP server side.
        """
        from fastmcp.client.elicitation import ElicitResult
        from mcp.types import ElicitResult as MCPElicitResult

        # Try current handler first (set per call_tool)
        if self._current_elicitation_handler:
            result = await self._current_elicitation_handler(
                message, response_type, params, context
            )
            # Safety net: if the per-call handler accidentally returns
            # MCPElicitResult (which is NOT fastmcp's ElicitResult),
            # extract the content or convert to fastmcp ElicitResult
            # before returning to fastmcp's wrapper.
            if isinstance(result, MCPElicitResult) and not isinstance(result, ElicitResult):
                logger.warning(
                    "Elicitation handler returned MCPElicitResult instead of"
                    " raw content or fastmcp ElicitResult — converting",
                    action=result.action,
                    content=result.content,
                )
                match result.action:
                    case "accept":
                        return result.content
                    case "decline" | "cancel":
                        return ElicitResult(action=result.action)
                    case _:
                        return ElicitResult(action="decline")
            return result
        # No handler available - decline by default
        return ElicitResult(action="decline")

    def _get_client(
        self, config: MCPServerConfig, force_oauth: bool = False
    ) -> fastmcp.Client[Any]:
        """Create FastMCP client based on config."""
        import fastmcp
        from mcp.types import Icon, Implementation

        # ACP configs are not handled here — they use AcpMcpConnectionManager.
        if isinstance(config, AcpMCPServerConfig):
            raise NotImplementedError(
                "ACP-transport MCP servers are managed by the ACP agent directly. "
                "Use AcpMcpConnectionManager to establish connections."
            )

        # Use shared to_transport() method from config classes.
        transport = config.to_transport(force_oauth=force_oauth)

        # Determine oauth flag for Client auth parameter.
        oauth = False
        if isinstance(config, (SSEMCPServerConfig, StreamableHTTPMCPServerConfig)):
            oauth = config.auth.oauth

        # Create message handler if needed
        msg_handler = self._get_message_handler()

        # Build client_info if client_name is provided
        client_info: Implementation | None = None
        if self._client_name:
            icons: list[Icon] | None = None
            if self._client_icon_path:
                icons = [Icon(src=self._client_icon_path)]
            client_info = Implementation(
                name=self._client_name,
                version=version("wolfharness"),
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

    def _get_client_from_transport(self, transport: ClientTransport) -> fastmcp.Client[Any]:
        """Create a FastMCP client from a pre-built transport (e.g. AcpMcpTransport).

        This bypasses config-based transport creation and is used for ACP-transport
        MCP servers where the transport is built externally.
        """
        import fastmcp
        from mcp.types import Icon, Implementation

        msg_handler = self._get_message_handler()

        client_info: Implementation | None = None
        if self._client_name:
            icons: list[Icon] | None = None
            if self._client_icon_path:
                icons = [Icon(src=self._client_icon_path)]
            client_info = Implementation(
                name=self._client_name,
                version=version("wolfharness"),
                title=self._client_title,
                websiteUrl=self._client_website_url,
                icons=icons,
            )

        return fastmcp.Client(
            transport,
            log_handler=self._log_handler,
            roots=self._accessible_roots,
            timeout=self.config.timeout,
            elicitation_handler=self._forwarding_elicitation_callback,
            sampling_handler=self._sampling_callback,
            message_handler=msg_handler,
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
        if not self._has_server_capability("prompts"):
            return []
        try:
            return await self._client.list_prompts()
        except Exception as e:  # noqa: BLE001
            logger.debug("Failed to list prompts", error=e)
            return []

    async def list_resources(self) -> list[MCPResource]:
        """Get available resources from the server."""
        self._ensure_connected()
        if not self._has_server_capability("resources"):
            return []
        try:
            return await self._client.list_resources()
        except (ClientError, FastMCPError, McpError, OSError, RuntimeError, ValueError) as e:
            raise RuntimeError(f"Failed to list resources: {e}") from e

    async def list_resources_page(self, cursor: str | None = None) -> ListResourcesResult:
        """Return one MCP ``resources/list`` page without auto-pagination."""
        self._ensure_connected()
        if not self._has_server_capability("resources"):
            raise RuntimeError("MCP server does not support resources")
        try:
            return await self._client.list_resources_mcp(cursor=cursor)
        except Exception as e:
            raise RuntimeError(f"Failed to list resources: {e}") from e

    async def list_resource_templates(self) -> list[ResourceTemplate]:
        """Get available resource templates from the server.

        Resource templates are URI patterns with placeholders that can be
        expanded to create concrete resource URIs.

        Example template: "file:///{path}" -> expand with path="config.json"
        -> "file:///config.json" which can then be read.

        Returns:
            List of resource templates from the server
        """
        self._ensure_connected()
        if not self._has_server_capability("resources"):
            return []
        try:
            return await self._client.list_resource_templates()
        except (ClientError, FastMCPError, McpError, OSError, RuntimeError, ValueError) as e:
            raise RuntimeError(f"Failed to list resource templates: {e}") from e

    async def list_resource_templates_page(
        self, cursor: str | None = None
    ) -> ListResourceTemplatesResult:
        """Return one MCP ``resources/templates/list`` page without auto-pagination."""
        self._ensure_connected()
        if not self._has_server_capability("resources"):
            raise RuntimeError("MCP server does not support resources")
        try:
            return await self._client.list_resource_templates_mcp(cursor=cursor)
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

    async def subscribe_resource(self, uri: str) -> None:
        """Subscribe to updates for a specific resource.

        Uses the low-level ``ClientSession.subscribe_resource()`` since
        FastMCP's high-level ``Client`` does not wrap this method. After
        subscribing, the server will send ``notifications/resources/updated``
        when the resource content changes.

        Args:
            uri: The URI of the resource to subscribe to.
        """
        from pydantic import AnyUrl

        self._ensure_connected()
        session = self._client.session
        await session.subscribe_resource(AnyUrl(uri))

    async def unsubscribe_resource(self, uri: str) -> None:
        """Unsubscribe from updates for a specific resource.

        Args:
            uri: The URI of the resource to unsubscribe from.
        """
        from pydantic import AnyUrl

        self._ensure_connected()
        session = self._client.session
        await session.unsubscribe_resource(AnyUrl(uri))

    async def complete(
        self,
        ref_type: str,
        ref_uri: str,
        argument_name: str,
        argument_value: str,
        context: dict[str, str] | None = None,
    ) -> Completion:
        """Send a completion request to the MCP server.

        Wraps FastMCP's ``Client.complete()`` to provide a simpler interface
        for resource template parameter completion.

        Args:
            ref_type: The reference type — ``"ref/resource"`` for resource
                templates or ``"ref/prompt"`` for prompt arguments.
            ref_uri: The URI (for resource templates) or name (for prompts)
                of the reference to complete.
            argument_name: The name of the argument being completed.
            argument_value: The current value of the argument.
            context: Optional context arguments for the completion request.

        Returns:
            ``mcp.types.Completion`` with completion values.

        Raises:
            RuntimeError: If not connected or the completion request fails.
            ValueError: If ``ref_type`` is not ``"ref/resource"`` or
                ``"ref/prompt"``.
        """
        import mcp.types

        self._ensure_connected()

        match ref_type:
            case "ref/resource":
                ref: mcp.types.ResourceTemplateReference | mcp.types.PromptReference = (
                    mcp.types.ResourceTemplateReference(type="ref/resource", uri=ref_uri)
                )
            case "ref/prompt":
                ref = mcp.types.PromptReference(type="ref/prompt", name=ref_uri)
            case _:
                raise ValueError(
                    f"Invalid ref_type {ref_type!r}: expected 'ref/resource' or 'ref/prompt'"
                )

        argument = mcp.types.CompletionArgument(name=argument_name, value=argument_value)

        try:
            return await self._client.complete(
                ref=ref,
                argument=argument.model_dump(),
                context_arguments=context,
            )
        except Exception as e:
            raise RuntimeError(f"Failed to complete {ref_type} {ref_uri!r}: {e}") from e

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
        schema = mcp_tool_to_fn_schema(tool)
        fn_schema = FunctionSchema.from_dict(schema)
        sig = fn_schema.to_python_signature()
        tool_callable.__signature__ = create_modified_signature(  # type: ignore[attr-defined]
            sig, inject={"ctx": RunContext, "agent_ctx": AgentContext}
        )
        annotations = fn_schema.get_annotations()
        annotations["ctx"] = RunContext
        annotations["agent_ctx"] = AgentContext
        # Update return annotation to support multiple types
        annotations["return"] = str | Any | ToolReturn  # type: ignore[assignment]
        tool_callable.__annotations__ = annotations
        tool_callable.__name__ = tool.name
        tool_callable.__doc__ = tool.description or "No description provided."
        return FunctionTool.from_callable(
            tool_callable,
            source="mcp",
            schema_override=schema,  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
        )

    async def call_tool(  # noqa: PLR0915
        self,
        name: str,
        run_context: RunContext,
        arguments: dict[str, Any] | None = None,
        agent_ctx: AgentContext[Any] | None = None,
    ) -> ToolReturn | str | Any:
        """Call an MCP tool with full PydanticAI return type support."""
        from wolfharness.mcp_server.conversions import from_mcp_content

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

                try:
                    result = await agent_ctx.handle_elicitation(params)
                except CallDeferred as exc:
                    # FastMCP's callback wrapper catches exceptions, so
                    # CallDeferred cannot propagate from here. Store the
                    # elicitation params in the side-channel and return a
                    # sentinel "decline" so FastMCP sees a normal decline.
                    # MCPClient.call_tool() will check the side-channel
                    # after the MCP call returns and re-raise CallDeferred.
                    logger.info(
                        "MCP elicitation_handler: CallDeferred raised",
                        tool_name=agent_ctx.tool_name,
                        tool_call_id=agent_ctx.tool_call_id,
                        deferred_kind="elicitation",
                    )
                    metadata = exc.metadata or {}
                    agent_ctx._pending_elicitation_deferral = metadata.get("elicitation")
                    return ElicitResult(action="decline")
                match result:
                    case MCPElicitResult(action="accept", content=content):
                        return content
                    case MCPElicitResult(action="cancel"):
                        return ElicitResult(action="cancel")
                    case MCPElicitResult(action="decline"):
                        return ElicitResult(action="decline")
                    case ErrorData():
                        return ElicitResult(action="decline")
                    case _:
                        return ElicitResult(action="decline")

            self._current_elicitation_handler = elicitation_handler

        # Prepare metadata to pass tool_call_id to the MCP server
        meta = None
        if agent_ctx and agent_ctx.tool_call_id:
            # Use the same key that tool_bridge expects: "claudecode/toolUseId"
            # Ensure it's a string (handles both real values and mocks)
            tool_call_id = str(agent_ctx.tool_call_id) if agent_ctx.tool_call_id else None
            if tool_call_id:
                meta = {"claudecode/toolUseId": tool_call_id}

        try:
            # Mark that we're inside an MCP callback so handle_elicitation()
            # knows to raise CallDeferred (FastMCP callbacks can't await
            # futures for long periods).
            if agent_ctx:
                agent_ctx.in_mcp_callback = True
            logger.info(
                "MCP call_tool: sending request",
                tool_name=name,
                tool_call_id=agent_ctx.tool_call_id if agent_ctx else None,
            )
            result = await self._client.call_tool(
                name, arguments, progress_handler=progress_handler, meta=meta, raise_on_error=False
            )
            logger.info(
                "MCP call_tool: received response",
                tool_name=name,
                is_error=result.is_error,
                tool_call_id=agent_ctx.tool_call_id if agent_ctx else None,
            )
            # Check side-channel for durable elicitation deferral
            if agent_ctx and agent_ctx._pending_elicitation_deferral is not None:
                deferred_params = agent_ctx._pending_elicitation_deferral
                agent_ctx._pending_elicitation_deferral = None
                logger.info(
                    "MCP call_tool: CallDeferred detected via side-channel",
                    tool_name=name,
                    deferred_kind="elicitation",
                    tool_call_id=agent_ctx.tool_call_id,
                )
                raise CallDeferred(  # noqa: TRY301
                    metadata={
                        "elicitation": deferred_params,
                        "deferred_kind": "elicitation",
                    }
                )
            if result.is_error:
                # MCP tool returned an error - return it as content so LLM can see it
                error_text = extract_text_content(result.content)
                return ToolReturn(return_value=f"Tool error: {error_text}", content=error_text)
            content = await from_mcp_content(result.content)
            # Decision logic for return type
            match (result.data is not None, bool(content)):
                case (True, True):  # Both structured data and rich content -> ToolReturn
                    return ToolReturn(
                        return_value=_mcp_content_return_value(content),
                        metadata=result.data,
                    )
                case (True, False):  # Only structured data -> return directly
                    return result.data
                case (False, True):  # Only content -> ToolReturn with content
                    return ToolReturn(return_value=_mcp_content_return_value(content))
                case (False, False):  # Fallback to text extraction
                    return extract_text_content(result.content)
                case _:  # Handle unexpected cases
                    raise ValueError(f"Unexpected MCP content: {result.content}")  # noqa: TRY301
        except CallDeferred:
            raise
        except Exception as e:
            raise RuntimeError(f"MCP tool call failed: {e}") from e
        finally:
            # Clear per-call handler and MCP callback flag
            self._current_elicitation_handler = None
            if agent_ctx:
                agent_ctx.in_mcp_callback = False


def _mcp_content_return_value(
    content: list[str | BinaryContent],
) -> str | list[str | BinaryContent]:
    """Return MCP content in the value field that PydanticAI sends as tool output."""
    if len(content) == 1 and isinstance(content[0], str):
        return content[0]
    return content


if __name__ == "__main__":
    path = "/home/phil65/dev/oss/wolfharness/tests/mcp_server/server.py"
    # path = Path(__file__).parent / "test_mcp_server.py"
    config = StdioMCPServerConfig(command="uv", args=["run", str(path)])

    async def main() -> None:
        async with MCPClient(config=config) as mcp_client:
            # Create MCP filesystem
            fs = mcp_client.get_resource_fs()
            print(await fs._ls(""))

    anyio.run(main)
