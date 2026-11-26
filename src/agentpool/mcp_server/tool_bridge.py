"""MCP server bridge for exposing ToolManager tools to ACP agents.

This module provides a bridge that exposes a ToolManager's tools as an MCP server
using HTTP transport. This allows ACP agents (external agents like Claude Code,
Gemini CLI, etc.) to use our internal toolsets like SubagentTools,
AgentManagementTools, etc.

The bridge runs in-process on the same event loop, providing direct access to
the pool and avoiding IPC serialization overhead.

One bridge serves every session of its agent. Each session reaches it through its own
route (:meth:`ToolManagerBridge.url_for`): a tool call runs with the context of the turn
running on its route, so turns of different sessions on one agent don't share their
input provider, deps or injections.

The bridge listens on localhost, where any local process could reach it: it serves only
requests carrying its random bearer token (:attr:`ToolManagerBridge.headers`), which the
backends get in the MCP server config they are handed.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field, replace
import inspect
import secrets
import socket
import time
from typing import TYPE_CHECKING, Any, Self, cast, get_args, get_origin, get_type_hints
from urllib.parse import quote
from uuid import uuid4

from pydantic_ai import RunContext, RunUsage
from pydantic_ai.models.test import TestModel

from agentpool.agents import Agent
from agentpool.log import get_logger
from agentpool.sessions.environment import (
    SessionEnvironment,
    current_session_environment,
    use_session_environment,
)
from agentpool.utils.network import BearerAuth, wait_until_started
from agentpool.utils.signatures import filter_schema_params, get_params_matching_predicate


if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable, Sequence

    from fastmcp import Context, FastMCP
    from fastmcp.tools import ToolResult as FastMCPToolResult
    from pydantic_ai import UserContent
    from uvicorn import Server

    from agentpool.agents import AgentContext
    from agentpool.agents.base_agent import BaseAgent
    from agentpool.resource_providers import ResourceChangeEvent
    from agentpool.tools.base import Tool


logger = get_logger(__name__)

DEFAULT_ROUTE = ""
"""The route of the agent's default session: the bridge's plain URL."""

ROUTE_PARAM = "route"
"""Query parameter of the bridge URL naming the route (see ``url_for``)."""


@dataclass
class BridgeRoute:
    """The tool calls of one session: the turn running on them."""

    key: str
    context: AgentContext[Any] | None = None
    """Context of the running turn (set by ``set_run_context``), None between turns."""
    prompt: str | Sequence[UserContent] | None = None
    """Prompt of the running turn (needed for a stub RunContext)."""
    environment: SessionEnvironment | None = None
    """Session environment of the running turn: its tool calls run in it."""


def _request_route() -> str:
    """The route the current MCP request came in on (the default outside HTTP)."""
    from fastmcp.server.dependencies import get_http_request

    try:
        request = get_http_request()
    except RuntimeError:  # No HTTP request in this context
        return DEFAULT_ROUTE
    return request.query_params.get(ROUTE_PARAM, DEFAULT_ROUTE)


def _is_annotation_of_type(annotation: Any, type_name: str) -> bool:
    """Check if annotation matches the given type name.

    Handles direct types, generics, forward references, and unions.
    """
    if annotation is None or annotation is inspect.Parameter.empty:
        return False
    # Handle string annotations (forward references)
    if isinstance(annotation, str):
        base_name = annotation.split("[")[0].strip()
        return base_name == type_name
    # Check direct class match by name
    if isinstance(annotation, type) and annotation.__name__ == type_name:
        return True
    # Check generic origin (e.g., SomeType[T])
    if origin := get_origin(annotation):
        if isinstance(origin, type) and origin.__name__ == type_name:
            return True
        # Handle Union types (e.g., SomeType | None)
        if origin is type(None) or str(origin) in ("typing.Union", "types.UnionType"):
            return any(_is_annotation_of_type(arg, type_name) for arg in get_args(annotation))
    return False


def _get_context_param_names(fn: Callable[..., Any], type_name: str) -> set[str]:
    """Get parameter names that are AgentContext (to be auto-injected)."""
    return get_params_matching_predicate(
        fn, lambda p: _is_annotation_of_type(p.annotation, type_name)
    )


def _create_stub_run_context(
    ctx: AgentContext[Any],
    prompt: str | Sequence[UserContent] | None = None,
) -> RunContext[Any]:
    """Create a stub RunContext from AgentContext for MCP bridge tool invocations.

    This provides a best-effort RunContext for tools that require it when
    invoked via MCP bridge. Not all fields can be populated accurately since
    we're outside of pydantic-ai's normal execution flow.

    Args:
        ctx: The AgentContext available in the bridge
        prompt: The current prompt being processed

    Returns:
        A RunContext with available information populated
    """
    match ctx.agent:
        case Agent():
            model = ctx.agent._model or TestModel()
        # case ACPAgent() | ClaudeCodeAgent():
        #     try:
        #         model = infer_model(ctx.agent.model_name or "test")
        #     except Exception:
        #         model = TestModel()
        case _:
            model = TestModel()
    # Create a minimal usage object
    return RunContext(
        deps=ctx.data,
        model=model,
        usage=RunUsage(),
        prompt=prompt,
        messages=[],
        tool_name=ctx.tool_name,
        tool_call_id=ctx.tool_call_id,
    )


def _append_injection_to_result(result: Any, injection: str) -> Any:
    """Append an injected message to a tool result.

    The injection is already wrapped in XML tags by the PromptInjectionManager.

    Handles different result types:
    - str: Append with newline separator
    - AgentPool ToolResult: Modify content field
    - dict: Add 'injected_context' key
    - Other: Wrap in dict with original and injection
    """
    from agentpool.tools.base import ToolResult as AgentPoolToolResult

    match result:
        case str():
            return f"{result}\n\n{injection}"
        case AgentPoolToolResult(content=None):
            return replace(result, content=injection)
        case AgentPoolToolResult(content=str() as existing):
            return replace(result, content=f"{existing}\n\n{injection}")
        case AgentPoolToolResult(content=list() as existing):
            return replace(result, content=[*existing, f"\n\n{injection}"])
        case dict():
            return {**result, "injected_context": injection}
        case _:
            return (result, {"injected_context": injection})


def _extract_tool_call_id(context: Context | None) -> str:
    """Extract Claude's original tool_call_id from request metadata.

    Claude Code passes the tool_use_id via the _meta field as
    'claudecode/toolUseId'. This allows us to maintain consistent
    tool_call_ids across ToolCallStartEvent and ToolCallCompleteEvent.

    Falls back to generating a UUID if not available.
    """
    if context and (request_ctx := context.request_context) and request_ctx.meta:
        claude_tool_id = request_ctx.meta.get("claudecode/toolUseId")
        if isinstance(claude_tool_id, str):
            logger.debug("Extracted Claude tool_call_id", tool_call_id=claude_tool_id)
            return claude_tool_id
    return str(uuid4())  # Generate fallback UUID if no tool_call_id found in meta


def _annotations_resolve(fn: Any) -> bool:
    """Whether the type hints of ``fn`` resolve (so a schema built from them is accurate)."""
    try:
        get_type_hints(fn)
    except Exception:  # noqa: BLE001
        return False
    return True


@dataclass
class ToolManagerBridge:
    """Exposes a node's tools as an MCP server for ACP agents.

    This bridge allows external ACP agents to access our internal toolsets
    (SubagentTools, AgentManagementTools, etc.) via HTTP MCP transport.

    The node's existing context is used for tool invocations, providing
    pool access and proper configuration without reconstruction.

    Example:
        ```python
        bridge = ToolManagerBridge(node=agent)
        async with bridge.set_run_context(...):
            # Bridge is running, get MCP config for ACP agent
            mcp_config = bridge.get_mcp_server_config()
            # Pass to ACP agent...
        ```
    """

    node: BaseAgent[Any, Any]
    """The node whose tools to expose."""

    server_name: str | None = None
    """Name for the MCP server."""

    token: str = field(default_factory=lambda: secrets.token_urlsafe(32), init=False)
    """Bearer token a caller must send (see :attr:`headers`)."""

    _routes: dict[str, BridgeRoute] = field(default_factory=dict, init=False, repr=False)
    """Routes by key (one per session reaching the bridge; see ``url_for``)."""

    _mcp: FastMCP | None = field(default=None, init=False, repr=False)
    """FastMCP server instance."""

    _server: Server | None = field(default=None, init=False, repr=False)
    """Uvicorn server instance."""

    _server_task: asyncio.Task[None] | None = field(default=None, init=False, repr=False)
    """Background task running the server."""

    _actual_port: int | None = field(default=None, init=False, repr=False)
    """Actual port the server is bound to."""

    _socket: socket.socket | None = field(default=None, init=False, repr=False)
    """The listening socket (bound by us, served by uvicorn)."""

    def route(self, key: str = DEFAULT_ROUTE) -> BridgeRoute:
        """The route ``key`` (created on first use, then kept)."""
        if (route := self._routes.get(key)) is None:
            route = self._routes[key] = BridgeRoute(key)
        return route

    def drop_route(self, key: str) -> None:
        """Forget route ``key`` (its session ended)."""
        if key != DEFAULT_ROUTE:
            self._routes.pop(key, None)

    def context(self, key: str = DEFAULT_ROUTE) -> AgentContext[Any] | None:
        """Context of the turn running on route ``key``, if one is."""
        route = self._routes.get(key)
        return route.context if route is not None else None

    async def __aenter__(self) -> Self:
        """Start the MCP server."""
        await self.start()
        return self

    async def __aexit__(self, *args: object) -> None:
        """Stop the MCP server."""
        await self.stop()

    async def start(self) -> None:
        """Start the HTTP MCP server in the background."""
        from fastmcp import FastMCP

        self._mcp = FastMCP(name=self.resolved_server_name)
        await self._register_tools()
        self._subscribe_to_tool_changes()
        await self._start_server()

    async def stop(self) -> None:
        """Stop the HTTP MCP server."""
        # Unsubscribe from tool changes
        self._unsubscribe_from_tool_changes()

        if self._server:
            self._server.should_exit = True
            if self._server_task:
                try:
                    await asyncio.wait_for(self._server_task, timeout=5.0)
                except TimeoutError:
                    self._server_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await self._server_task
            self._server = None
            self._server_task = None
        if self._socket is not None:
            self._socket.close()
            self._socket = None
        self._mcp = None
        self._actual_port = None
        logger.info("ToolManagerBridge stopped")

    def _subscribe_to_tool_changes(self) -> None:
        """Subscribe to tool changes from all providers via signals."""
        for provider in self.node.tools.providers:
            provider.tools_changed.connect(self._on_tools_changed)

    def _unsubscribe_from_tool_changes(self) -> None:
        """Disconnect from tool change signals on all providers."""
        for provider in self.node.tools.providers:
            provider.tools_changed.disconnect(self._on_tools_changed)

    async def _on_tools_changed(self, event: ResourceChangeEvent) -> None:
        """Handle tool changes from a provider."""
        logger.info(
            "Tools changed in provider, refreshing MCP tools",
            provider=event.provider_name,
            provider_kind=event.provider_kind,
        )
        if self._mcp:
            await self._refresh_tools()

    async def _refresh_tools(self) -> None:
        """Refresh the tools registered with the MCP server, and announce the change.

        Clients on 2026-07-28 connections hear of it on their ``subscriptions/listen``
        streams (``notify_tools_changed``), with or without a request in flight. Earlier
        protocol versions get FastMCP's in-request notification when the change happens
        during a request, and otherwise see it on their next ``tools/list``.
        """
        if not self._mcp:
            return

        # Get current and new tool sets
        # Support both old and new FastMCP API

        # New API (git): tools stored in _local_provider._components
        # Keys are prefixed with 'tool:', e.g., 'tool:bash'
        current_names = {
            key.removeprefix("tool:")
            for key in self._mcp.local_provider._components
            if key.startswith("tool:")
        }
        new_tools = await self.node.tools.get_tools(state="enabled")
        new_names = {t.name for t in new_tools}
        # Remove tools that are no longer present
        for name in current_names - new_names:
            with suppress(Exception):
                self._mcp.local_provider.remove_tool(name)

        # Add/update tools
        for tool in new_tools:
            if tool.name in current_names:
                # Remove and re-add to update
                with suppress(Exception):
                    self._mcp.local_provider.remove_tool(tool.name)
            self._register_single_tool(tool)
        await self._mcp.notify_tools_changed()

    @property
    def port(self) -> int:
        """Get the actual port the server is running on."""
        if self._actual_port is None:
            raise RuntimeError("Server not started")
        return self._actual_port

    @property
    def url(self) -> str:
        """URL of the default route (the agent's default session)."""
        return f"http://127.0.0.1:{self.port}/mcp"

    @property
    def headers(self) -> dict[str, str]:
        """HTTP headers a backend must send to the bridge: put them in its MCP config."""
        return {"Authorization": f"Bearer {self.token}"}

    def url_for(self, key: str = DEFAULT_ROUTE) -> str:
        """URL of route ``key``: give each session's backend connection its own."""
        if key == DEFAULT_ROUTE:
            return self.url
        return f"{self.url}?{ROUTE_PARAM}={quote(key, safe='')}"

    @property
    def resolved_server_name(self) -> str:
        """Get the server name."""
        return self.server_name or f"agentpool-{self.node.name}-tools"

    async def _register_tools(self) -> None:
        """Register all node tools with the FastMCP server."""
        if not self._mcp:
            return

        tools = await self.node.tools.get_tools(state="enabled")
        for tool in tools:
            self._register_single_tool(tool)
        logger.info("Registered tools with MCP bridge", tools=[t.name for t in tools])

    def _register_single_tool(self, tool: Tool) -> None:
        """Register a single tool with the FastMCP server."""
        if not self._mcp:
            return
        from fastmcp.exceptions import ToolError as FastMCPToolError
        from fastmcp.tools import Tool as FastMCPTool

        class _BridgeTool(FastMCPTool):
            """Custom FastMCP Tool that wraps a agentpool Tool.

            This allows us to use our own schema and invoke tools with AgentContext.
            """

            def __init__(self, tool: Tool, bridge: ToolManagerBridge) -> None:
                # Get input schema from our tool
                schema = tool.schema["function"]
                input_schema = schema.get("parameters", {"type": "object", "properties": {}})
                # Filter out context parameters - they're auto-injected by the bridge
                fn = tool.get_callable()
                context_params = _get_context_param_names(fn, "AgentContext")
                run_context_params = _get_context_param_names(fn, "RunContext")
                all_context_params = context_params | run_context_params
                filtered_schema = filter_schema_params(input_schema, all_context_params)
                super().__init__(
                    name=tool.name,
                    description=tool.description or "No description",
                    parameters=cast(dict[str, Any], filtered_schema),
                    annotations=tool.hints.to_mcp(),
                    # output_schema=tool.output_schema,
                )
                # Set these AFTER super().__init__() to avoid being overwritten
                self._tool = tool
                self._bridge = bridge
                # A schema from annotations that don't resolve is a guess: don't enforce it
                self._check_args = _annotations_resolve(fn)

            def _validate(self, args: dict[str, Any]) -> None:
                """Check ``args`` against the advertised parameters (context ones excluded).

                Raises:
                    ToolError: They don't match (an error result the model sees)
                """
                import jsonschema

                if not self._check_args:
                    return
                try:
                    jsonschema.validate(args, self.parameters)
                except jsonschema.ValidationError as e:
                    msg = (
                        f"Tool {self._tool.name!r} called with invalid args. "
                        f"Ensure args match the schema.\n\nValidation error: {e.message}"
                    )
                    raise FastMCPToolError(msg) from e
                except jsonschema.SchemaError:  # A schema we can't check against
                    pass

            async def run(self, arguments: dict[str, Any]) -> FastMCPToolResult:
                """Execute the wrapped tool with context bridging."""
                from fastmcp.server.dependencies import get_context
                from fastmcp.tools import ToolResult as FastMCPToolResult

                from agentpool.tools.base import ToolResult as AgentPoolToolResult

                args = arguments.copy()
                self._validate(args)

                # Get FastMCP context from context variable (not passed as parameter)
                try:
                    mcp_context: Context | None = get_context()
                except LookupError:
                    mcp_context = None
                # Try to get Claude's original tool_call_id from request metadata
                tc_id = _extract_tool_call_id(mcp_context)
                # Derive per-call context from the context of the turn on this route
                route = self._bridge.route(_request_route())
                base = route.context or self._bridge.node.get_context()
                args_ = args.copy()
                ctx = replace(base, tool_name=self._tool.name, tool_call_id=tc_id, tool_input=args_)
                # Invoke with context - copy args since invoke_tool_with_context
                # modifies kwargs in-place to inject context parameters
                with use_session_environment(route.environment):  # The turn's, not ours
                    result = await self._bridge.invoke_tool_with_context(
                        self._tool, ctx, args, prompt=route.prompt
                    )
                # Take in what was injected into this session's turn meanwhile
                runtime = ctx.runtime or self._bridge.node.runtime()
                if injection := await runtime.injections.consume():
                    result = _append_injection_to_result(result, injection)
                # Convert AgentPool ToolResult to FastMCP ToolResult
                if isinstance(result, AgentPoolToolResult):
                    return FastMCPToolResult(
                        content=result.content,
                        structured_content=result.structured_content,
                    )
                return self.convert_result(result)

        # Create a custom FastMCP Tool that wraps our tool
        bridge_tool = _BridgeTool(tool=tool, bridge=self)
        self._mcp.add_tool(bridge_tool)

    @asynccontextmanager
    async def set_run_context(
        self,
        context: AgentContext[Any],
        prompt: str | Sequence[UserContent] | None = None,
        *,
        route: str = DEFAULT_ROUTE,
    ) -> AsyncGenerator[BridgeRoute]:
        """Make ``context`` the context of tool calls on ``route`` while a turn runs.

        Tool calls coming in on the route derive their context from it (``replace``).

        Args:
            context: Base AgentContext of the turn (deps, input_provider, runtime, ...)
            prompt: The turn's prompt (needed for a stub RunContext)
            route: Route of the turn's session (see :meth:`url_for`)
        """
        bridge_route = self.route(route)
        bridge_route.context = context
        bridge_route.prompt = prompt
        bridge_route.environment = current_session_environment()
        try:
            yield bridge_route
        finally:
            if bridge_route.context is context:  # Not taken over by a later turn
                bridge_route.context = None
                bridge_route.prompt = None
                bridge_route.environment = None

    async def invoke_tool_with_context(
        self,
        tool: Tool,
        ctx: AgentContext[Any],
        kwargs: dict[str, Any],
        *,
        prompt: str | Sequence[UserContent] | None = None,
    ) -> Any:
        """Invoke a tool with proper context injection and hooks.

        Handles tools that expect AgentContext, RunContext, or neither.
        Runs pre/post tool hooks if configured on the node.
        """
        from fastmcp.exceptions import ToolError

        if hooks := self.node.hooks:
            pre_result = await hooks.run_pre_tool_hooks(
                agent_name=ctx.node_name,
                tool_name=tool.name,
                tool_input=kwargs,
                session_id=None,
                env=self.node.env,
            )
            if pre_result.get("decision") == "deny":
                # An error result: the agent's model is told and goes on
                reason = pre_result.get("reason", "Blocked by pre-tool hook")
                raise ToolError(f"Tool {tool.name!r} was blocked: {reason}")
            # Apply modified input if provided
            if modified := pre_result.get("modified_input"):
                kwargs.update(modified)

        fn = tool.get_callable()
        tool_input = dict(kwargs)  # What hooks see: the caller's arguments, no contexts
        # Inject AgentContext parameters, over any value a caller passed for them
        context_param_names = _get_context_param_names(fn, "AgentContext")
        kwargs |= dict.fromkeys(context_param_names, ctx)
        # Inject RunContext parameters (as stub since we're outside pydantic-ai)
        run_context_param_names = _get_context_param_names(fn, "RunContext")
        if run_context_param_names:
            stub_run_ctx = _create_stub_run_context(ctx, prompt=prompt)
            kwargs |= dict.fromkeys(run_context_param_names, stub_run_ctx)

        start_time = time.perf_counter()
        result = fn(**kwargs)
        if inspect.isawaitable(result):
            result = await result
        duration_ms = (time.perf_counter() - start_time) * 1000
        if hooks:
            await hooks.run_post_tool_hooks(
                agent_name=ctx.node_name,
                tool_name=tool.name,
                tool_input=tool_input,
                tool_output=result,
                duration_ms=duration_ms,
                session_id=None,
                env=self.node.env,
            )

        return result

    async def _start_server(self) -> None:
        """Start the uvicorn server in the background, and wait until it serves."""
        import uvicorn

        if not self._mcp:
            raise RuntimeError("MCP server not initialized")

        # Bind the port ourselves and hand uvicorn the bound socket: the port reported is
        # the one served on, with no gap in which another process could take it.
        self._socket = sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(("127.0.0.1", 0))
            sock.set_inheritable(True)
        except BaseException:
            sock.close()
            self._socket = None
            raise
        app = BearerAuth(self._mcp.http_app(transport="http"), self.token)
        cfg = uvicorn.Config(app=app, log_level="warning", ws="websockets-sansio")
        self._server = server = uvicorn.Server(cfg)
        name = f"mcp-bridge-{self.resolved_server_name}"
        self._server_task = task = asyncio.create_task(server.serve(sockets=[sock]), name=name)
        try:
            await wait_until_started(server, task)
        except BaseException:
            await self.stop()
            raise
        self._actual_port = sock.getsockname()[1]
        logger.info("ToolManagerBridge started", url=self.url)
