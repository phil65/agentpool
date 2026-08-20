"""McpServerCap — wraps an MCP server as a pydantic-ai capability with Resource Protocols.

Replaces :class:`~wolfharness.capabilities.mcp_capability.MCPCapability` with
a clean implementation that delegates to ``MCPClient`` via
``SessionConnectionPool.get_client()`` using lazy initialization.

Implements:
    - ``McpResource``: list_tools, call_tool, list_resources, read_resource, resource_exists
    - ``SkillResource``: list_skills, read_skill, skill_exists (MCP resources → skills)
    - ``CommandResource``: list_commands, get_command (MCP prompts → commands)
    - ``ChangeObservable``: on_change yields ChangeEvent for tools/list_changed,
      resources/list_changed, resources/updated, and prompts/list_changed
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
import logging
from typing import TYPE_CHECKING, Any

from mcp.types import BlobResourceContents, TextResourceContents
from pydantic import BaseModel
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.tools import AgentDepsT, RunContext

from wolfharness.capabilities.change_event import ChangeEvent
from wolfharness.capabilities.resource_protocols import (
    BlobResourceContent,
    ChangeObservable,
    CommandEntry,
    CommandResource,
    CompletionArgument,
    CompletionResult,
    ResourceAccess,
    ResourceEntry,
    ResourcePage,
    ResourceTemplateAccess,
    ResourceTemplateEntry,
    ResourceTemplatePage,
    SkillEntry,
    SkillResource,
    TextResourceContent,
    ToolAccess,
    ToolEntry,
    ToolResult,
)


if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from types import TracebackType

    from pydantic_ai.toolsets import AbstractToolset

    from wolfharness.common_types import JsonObject, JsonValue
    from wolfharness.mcp_server.client import MCPClient
    from wolfharness.mcp_server.session_pool import SessionConnectionPool
    from wolfharness_config.mcp_server import MCPServerConfig


logger = logging.getLogger(__name__)

# Retry constants (migrated from SkillMcpManager).
_DEFAULT_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 1  # seconds


def _normalize_json_object(value: object | None) -> JsonObject | None:
    """Normalize SDK metadata models and mappings to ``JsonObject``."""
    if value is None:
        return None
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", by_alias=True, exclude_none=True)
    if not isinstance(value, Mapping):
        return None
    result: JsonObject = {}
    for key, nested in value.items():
        if isinstance(key, str):
            result[key] = _normalize_json_value(nested)
    return result


def _normalize_json_value(value: object) -> JsonValue:
    """Recursively normalize an SDK metadata value to JSON-compatible types."""
    match value:
        case None | bool() | int() | float() | str():
            return value
        case BaseModel():
            return _normalize_json_value(
                value.model_dump(mode="json", by_alias=True, exclude_none=True)
            )
        case Mapping():
            result: JsonObject = {}
            for key, nested in value.items():
                if isinstance(key, str):
                    result[key] = _normalize_json_value(nested)
            return result
        case Sequence() if not isinstance(value, (str, bytes, bytearray)):
            return [_normalize_json_value(item) for item in value]
        case _:
            return str(value)


class McpServerCap(
    AbstractCapability[AgentDepsT],
    ToolAccess,
    ResourceAccess,
    ResourceTemplateAccess,
    SkillResource,
    CommandResource,
    ChangeObservable,
):
    """Wraps a single MCP server as a capability with Resource Protocol access.

    The client is obtained lazily from ``SessionConnectionPool.get_client()``
    on first access via ``_ensure_client()``. The pool retains ownership of
    the transport lifecycle.

    Implements six Resource Protocols:
        - ``ToolAccess``: MCP tool listing and invocation
        - ``ResourceAccess``: MCP resource listing, reading, existence checking
        - ``ResourceTemplateAccess``: MCP resource template listing and completion
        - ``SkillResource``: MCP resources mapped as skills
        - ``CommandResource``: MCP prompts mapped as commands
        - ``ChangeObservable``: Change events for tool/resource list changes
    """

    def __init__(
        self,
        config: MCPServerConfig,
        session_pool: SessionConnectionPool | None = None,
        *,
        name: str | None = None,
        client: MCPClient | None = None,
        tool_prefix: str | None = None,
    ) -> None:
        """Initialize the capability.

        Args:
            config: MCP server configuration.
            session_pool: Pool for obtaining a shared ``MCPClient``.
                Required if ``client`` is not provided.
            name: Optional name override. Defaults to ``config.client_id``.
            client: Optional pre-created ``MCPClient``. When provided,
                bypasses the session pool and uses this client directly.
            tool_prefix: Optional model-visible tool namespace for MCP tools.
                Passed by ``MCPManager`` for POOL-scope servers so prefixed tool
                names never collide across servers sharing a ``display_name``
                (RFC-0058). When ``None`` (direct-construction paths such as
                ``NativeAgentConfig(mcp_servers=[...])``), tools keep their raw
                MCP names for backward compatibility.
        """
        self._config = config
        self._session_pool = session_pool
        self._name = name or config.client_id
        self._tool_prefix = tool_prefix
        self._client: MCPClient | None = client
        self._change_queues: set[asyncio.Queue[ChangeEvent]] = set()

    # ---- Properties ----

    @property
    def name(self) -> str:
        """Return the capability name."""
        return self._name

    @property
    def tool_prefix(self) -> str | None:
        """Return the model-visible tool namespace for this server."""
        return self._tool_prefix

    @property
    def config(self) -> MCPServerConfig:
        """Return the MCP server config."""
        return self._config

    @property
    def client_name(self) -> str:
        """Return the bare configured server display name.

        Unlike ``name`` (which may be manager-prefixed), this returns the
        server's canonical display name from configuration — the value used
        for catalog keys and resource identity across the system.

        Returns:
            The configured display name (stripped ``name`` field) or the
            generated ``client_id`` if no name is configured.
        """
        return self._config.display_name

    @property
    def client(self) -> MCPClient | None:
        """Return the wrapped MCP client, or None if not yet initialized."""
        return self._client

    # ---- Lazy client initialization ----

    async def _ensure_client(self) -> MCPClient:
        """Lazily obtain and cache the MCPClient from the session pool.

        Retries with exponential backoff (3 attempts, base delay 1s)
        on connection failures. Retry logic migrated from
        ``SkillMcpManager`` (Phase 2, task 2.6b).

        Returns:
            The cached ``MCPClient`` instance.

        Raises:
            RuntimeError: If the session pool is ``None`` or connection
                fails after all retries.
        """
        if self._client is not None:
            return self._client

        if self._session_pool is None:
            raise RuntimeError(
                f"Cannot connect MCP server {self._name!r}: no session pool configured"
            )

        last_error: Exception | None = None
        for attempt in range(1, _DEFAULT_MAX_RETRIES + 1):
            try:
                client = await self._session_pool.get_client(self._config)
            except (OSError, TimeoutError, RuntimeError) as e:
                last_error = e
                if attempt < _DEFAULT_MAX_RETRIES:
                    delay = _RETRY_BASE_DELAY * (2 ** (attempt - 1))
                    logger.warning(
                        "MCP server connection failed, retrying",
                        extra={
                            "server_name": self._name,
                            "attempt": attempt,
                            "delay": delay,
                            "error": str(e),
                        },
                    )
                    await asyncio.sleep(delay)
                continue

            # Success — set up change notification callbacks.
            async def _on_tools_changed() -> None:
                # Cross-layer wiring: this ChangeEvent(kind="tools_changed") is
                # consumed by the OpenCode server's _watch_mcp_tool_changes task
                # (server.py) which converts it to McpToolsChangedEvent and
                # broadcasts it as an SSE event to connected clients.
                event = ChangeEvent(
                    capability_name=self._name,
                    kind="tools_changed",
                    source_uri=f"mcp://{self._name}",
                )
                for q in list(self._change_queues):
                    await q.put(event)

            async def _on_resource_list_changed() -> None:
                event = ChangeEvent(
                    capability_name=self._name,
                    kind="resource_list_changed",
                    source_uri=f"mcp://{self._name}",
                )
                for q in list(self._change_queues):
                    await q.put(event)

            async def _on_resource_updated(uri: str) -> None:
                event = ChangeEvent(
                    capability_name=self._name,
                    kind="resource_updated",
                    source_uri=uri,
                )
                for q in list(self._change_queues):
                    await q.put(event)

            async def _on_prompts_changed() -> None:
                event = ChangeEvent(
                    capability_name=self._name,
                    kind="prompts_changed",
                    source_uri=f"mcp://{self._name}",
                )
                for q in list(self._change_queues):
                    await q.put(event)

            client.set_notification_callbacks(
                tool_change_callback=_on_tools_changed,
                resource_list_changed_callback=_on_resource_list_changed,
                resource_updated_callback=_on_resource_updated,
                prompt_change_callback=_on_prompts_changed,
            )
            self._client = client
            return client

        raise RuntimeError(
            f"Failed to connect MCP server {self._name!r} after {_DEFAULT_MAX_RETRIES} attempts"
        ) from last_error

    # ---- AbstractCapability overrides ----

    def get_toolset(self) -> Any:
        """Return a ``ToolsetFunc`` that lazily builds tool wrappers.

        The returned async callable is invoked once per agent run. It
        obtains the MCPClient via ``_ensure_client()``, lists tools, and
        converts each to a ``FunctionTool`` via ``client.convert_tool()``.

        Returns:
            A ``ToolsetFunc`` or ``None`` if no tools are available.
        """

        async def _build_toolset(
            ctx: RunContext[AgentDepsT],
        ) -> AbstractToolset[AgentDepsT] | None:
            del ctx  # Tools are server-scoped, not run-scoped.
            client = await self._ensure_client()
            tools = await client.list_tools()
            if not tools:
                return None
            from pydantic_ai.toolsets import CombinedToolset, FunctionToolset, PrefixedToolset

            from wolfharness.tools.tool_wrapping import wrap_tool_for_pydantic_ai

            converted = [client.convert_tool(t) for t in tools]
            pydantic_tools = [wrap_tool_for_pydantic_ai(tool) for tool in converted]
            toolsets: list[AbstractToolset[Any]] = [
                FunctionToolset[Any]([tool]) for tool in pydantic_tools
            ]
            if not toolsets:
                return None
            combined = CombinedToolset(toolsets)
            prefix = self._tool_prefix
            if not prefix:
                return combined
            return PrefixedToolset(wrapped=combined, prefix=prefix)

        return _build_toolset

    def get_instructions(self) -> str | None:
        """Return instructions for the system prompt, or ``None``.

        MCP servers do not provide prompt instructions via this method.
        Tool-level instructions are handled by the toolset itself.
        """
        return None

    # ---- ChangeObservable ----

    def on_change(self) -> AsyncIterator[ChangeEvent] | None:
        """Yield ``ChangeEvent`` when the MCP server's tools or resources change.

        Events yielded:
            - ``ChangeEvent(kind="tools_changed")`` on
              ``notifications/tools/list_changed``
            - ``ChangeEvent(kind="resource_list_changed")`` on
              ``notifications/resources/list_changed``
            - ``ChangeEvent(kind="resource_updated", source_uri=uri)`` on
              ``notifications/resources/updated``
            - ``ChangeEvent(kind="prompts_changed")`` on
              ``notifications/prompts/list_changed``

        Returns:
            An async iterator yielding ``ChangeEvent`` instances, or
            ``None`` if change notifications are not supported.
        """
        queue: asyncio.Queue[ChangeEvent] = asyncio.Queue()
        self._change_queues.add(queue)

        async def _generator() -> AsyncIterator[ChangeEvent]:
            try:
                while True:
                    event = await queue.get()
                    yield event
            finally:
                self._change_queues.discard(queue)

        return _generator()

    # ---- ToolAccess ----

    async def list_tools(self) -> Sequence[ToolEntry]:
        """List available MCP tools.

        Returns:
            Sequence of ``ToolEntry`` descriptors.
        """
        client = await self._ensure_client()
        tools = await client.list_tools()
        return [
            ToolEntry(
                name=t.name,
                description=t.description or "",
                schema=t.inputSchema if t.inputSchema else {},
            )
            for t in tools
        ]

    async def call_tool(self, name: str, args: dict[str, Any]) -> ToolResult:
        """Call an MCP tool.

        Args:
            name: Tool name to call.
            args: Arguments to pass to the tool.

        Returns:
            ``ToolResult`` with the tool output.
        """
        from pydantic_ai import RunContext

        client = await self._ensure_client()
        result = await client.call_tool(name, RunContext(deps=None, model=None, usage=None), args)  # type: ignore[arg-type]
        if isinstance(result, str):
            return ToolResult(content=result)
        # ToolReturn or other — extract text representation
        content = getattr(result, "return_value", None)
        if content is None:
            content = str(result)
        return ToolResult(content=str(content))

    # ---- ResourceAccess ----

    async def list_resources(self) -> Sequence[ResourceEntry]:
        """List available MCP resources.

        Returns:
            Sequence of ``ResourceEntry`` descriptors.
        """
        client = await self._ensure_client()
        resources = await client.list_resources()
        return [
            ResourceEntry(
                uri=str(r.uri),
<<<<<<< Updated upstream
                name=r.title or r.name,
=======
                name=r.name,
                title=r.title or "",
>>>>>>> Stashed changes
                description=r.description or "",
                mime_type=r.mimeType if r.mimeType else "",
                size=r.size,
                annotations=_normalize_json_object(r.annotations),
                meta=_normalize_json_object(r.meta),
            )
            for r in resources
        ]

    async def supports_resources(self) -> bool:
        """Return whether this server advertised the MCP resources capability."""
        client = await self._ensure_client()
        return client.supports_resources()

    async def list_resources_page(self, cursor: str | None = None) -> ResourcePage:
        """Return one upstream resource page for model-visible pagination."""
        client = await self._ensure_client()
        page = await client.list_resources_page(cursor)
        return ResourcePage(
            entries=[
                ResourceEntry(
                    uri=str(resource.uri),
                    name=resource.name,
                    title=resource.title or "",
                    description=resource.description or "",
                    mime_type=resource.mimeType or "",
                    size=resource.size,
                    annotations=_normalize_json_object(resource.annotations),
                    meta=_normalize_json_object(resource.meta),
                )
                for resource in page.resources
            ],
            next_cursor=str(page.nextCursor) if page.nextCursor else None,
        )

    async def read_resource(
        self, uri: str
    ) -> list[TextResourceContent | BlobResourceContent] | None:
        """Read an MCP resource by URI.

        Args:
            uri: Resource URI to read.

        Returns:
            List of ``TextResourceContent`` and/or ``BlobResourceContent``
            instances, or ``None`` if not found.
        """
        client = await self._ensure_client()
        try:
            contents = await client.read_resource(uri)
        except RuntimeError as exc:
            if "not found" in str(exc).lower():
                return None
            raise
        if not contents:
            return None
        result: list[TextResourceContent | BlobResourceContent] = []
        for c in contents:
            if isinstance(c, TextResourceContents):
                result.append(
                    TextResourceContent(
                        uri=str(c.uri),
                        mime_type=c.mimeType,
                        meta=_normalize_json_object(c.meta),
                        text=c.text,
                    )
                )
            elif isinstance(c, BlobResourceContents):
                result.append(
                    BlobResourceContent(
                        uri=str(c.uri),
                        mime_type=c.mimeType,
                        meta=_normalize_json_object(c.meta),
                        blob=c.blob,
                    )
                )
        return result if result else None

    async def resource_exists(self, uri: str) -> bool:
        """Check if an MCP resource exists.

        Args:
            uri: Resource URI to check.

        Returns:
            ``True`` if the resource exists, ``False`` otherwise.
        """
        client = await self._ensure_client()
        try:
            resources = await client.list_resources()
        except Exception:  # noqa: BLE001
            return False
        return any(str(r.uri) == uri for r in resources)

    # ---- ResourceTemplateAccess ----

    async def list_resource_templates(self) -> Sequence[ResourceTemplateEntry]:
        """List available MCP resource templates.

        Returns:
            Sequence of ``ResourceTemplateEntry`` descriptors.
        """
        client = await self._ensure_client()
        templates = await client.list_resource_templates()
        return [
            ResourceTemplateEntry(
                uri_template=str(t.uriTemplate),
                name=t.name or "",
                title=t.title or "",
                description=t.description or "",
                mime_type=t.mimeType if t.mimeType else "",
                annotations=_normalize_json_object(t.annotations),
                meta=_normalize_json_object(t.meta),
            )
            for t in templates
        ]

    async def list_resource_templates_page(self, cursor: str | None = None) -> ResourceTemplatePage:
        """Return one upstream resource-template page."""
        client = await self._ensure_client()
        page = await client.list_resource_templates_page(cursor)
        return ResourceTemplatePage(
            entries=[
                ResourceTemplateEntry(
                    uri_template=str(template.uriTemplate),
                    name=template.name,
                    title=template.title or "",
                    description=template.description or "",
                    mime_type=template.mimeType or "",
                    annotations=_normalize_json_object(template.annotations),
                    meta=_normalize_json_object(template.meta),
                )
                for template in page.resourceTemplates
            ],
            next_cursor=str(page.nextCursor) if page.nextCursor else None,
        )

    async def complete_resource_template(
        self,
        uri_template: str,
        argument: CompletionArgument,
        context: dict[str, str] | None = None,
    ) -> CompletionResult:
        """Complete a resource template parameter via MCP completion.

        Args:
            uri_template: The URI template to complete.
            argument: The argument being completed.
            context: Optional context arguments.

        Returns:
            ``CompletionResult`` with suggestion values.

        Raises:
            NotImplementedError: If the MCP server does not support completion.
        """
        client = await self._ensure_client()
        completion = await client.complete(
            ref_type="ref/resource",
            ref_uri=uri_template,
            argument_name=argument.name,
            argument_value=argument.value,
            context=context,
        )
        return CompletionResult(
            values=list(completion.values),
            total=getattr(completion, "total", None),
            has_more=getattr(completion, "hasMore", None),
        )

    # ---- SkillResource ----

    async def list_skills(self) -> Sequence[SkillEntry]:
        """List MCP resources as skills.

        Maps MCP resources to ``SkillEntry`` descriptors. Each resource
        becomes a skill with ``source="remote"`` and a ``skill://`` URI.

        Returns:
            Sequence of ``SkillEntry`` descriptors.
        """
        client = await self._ensure_client()
        try:
            resources = await client.list_resources()
        except Exception:
            logger.warning("Failed to list resources for skills", exc_info=True)
            return []
        entries: list[SkillEntry] = []
        for r in resources:
            skill_name = r.name or str(r.uri)
            entries.append(
                SkillEntry(
                    name=skill_name,
                    description=r.description or "",
                    uri=f"skill://{self._name}/{skill_name}",
                    source="remote",
                )
            )
        return entries

    async def read_skill(self, name: str) -> str | None:
        """Read skill content by name or URI.

        Tries to find an MCP resource matching the given name or URI,
        then reads its content. If ``name`` is a ``skill://`` URI,
        extracts the short skill name before matching.

        Args:
            name: Skill name or resource URI to read.

        Returns:
            Skill content as string, or ``None`` if not found.
        """
        client = await self._ensure_client()
        try:
            resources = await client.list_resources()
        except Exception:  # noqa: BLE001
            return None

        # Extract short name from skill:// URI if present.
        lookup_name = name
        if name.startswith("skill://"):
            path = name[len("skill://") :]
            lookup_name = path.split("/")[-1] if path else name

        # Find matching resource by name or URI.
        target_uri: str | None = None
        for r in resources:
            if (
                r.name == lookup_name
                or str(r.uri) == lookup_name
                or r.name == name
                or str(r.uri) == name
            ):
                target_uri = str(r.uri)
                break
        if target_uri is None:
            return None

        try:
            contents = await client.read_resource(target_uri)
        except Exception:
            logger.warning("Failed to read skill %r from MCP", name, exc_info=True)
            return None
        if not contents:
            return None
        first = contents[0]
        text: str | None = getattr(first, "text", None)
        if text is not None:
            return text
        return str(first)

    async def skill_exists(self, name: str) -> bool:
        """Check if a skill exists without reading it.

        If ``name`` is a ``skill://`` URI, extracts the short skill
        name before checking.

        Args:
            name: Skill name or resource URI to check.

        Returns:
            ``True`` if the skill exists, ``False`` otherwise.
        """
        client = await self._ensure_client()
        try:
            resources = await client.list_resources()
        except Exception:  # noqa: BLE001
            return False

        # Extract short name from skill:// URI if present.
        lookup_name = name
        if name.startswith("skill://"):
            path = name[len("skill://") :]
            lookup_name = path.split("/")[-1] if path else name

        return any(
            r.name == lookup_name
            or str(r.uri) == lookup_name
            or r.name == name
            or str(r.uri) == name
            for r in resources
        )

    # ---- CommandResource ----

    async def list_commands(self) -> Sequence[CommandEntry]:
        """List MCP prompts as commands.

        Maps MCP prompts to ``CommandEntry`` descriptors. Each prompt
        becomes a command with ``source="remote"``.

        Returns:
            Sequence of ``CommandEntry`` descriptors.
        """
        client = await self._ensure_client()
        try:
            prompts = await client.list_prompts()
        except Exception:
            logger.warning("Failed to list prompts for commands", exc_info=True)
            return []
        return [
            CommandEntry(
                name=p.name,
                description=p.description or "",
                skill_uri=f"skill://{self._name}/{p.name}",
                source="remote",
            )
            for p in prompts
        ]

    async def get_command(self, name: str) -> CommandEntry | None:
        """Get a specific command by name.

        Finds an MCP prompt matching the given name.

        Args:
            name: Command name to retrieve.

        Returns:
            ``CommandEntry`` if found, ``None`` otherwise.
        """
        client = await self._ensure_client()
        try:
            prompts = await client.list_prompts()
        except Exception:  # noqa: BLE001
            return None
        for p in prompts:
            if p.name == name:
                return CommandEntry(
                    name=p.name,
                    description=p.description or "",
                    skill_uri=f"skill://{self._name}/{p.name}",
                    source="remote",
                )
        return None

    # ---- Lifecycle ----

    async def __aenter__(self) -> McpServerCap[AgentDepsT]:
        """Enter async context.

        If a pre-created client was provided, connects it.
        Otherwise, lazy — does not connect.
        """
        if self._client is not None:
            await self._client.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Exit async context.

        If a pre-created client was provided, closes it.
        Otherwise, does NOT close the session pool or transport —
        the pool retains ownership of the transport lifecycle.
        The cached client reference is cleared so a new client will be
        obtained on next use.
        """
        if self._session_pool is None and self._client is not None:
            await self._client.__aexit__(exc_type, exc_val, exc_tb)
        self._client = None
        self._change_queues.clear()
