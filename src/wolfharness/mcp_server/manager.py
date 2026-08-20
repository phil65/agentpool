"""MCP server management for AgentPool."""

from __future__ import annotations

import asyncio
import contextlib
from contextlib import AsyncExitStack
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Self, cast
import warnings

import anyio
from pydantic_ai.capabilities import AbstractCapability

from wolfharness.capabilities.combined_toolset import CombinedToolsetCapability
from wolfharness.capabilities.mcp_server_cap import McpServerCap
from wolfharness.common_types import MCPServerStatus
from wolfharness.log import get_logger
from wolfharness.mcp_server.global_pool import GlobalConnectionPool
from wolfharness_config.mcp_server import AcpMCPServerConfig, BaseMCPServerConfig


if TYPE_CHECKING:
    from collections.abc import Sequence
    from types import TracebackType

    from fastmcp.client import ClientTransport
    from mcp import types
    from mcp.shared.context import RequestContext
    from mcp.types import ElicitRequestParams, SamplingMessage
    from pydantic_ai.capabilities import MCP

    from wolfharness.mcp_server.config_snapshot import McpConfigSnapshot
    from wolfharness.mcp_server.session_pool import SessionConnectionPool
    from wolfharness.ui.base import InputProvider
    from wolfharness_config.mcp_server import MCPServerConfig
    from wolfharness_server.acp_server.acp_mcp_manager import AcpMcpConnectionManager


logger = get_logger(__name__)

#: Per-step timeout (seconds) for MCP cleanup operations in ``cleanup_session()``.
#: Each toolset ``__aexit__``, connection pool cleanup, and ACP MCP cleanup
#: is wrapped in ``asyncio.timeout(_MCP_CLEANUP_TIMEOUT)`` to prevent hangs
#: when an HTTP proxy goes silent during teardown.
_MCP_CLEANUP_TIMEOUT: float = 30.0

# ContextVar for the current session's InputProvider, set by the run loop
# before agent execution.  Read by the PydanticAI MCP elicitation callback
# so that agent-level MCP servers can delegate to ACPInputProvider.
_current_input_provider: ContextVar[InputProvider | None] = ContextVar(
    "_current_input_provider", default=None
)


def set_current_input_provider(provider: InputProvider | None) -> None:
    """Set the InputProvider for the current async context."""
    _current_input_provider.set(provider)


def _make_elicitation_handler() -> Any:
    """Create a FastMCP elicitation handler for MCPToolset.

    The handler reads the current InputProvider from the ContextVar
    and delegates to ``InputProvider.get_elicitation()``.

    **Critical**: This handler must NEVER return ``mcp.types.ElicitResult``
    (``MCPElicitResult``) directly. FastMCP's ``create_elicitation_callback``
    wrapper checks ``isinstance(result, fastmcp.client.elicitation.ElicitResult)``
    — and since ``MCPElicitResult`` is NOT a subclass of fastmcp's
    ``ElicitResult``, the wrapper would wrap the entire ``MCPElicitResult``
    object as ``content``, producing ``{"content": {"_meta": null, "content": null}}``
    on the wire. This causes Zod validation errors on the MCP server side.
    """
    from fastmcp.client.elicitation import ElicitResult
    from mcp.types import ElicitResult as MCPElicitResult, ErrorData

    async def _handler[T](
        message: str,
        response_type: type[T] | None,
        params: ElicitRequestParams,
        context: RequestContext[Any, Any],
    ) -> T | dict[str, Any] | ElicitResult:
        provider = _current_input_provider.get()
        if provider is None:
            logger.warning(
                "No InputProvider in context for MCP elicitation, declining",
            )
            return ElicitResult(action="decline")
        result = await provider.get_elicitation(params)
        # Extract content from MCPElicitResult before returning to fastmcp.
        # See docstring for why MCPElicitResult must never reach the wrapper.
        match result:
            case MCPElicitResult(action="accept", content=content):
                return cast("T | dict[str, Any]", content)
            case MCPElicitResult(action="cancel"):
                return ElicitResult(action="cancel")
            case MCPElicitResult(action="decline"):
                return ElicitResult(action="decline")
            case ErrorData():
                return ElicitResult(action="decline")
            case _:
                return ElicitResult(action="decline")

    return _handler


def _make_timeout_logger(
    server_name: str | None,
) -> Any:
    """Build a ``process_tool_call`` callback that logs MCP tool call timeouts.

    The callback wraps ``direct_call_tool`` and emits a ``WARNING``-level log
    when the underlying MCP request times out, so operators can distinguish
    timeouts from other tool errors.

    Args:
        server_name: Display name of the MCP server, included in the log message.

    Returns:
        A callable suitable for ``MCPToolset.process_tool_call``.
    """

    async def _process_tool_call(
        ctx: Any,
        direct_call_tool: Any,
        name: str,
        tool_args: dict[str, Any],
    ) -> Any:
        try:
            return await direct_call_tool(name, tool_args)
        except Exception as e:
            msg = str(e)
            if "Timed out" in msg or "timeout" in msg.lower():
                logger.warning(
                    "MCP tool call timed out (server=%s, tool=%s): %s",
                    server_name,
                    name,
                    msg,
                )
            raise

    return _process_tool_call


async def _fetch_connected_status(
    cap: McpServerCap,
) -> tuple[list[str], str | None, str | None]:
    """Fetch tools list and server info for a connected McpServerCap.

    Returns a tuple of ``(tool_names, server_name, server_version)``.
    Tool names come from ``cap.list_tools()``; server name/version come
    from ``cap.client.server_info`` (the new MCPClient property). Both
    are read-only and do NOT trigger lazy connections — the caller must
    ensure ``cap.client is not None`` before invoking this helper.
    """
    tool_entries = await cap.list_tools()
    tools = [t.name for t in tool_entries]
    server_name: str | None = None
    server_version: str | None = None
    client = cap.client
    if client is not None:
        info = client.server_info
        if info is not None:
            server_name = info.get("name")
            server_version = info.get("version")
    return tools, server_name, server_version


@dataclass
class McpSessionContext:
    """Per-session MCP state container for the :class:`MCPManager`.

    Holds all session-scoped MCP resources so that each session has its own
    connection pool, toolset cache, config snapshot, and ACP connection
    tracking — isolated from other sessions.

    Attributes:
        connection_pool: Per-session transport pool; created lazily by
            ``get_or_create_session()`` (T2).
        toolset_cache: Session-scoped ``MCPToolset`` cache keyed by
            ``client_id``, mirroring the global ``_toolset_cache``.
        snapshot: Immutable MCP config snapshot for this session, or
            ``None`` if no snapshot has been built yet.
        acp_connection_ids: List of ``(client_id, connection_id)`` tuples
            for ACP MCP connections opened during this session, used for
            cleanup tracking.
        _cleanup_lock: Serializes concurrent cleanup calls for this session.
    """

    connection_pool: SessionConnectionPool | None = None
    toolset_cache: dict[str, Any] = field(default_factory=dict)
    snapshot: McpConfigSnapshot | None = None
    acp_connection_ids: list[tuple[str, int]] = field(default_factory=list)
    #: Session-scoped ``McpServerCap`` instances for resource access, keyed by
    #: ``client_id``. Created lazily by ``get_resource_providers()`` and cleared
    #: during ``cleanup_session()``. Unlike pool-level providers (which own
    #: their MCPClient connections via ``setup_server``), these caps use the
    #: session's ``connection_pool`` for lazy client initialization — no
    #: duplicate connections.
    resource_providers: dict[str, McpServerCap] = field(default_factory=dict)
    _cleanup_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class MCPManager:
    """Manages MCP server connections and distributes resource providers.

    .. deprecated::
        This class is deprecated and will be removed in v0.5.0.
        Use :meth:`get_capabilities()` instead.
    """

    def __init__(
        self,
        name: str = "mcp",
        owner: str | None = None,
        sampling_model: str = "openai:gpt-5-nano",
        servers: Sequence[MCPServerConfig | str] | None = None,
        accessible_roots: list[str] | None = None,
    ) -> None:
        self.name = name
        self.owner = owner
        self.servers: list[MCPServerConfig] = []
        for server in servers or []:
            self.add_server_config(server)
        self.providers: list[McpServerCap] = []
        self.sampling_model = sampling_model
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            self.aggregating_provider = CombinedToolsetCapability(
                capabilities=cast(list[AbstractCapability], self.providers),
                name=f"{name}_aggregated",
            )
        self.exit_stack = AsyncExitStack()
        self._accessible_roots = accessible_roots
        self._global_pool = GlobalConnectionPool()
        self._toolset_cache: dict[str, Any] = {}
        self._session_contexts: dict[str, McpSessionContext] = {}
        self._acp_mcp_manager: AcpMcpConnectionManager | None = None
        # Per-client_id setup error messages, populated by setup_server()
        # when a connection attempt fails. Cleared on successful retry.
        # Used by get_server_status() to report status="error" for failed
        # servers instead of silently dropping them.
        self._setup_errors: dict[str, str] = {}
        # Model-visible tool prefixes in use, for display_name de-duplication
        # (RFC-0058): two servers sharing a display_name get prefixes
        # ``name``, ``name_2``, ...
        self._used_tool_prefixes: set[str] = set()

    def add_server_config(self, cfg: MCPServerConfig | str) -> None:
        """Add a new MCP server to the manager."""
        resolved = BaseMCPServerConfig.from_string(cfg) if isinstance(cfg, str) else cfg
        self.servers.append(resolved)

    def get_or_create_session(self, session_id: str) -> McpSessionContext:
        """Get or create the per-session MCP context for ``session_id``.

        If no context exists for ``session_id``, a new ``McpSessionContext``
        is created with a fresh ``SessionConnectionPool``, empty toolset
        cache, no snapshot, and an empty ACP connection list.  Subsequent
        calls with the same ``session_id`` return the same object.

        Args:
            session_id: Unique identifier for the session.

        Returns:
            The ``McpSessionContext`` for this session.
        """
        from wolfharness.mcp_server.session_pool import SessionConnectionPool

        ctx = self._session_contexts.get(session_id)
        if ctx is None:
            ctx = McpSessionContext(
                connection_pool=SessionConnectionPool(session_id=session_id),
            )
            self._session_contexts[session_id] = ctx
        return ctx

    def get_session_context(self, session_id: str) -> McpSessionContext | None:
        """Get the session context for ``session_id`` without creating one.

        Returns ``None`` if no context exists for the session.
        """
        return self._session_contexts.get(session_id)

    def update_session_snapshot(
        self,
        session_id: str,
        snapshot: McpConfigSnapshot,
    ) -> None:
        """Update the config snapshot for a session.

        Ensures the session context exists (creating it if necessary),
        then sets ``ctx.snapshot`` to the provided snapshot.  Safe to call
        on an already-existing session — only the snapshot is replaced.

        Args:
            session_id: Unique identifier for the session.
            snapshot: Immutable MCP config snapshot to store.
        """
        ctx = self.get_or_create_session(session_id)
        ctx.snapshot = snapshot

    async def add_transport(
        self,
        session_id: str,
        client_id: str,
        transport: ClientTransport,
        skill_name: str | None = None,
    ) -> None:
        """Add a pre-created transport to the session's connection pool.

        Delegates to the internal :class:`SessionConnectionPool` for the
        given session.  If no session context exists yet, one is created
        via ``get_or_create_session()``.

        Args:
            session_id: Unique identifier for the session.
            client_id: Client identifier for the MCP server.
            transport: Pre-created fastmcp ``ClientTransport``.
            skill_name: Optional skill name for skill-scoped MCP isolation.
        """
        ctx = self.get_or_create_session(session_id)
        if ctx.connection_pool is not None:
            await ctx.connection_pool.add_transport(client_id, transport, skill_name)

    def __repr__(self) -> str:
        return f"MCPManager(name={self.name!r}, servers={len(self.servers)})"

    async def __aenter__(self) -> Self:
        # Use return_exceptions=True so a single server's setup failure does
        # NOT prevent the remaining servers from initializing. Failed servers
        # are recorded in self._setup_errors by setup_server() before
        # re-raising; successful servers proceed to self.providers. The
        # manager enters its context even if some servers fail, making
        # get_server_status() reachable to report both connected and failed
        # servers.
        if tasks := [self.setup_server(server) for server in self.servers]:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for server, result in zip(self.servers, results, strict=True):
                if isinstance(result, asyncio.CancelledError):
                    raise result
                if isinstance(result, BaseException):
                    logger.warning(
                        "MCP server %s failed to initialize: %s",
                        server.display_name,
                        result,
                    )
                    # setup_server already recorded the error in
                    # self._setup_errors before re-raising.
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.cleanup()

    async def _sampling_callback(
        self,
        messages: list[SamplingMessage],
        params: types.CreateMessageRequestParams,
        context: RequestContext[Any, Any, Any],
    ) -> str:
        """Handle MCP sampling by creating a new agent with specified preferences."""
        from wolfharness.agents import Agent
        from wolfharness.mcp_server.conversions import sampling_messages_to_user_content

        prompts = sampling_messages_to_user_content(messages)
        model = self.sampling_model
        if (prefs := params.modelPreferences) and prefs.hints and prefs.hints[0].name:
            model = prefs.hints[0].name  # Extract model from preferences
        # Create usage limits from sampling parameters
        # limits = UsageLimits(output_tokens_limit=params.maxTokens, request_limit=1)
        # TODO: Re-add per-turn usage_limits once implemented for all agents
        # TODO: Apply temperature from params.temperature
        sys_prompt = params.systemPrompt or ""
        agent = Agent(name="sampling-agent", model=model, system_prompt=sys_prompt, session=False)
        try:
            async with agent:
                result = await agent.run(*prompts, store_history=False)
                return result.content

        except Exception as e:
            logger.exception("Sampling failed")
            return f"Sampling failed: {e!s}"

    async def setup_server(
        self, config: MCPServerConfig, *, add_to_config: bool = False
    ) -> McpServerCap | None:
        """Set up a single MCP server resource provider.

        Args:
            config: MCP server configuration
            add_to_config: If True, also add config to self.servers list and
                          raise ValueError if config is disabled

        Returns:
            The provider if created, None if config is disabled (only when add_to_config=False)

        Raises:
            ValueError: If add_to_config=True and config is disabled
        """
        if not config.enabled:
            if add_to_config:
                raise ValueError(f"Server config {config.client_id} is disabled")
            return None

        if add_to_config:
            self.add_server_config(config)

        # Deduplication: skip if a provider with the same client_id already exists
        if any(p.config.client_id == config.client_id for p in self.providers):
            logger.debug(
                "MCP server already registered, skipping",
                client_id=config.client_id,
            )
            return None

        # De-duplicate the model-visible tool prefix across servers sharing
        # a display_name so prefixed tool names never collide (RFC-0058).
        base_prefix = config.display_name
        candidate = base_prefix
        n = 2
        while candidate in self._used_tool_prefixes:
            candidate = f"{base_prefix}_{n}"
            n += 1
        self._used_tool_prefixes.add(candidate)

        from wolfharness.mcp_server.client import MCPClient

        try:
            client = MCPClient(
                config=config,
                sampling_callback=self._sampling_callback,
                accessible_roots=self._accessible_roots,
            )
            provider = McpServerCap(
                config=config,
                name=f"{self.name}_{config.display_name}",
                client=client,
                tool_prefix=candidate,
            )
            provider = await self.exit_stack.enter_async_context(provider)
            self.providers.append(provider)
        except Exception as e:
            # Record the failure so get_server_status() can report it
            # as status="error" instead of silently dropping the server.
            # Re-raise so __aenter__ (with return_exceptions=True) and
            # direct callers both see the failure.
            self._setup_errors[config.client_id] = str(e)
            raise
        # Success — clear any prior error for this client_id (retry succeeded).
        self._setup_errors.pop(config.client_id, None)
        return provider

    def get_mcp_providers(self) -> list[McpServerCap]:
        """Get all MCP resource providers managed by this manager."""
        return list(self.providers)

    @staticmethod
    async def _filter_resource_providers(
        providers: list[McpServerCap],
    ) -> list[McpServerCap]:
        """Keep only providers whose servers advertised resources."""
        supported: list[McpServerCap] = []
        for provider in providers:
            try:
                if await provider.supports_resources():
                    supported.append(provider)
            except (OSError, TimeoutError, RuntimeError) as exc:
                logger.warning(
                    "Failed to negotiate MCP resource capability",
                    server=provider.client_name,
                    error=str(exc),
                )
        return supported

    async def get_resource_providers(
        self,
        session_id: str | None = None,
        *,
        session_only: bool = False,
    ) -> list[McpServerCap]:
        """Return ``McpServerCap`` instances for resource access.

        Pool-level servers reuse the existing instances created by
        ``setup_server()`` during ``__aenter__``.  Session-scoped servers
        (from the session's ``McpConfigSnapshot``) get lazily-created
        ``McpServerCap`` instances backed by the session's connection pool —
        no duplicate connections are opened.

        Only servers that advertised the MCP ``resources`` capability are
        returned. Capability discovery may initialize a lazy session-scoped
        connection, but it reuses the session connection pool.

        ACP servers are excluded from the session-scoped path (their
        transports are managed separately via ``add_acp_transport`` and
        surfaced through ``get_aggregating_provider()``).

        Args:
            session_id: Optional session identifier. When provided and a
                session context with a config snapshot exists, session-scoped
                providers are appended to the pool-level providers. When
                ``None`` or no session context exists, only pool-level
                providers are returned.
            session_only: When ``True``, return **only** session-scoped
                providers (excluding pool-level ones). Requires
                ``session_id``; returns an empty list if no session context
                exists. Use this when registering at SESSION scope to avoid
                duplicating pool-level providers that are registered
                separately at POOL scope.

        Returns:
            List of ``McpServerCap`` instances. Deduplicated by ``client_id``
            (pool-level providers take precedence over session-scoped ones
            with the same ``client_id``).
        """
        if session_only:
            if session_id is None:
                return []
            ctx = self._session_contexts.get(session_id)
            if ctx is None or ctx.snapshot is None or ctx.connection_pool is None:
                return []
            existing_ids = {p.config.client_id for p in self.providers}
            result: list[McpServerCap] = []
            for entry in ctx.snapshot.session_scoped_configs:
                server = entry.server_config
                if not server.enabled:
                    continue
                if server.client_id in existing_ids:
                    continue
                if isinstance(server, AcpMCPServerConfig):
                    continue
                if server.client_id not in ctx.resource_providers:
                    cap = McpServerCap(
                        config=server,
                        session_pool=ctx.connection_pool,
                        name=f"{self.name}_{server.display_name}",
                    )
                    ctx.resource_providers[server.client_id] = cap
                result.append(ctx.resource_providers[server.client_id])
            return await self._filter_resource_providers(result)

        providers = list(self.providers)

        if session_id is None:
            return await self._filter_resource_providers(providers)

        ctx = self._session_contexts.get(session_id)
        if ctx is None or ctx.snapshot is None or ctx.connection_pool is None:
            return await self._filter_resource_providers(providers)

        existing_ids = {p.config.client_id for p in providers}

        for entry in ctx.snapshot.session_scoped_configs:
            server = entry.server_config
            if not server.enabled:
                continue
            if server.client_id in existing_ids:
                continue
            if isinstance(server, AcpMCPServerConfig):
                continue

            if server.client_id not in ctx.resource_providers:
                cap = McpServerCap(
                    config=server,
                    session_pool=ctx.connection_pool,
                    name=f"{self.name}_{server.display_name}",
                )
                ctx.resource_providers[server.client_id] = cap

            providers.append(ctx.resource_providers[server.client_id])

        return await self._filter_resource_providers(providers)

    async def get_server_status(self) -> dict[str, MCPServerStatus]:
        """Get status of all configured MCP servers.

        Joins ``self.servers`` (configured), ``self.providers`` (connected),
        and ``self._setup_errors`` (failed setup attempts) into a single
        dict keyed by ``display_name`` (the human-readable server name
        configured by the user, falling back to ``client_id`` when no
        name is set).

        Precedence on multi-category membership:
            - ``disabled`` wins over all (config has ``enabled=False``)
            - ``providers`` (connected) wins over ``_setup_errors`` (error)
            - otherwise ``_setup_errors`` → ``status="error"``
            - otherwise ``status="disconnected"``

        This method SHALL NOT trigger lazy connections: it only reads
        ``cap.client`` (the cached property) and calls ``list_tools()``
        when ``cap.client is not None``. Disconnected/pending servers
        return ``tools=[]`` without calling ``_ensure_client()``.

        Returns:
            Dict mapping ``display_name`` to ``MCPServerStatus``.
        """
        # Index providers by client_id for O(1) lookup.
        providers_by_id: dict[str, McpServerCap] = {p.config.client_id: p for p in self.providers}
        # Index server configs by client_id.
        configs_by_id: dict[str, MCPServerConfig] = {s.client_id: s for s in self.servers}
        # Union of all known client_ids.
        all_ids: set[str] = set(configs_by_id) | set(providers_by_id) | set(self._setup_errors)

        # Build base status entries (without tools) synchronously.
        # Keyed by display_name (readable), not client_id (internal).
        entries: dict[str, MCPServerStatus] = {}
        connected_caps: list[McpServerCap] = []
        for client_id in all_ids:
            config = configs_by_id.get(client_id)
            cap = providers_by_id.get(client_id)
            # Resolve display_name from config or cap (falls back to client_id).
            display_name: str = (
                config.display_name
                if config is not None
                else cap.config.display_name
                if cap is not None
                else client_id
            )
            server_type = (
                config.type
                if config is not None
                else cap.config.type
                if cap is not None
                else "unknown"
            )
            # Disabled wins over all other states.
            if config is not None and not config.enabled:
                entries[display_name] = MCPServerStatus(
                    name=display_name,
                    status="disabled",
                    display_name=display_name,
                    server_type=server_type,
                )
                continue
            # Connected (providers wins over errors).
            if cap is not None:
                # Only query tools if the client is already cached (no lazy
                # connection). cap.client returns self._client without
                # calling _ensure_client().
                if cap.client is not None:
                    connected_caps.append(cap)
                entries[display_name] = MCPServerStatus(
                    name=display_name,
                    status="connected",
                    display_name=display_name,
                    server_type=server_type,
                )
                continue
            # Error (failed setup).
            if client_id in self._setup_errors:
                error_msg = self._setup_errors[client_id]
                entries[display_name] = MCPServerStatus(
                    name=display_name,
                    status="error",
                    display_name=display_name,
                    server_type=server_type,
                    error=error_msg,
                )
                continue
            # Configured but not yet connected and no error recorded.
            entries[display_name] = MCPServerStatus(
                name=display_name,
                status="disconnected",
                display_name=display_name,
                server_type=server_type,
            )

        # Concurrently fetch tools + server_info for connected servers only.
        if connected_caps:
            results = await asyncio.gather(
                *(_fetch_connected_status(cap) for cap in connected_caps),
                return_exceptions=True,
            )
            for cap, result in zip(connected_caps, results, strict=True):
                # Resolve the entry key using the same logic as entry creation:
                # config.display_name takes precedence over cap.config.display_name.
                cid = cap.config.client_id
                cfg = configs_by_id.get(cid)
                key = cfg.display_name if cfg is not None else cap.config.display_name
                entry = entries.get(key)
                if entry is None:
                    continue
                if isinstance(result, asyncio.CancelledError):
                    raise result
                if isinstance(result, BaseException):
                    # Tool fetch failed — keep connected status, empty tools.
                    logger.warning(
                        "Failed to fetch MCP server status for %s: %s",
                        cap.config.client_id,
                        result,
                    )
                    continue
                tools, server_name, server_version = result
                entries[key] = MCPServerStatus(
                    name=entry.name,
                    status=entry.status,
                    display_name=entry.display_name,
                    server_type=entry.server_type,
                    error=entry.error,
                    server_name=server_name,
                    server_version=server_version,
                    tools=tools,
                )

        return entries

    def remove_provider(self, client_id: str) -> bool:
        """Remove a provider by its server config's client_id.

        Args:
            client_id: The client_id of the MCP server config to remove

        Returns:
            True if a provider was removed, False otherwise
        """
        for i, provider in enumerate(self.providers):
            if provider.config.client_id == client_id:
                # Note: We don't remove from exit_stack here because
                # the provider was entered into the stack; cleanup() handles that
                self.providers.pop(i)
                return True
        return False

    async def disconnect_all(self) -> None:
        """Disconnect all MCP providers without clearing the servers list.

        Closes both global cached toolsets and per-session toolset caches
        to ensure no persistent connections are leaked during pool shutdown.

        Each ``__aexit__`` call is wrapped in a timeout
        (``_MCP_CLEANUP_TIMEOUT``) to prevent hangs when HTTP proxies
        don't promptly close TCP connections.  Unexpected exceptions are
        logged but never re-raised so that one failing toolset doesn't
        block cleanup of the rest.
        """
        # Close global cached MCPToolset instances.
        # MCPToolset has no aclose() — must use __aexit__ for cleanup.
        # Guard against toolsets that were constructed but never entered
        # (MCPToolset.__aexit__ raises ValueError if __aenter__ wasn't called).
        for toolset in self._toolset_cache.values():
            with contextlib.suppress(ValueError):
                try:
                    async with asyncio.timeout(_MCP_CLEANUP_TIMEOUT):
                        await toolset.__aexit__(None, None, None)
                except TimeoutError:
                    logger.warning("MCP toolset cleanup timed out (global)")
                except Exception:
                    logger.exception("Error cleaning up toolset (global)")
        self._toolset_cache.clear()

        # Close per-session toolset caches to avoid leaking persistent
        # connections from abandoned sessions.
        for ctx in self._session_contexts.values():
            for toolset in ctx.toolset_cache.values():
                with contextlib.suppress(ValueError):
                    try:
                        async with asyncio.timeout(_MCP_CLEANUP_TIMEOUT):
                            await toolset.__aexit__(None, None, None)
                    except TimeoutError:
                        logger.warning("MCP toolset cleanup timed out (session)")
                    except Exception:
                        logger.exception("Error cleaning up toolset (session)")
            ctx.toolset_cache.clear()

        await self._global_pool.shutdown_all()
        await self.cleanup()
        self.exit_stack = AsyncExitStack()

    def get_aggregating_provider(self) -> CombinedToolsetCapability:
        """Get an aggregating provider containing only ACP providers.

        Non-ACP providers are excluded because they are handled separately
        by :meth:`get_capabilities()`.
        """
        acp_providers = [p for p in self.providers if isinstance(p.config, AcpMCPServerConfig)]
        return CombinedToolsetCapability(
            capabilities=cast(list[AbstractCapability], acp_providers),
            name=f"{self.name}_acp_aggregated",
        )

    async def get_capabilities(  # noqa: PLR0915
        self,
        session_id: str | None = None,
        *,
        exclude_global: bool = False,
    ) -> list[MCP]:
        """Return pydantic-ai MCP capabilities for all configured servers.

        Each enabled server is converted to a pydantic-ai ``MCP`` capability.
        ``MCPToolset`` instances are cached by ``client_id`` so repeated calls
        reuse the same underlying connection.  Servers using ACP transport are
        skipped in global configs since pydantic-ai does not support ACP
        directly. Disabled servers are also skipped.

        When ``session_id`` is provided, the session's ``McpSessionContext`` is
        looked up via ``get_or_create_session()``.  If a snapshot is stored
        on the context, configs are partitioned:

        - Global configs (pool + agent) use ``self._global_pool`` for
          transports and ``self._toolset_cache`` for toolset caching.
        - Session-scoped configs (session + skill) use the session's
          ``connection_pool`` for transports and ``ctx.toolset_cache`` for
          toolset caching.

        GAP-11: If the session context was popped by concurrent
        ``cleanup_session()`` between the initial state check and
        subsequent access, the ``.get()`` call returns ``None`` and
        the method falls back to global-only capabilities (with a
        warning).  This is a benign race: ``dict.get()`` is atomic,
        so no ``KeyError`` can occur.

        When ``session_id`` is None, the legacy path uses ``self.servers``
        with ``self._global_pool`` for transports and ``self._toolset_cache``
        for toolset caching.

        Args:
            session_id: Optional session identifier for per-session MCP
                config isolation. When None, only global configs from
                ``self.servers`` are processed.
            exclude_global: When True, skip pool + agent global configs.
                Used when top-level McpServerCap instances are injected
                directly into the agent (RFC-0058); only session-scoped
                configs (session + skill) are processed.

        Returns:
            A list of ``pydantic_ai.capabilities.MCP`` instances, one per
            configured and enabled server with a supported transport.
        """
        from pydantic_ai.capabilities import MCP
        from pydantic_ai.mcp import MCPToolset

        from wolfharness_config.mcp_server import (
            SSEMCPServerConfig,
            StdioMCPServerConfig,
            StreamableHTTPMCPServerConfig,
        )

        capabilities: list[MCP] = []

        def _make_kwargs(server: BaseMCPServerConfig) -> dict[str, Any]:
            """Build MCPToolset constructor kwargs (without client)."""
            kwargs: dict[str, Any] = {
                "id": server.name,
                "include_instructions": True,
                "process_tool_call": _make_timeout_logger(server.display_name),
                "init_timeout": server.timeout,
                "read_timeout": server.timeout,
                "elicitation_handler": _make_elicitation_handler(),
            }
            if (
                isinstance(server, (SSEMCPServerConfig, StreamableHTTPMCPServerConfig))
                and server.auth.oauth
            ):
                kwargs["auth"] = "oauth"
            return kwargs

        def _derive_url(server: BaseMCPServerConfig) -> str:
            """Derive the synthetic URL required by ``MCP.__init__``."""
            match server:
                case SSEMCPServerConfig():
                    return str(server.url)
                case StreamableHTTPMCPServerConfig():
                    return str(server.url)
                case StdioMCPServerConfig():
                    return f"mcp://stdio/{server.client_id}"
                case _:
                    return f"mcp://{server.type}/{server.client_id}"

        async def _make_capability(
            server: BaseMCPServerConfig,
            transport: Any,
            toolset_cache: dict[str, Any],
        ) -> MCP:
            """Create or reuse an MCPToolset and wrap it in an MCP capability.

            On first call for a given ``client_id``, a new ``MCPToolset`` is
            constructed, eagerly entered via ``__aenter__`` to hold a
            persistent reference, and stored in ``toolset_cache``.  This
            ensures the MCP connection survives across turns: pydantic-ai's
            per-turn ``iter()`` enter/exit goes 1→2→1 instead of 0→1→0,
            preventing connection teardown and cache clearing between turns.

            Subsequent calls reuse the cached, already-entered toolset.
            The ``MCP`` wrapper is always fresh.

            If ``__aenter__`` fails, the toolset is NOT cached so that
            subsequent calls can retry the connection.
            """
            client_id = server.client_id
            toolset = toolset_cache.get(client_id)
            if toolset is None:
                toolset = MCPToolset(client=transport, **_make_kwargs(server))
                try:
                    await toolset.__aenter__()
                except Exception:
                    # Eager enter failed — don't cache a broken toolset.
                    # Subsequent calls can retry.
                    del toolset  # Help GC
                    raise
                toolset_cache[client_id] = toolset

            return MCP(
                url=_derive_url(server),
                local=toolset,
                native=False,
                id=server.name or server.client_id,
                allowed_tools=server.enabled_tools,
            )

        async def _process_global_configs(
            snap: McpConfigSnapshot,
            toolset_cache: dict[str, Any],
        ) -> None:
            """Process global configs (pool + agent) from the snapshot."""
            for entry in snap.global_configs:
                server = entry.server_config
                if not server.enabled or isinstance(server, AcpMCPServerConfig):
                    continue
                transport = await self._global_pool.get_transport(server)
                capabilities.append(await _make_capability(server, transport, toolset_cache))

        async def _process_session_configs(
            snap: McpConfigSnapshot,
            toolset_cache: dict[str, Any],
            connection_pool: SessionConnectionPool,
        ) -> None:
            """Process session-scoped configs (session + skill) from the snapshot.

            ACP entries have pre-stored transports via ``add_transport()``;
            ``get_transport()`` returns them without trying to create new
            ones.  Inherited ACP configs (from parent session) that don't
            have a transport in this session's pool are skipped — they go
            through the ACP aggregating provider.
            """
            for entry in snap.session_scoped_configs:
                server = entry.server_config
                if not server.enabled:
                    continue
                if isinstance(server, AcpMCPServerConfig):
                    try:
                        transport = await connection_pool.get_transport(server, entry.skill_name)
                    except NotImplementedError:
                        continue
                else:
                    transport = await connection_pool.get_transport(server, entry.skill_name)
                capabilities.append(await _make_capability(server, transport, toolset_cache))

        ctx = self._session_contexts.get(session_id) if session_id is not None else None

        if ctx is not None and ctx.snapshot is not None:
            if not exclude_global:
                await _process_global_configs(ctx.snapshot, self._toolset_cache)
            if ctx.connection_pool is not None:
                await _process_session_configs(
                    ctx.snapshot,
                    ctx.toolset_cache,
                    ctx.connection_pool,
                )
        else:
            if session_id is not None and ctx is None:
                logger.warning(
                    "Session %s context was removed during get_capabilities(); "
                    "falling back to global-only MCP capabilities.",
                    session_id,
                )
            if not exclude_global:
                for server in self.servers:
                    if not server.enabled or isinstance(server, AcpMCPServerConfig):
                        continue
                    transport = await self._global_pool.get_transport(server)
                    caps = await _make_capability(server, transport, self._toolset_cache)
                    capabilities.append(caps)

        return capabilities

    async def cleanup(self) -> None:
        """Clean up all MCP connections and providers."""
        try:
            with anyio.CancelScope(shield=True):
                try:
                    with anyio.fail_after(5):
                        await self.exit_stack.aclose()
                except TimeoutError:
                    logger.warning("MCP cleanup timed out after 5s, forcing exit")

            self.providers.clear()

        except Exception as e:
            msg = "Error during MCP manager cleanup"
            logger.exception(msg, exc_info=e)
            raise RuntimeError(msg) from e

    async def add_acp_transport(
        self,
        session_id: str,
        client_id: str,
        transport: ClientTransport,
        connection_id: str,
        session_key: int,
    ) -> None:
        """Register an ACP MCP transport for a session.

        Adds a pre-created transport (e.g. ``AcpMcpTransport``) to the
        session's connection pool and tracks the ACP connection for
        cleanup.

        Idempotent: calling twice with the same ``connection_id`` and
        ``session_key`` does not create duplicate tracking entries.

        Args:
            session_id: Unique identifier for the session.
            client_id: Client identifier for the MCP server.
            transport: Pre-created fastmcp ``ClientTransport``.
            connection_id: ACP connection identifier.
            session_key: ACP session key for the connection.
        """
        ctx = self.get_or_create_session(session_id)
        pool = ctx.connection_pool
        if pool is not None:
            await pool.add_transport(client_id, transport)
        entry = (connection_id, session_key)
        if entry not in ctx.acp_connection_ids:
            ctx.acp_connection_ids.append(entry)

    async def cleanup_session(self, session_id: str) -> None:
        """Clean up all MCP resources for a single session.

        Clears the session-scoped toolset cache, shuts down the
        per-session connection pool, delegates ACP connection cleanup
        to :class:`AcpMcpConnectionManager` (if wired), and removes
        the session context from the registry.

        The per-session ``_cleanup_lock`` serializes concurrent calls
        for the same ``session_id``, making cleanup idempotent: a
        second caller blocks on the lock, then finds the context
        already popped in the ``finally`` block.

        Intermediate cleanup errors are logged but never re-raised
        so that the context is always removed.

        Args:
            session_id: Unique identifier for the session to clean up.
        """
        ctx = self._session_contexts.get(session_id)
        if ctx is None:
            return
        async with ctx._cleanup_lock:
            # Identity check: if the context in _session_contexts is no
            # longer this ctx, another caller already cleaned it up.
            if self._session_contexts.get(session_id) is not ctx:
                return
            try:
                # Close cached MCPToolset instances before clearing the cache.
                # MCPToolset has no aclose() — must use __aexit__ for cleanup.
                # Guard against toolsets that were constructed but never entered
                # (MCPToolset.__aexit__ raises ValueError if __aenter__ wasn't called).
                for toolset in ctx.toolset_cache.values():
                    with contextlib.suppress(ValueError):
                        try:
                            async with asyncio.timeout(_MCP_CLEANUP_TIMEOUT):
                                await toolset.__aexit__(None, None, None)
                        except TimeoutError:
                            logger.warning(
                                "MCP toolset cleanup timed out",
                                session_id=session_id,
                            )
                        except Exception:
                            logger.exception(
                                "Error cleaning up toolset",
                                session_id=session_id,
                            )
                ctx.toolset_cache.clear()

                # Clear session-scoped resource provider caps. These don't own
                # connections (they borrow from the session connection pool),
                # so we only need to drop references — the pool cleanup below
                # handles transport teardown.
                ctx.resource_providers.clear()

                if ctx.connection_pool is not None:
                    try:
                        async with asyncio.timeout(_MCP_CLEANUP_TIMEOUT):
                            await ctx.connection_pool.cleanup()
                    except TimeoutError:
                        logger.warning(
                            "MCP connection pool cleanup timed out",
                            session_id=session_id,
                        )
                    except Exception:
                        logger.exception(
                            "Error cleaning up session connection pool",
                            session_id=session_id,
                        )

                if self._acp_mcp_manager is not None:
                    try:
                        async with asyncio.timeout(_MCP_CLEANUP_TIMEOUT):
                            await self._acp_mcp_manager.cleanup_session(session_id)
                    except TimeoutError:
                        logger.warning(
                            "ACP MCP cleanup timed out",
                            session_id=session_id,
                        )
                    except Exception:
                        logger.exception(
                            "Error cleaning up ACP MCP connections",
                            session_id=session_id,
                        )
            finally:
                self._session_contexts.pop(session_id, None)
