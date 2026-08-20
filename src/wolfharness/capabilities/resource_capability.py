"""ResourceCapability — unified MCP resource access via three agent tools.

Provides a single ``AbstractCapability`` that aggregates resource access
across all visible ``ResourceAccess``, ``SkillResource``, and
``ResourceTemplateAccess`` providers registered in the
``ExtensionRegistry``. The capability is stateless — it reads
``ctx.deps`` (an ``AgentContextDeps``) at runtime to resolve providers.

The model-visible surface mirrors OpenCode's three MCP resource tools. Legacy
Python methods remain callable for compatibility but are not registered in the
toolset.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Literal, Protocol

import logfire
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic_ai import ToolReturn
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.tools import AgentDepsT, RunContext
from pydantic_ai.toolsets import AgentToolset, FunctionToolset

from wolfharness.capabilities.extension_registry import Scope, ScopeLevel
from wolfharness.capabilities.mcp_server_cap import McpServerCap
from wolfharness.capabilities.resource_protocols import (
    BlobResourceContent,
    CompletionArgument,
    CompletionResult,
    McpBlobContentResult,
    McpResourceError,
    McpResourceItem,
    McpResourceListResult,
    McpResourceReadResult,
    McpResourceTemplateItem,
    McpResourceTemplateListResult,
    McpTextContentResult,
    ResourceAccess,
    ResourceEntry,
    ResourcePage,
    ResourceTemplateEntry,
    ResourceTemplatePage,
    TextResourceContent,
)


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from pydantic_ai.messages import BinaryContent

    from wolfharness.capabilities.agent_context import AgentContextDeps
    from wolfharness.tools.base import Tool


# Number of header lines (header + separator) before data rows.
_HEADER_LINE_COUNT = 2

# Default pagination limits.
_DEFAULT_LIST_LIMIT = 50
_DEFAULT_READ_TEXT_LIMIT = 10_000
_MAX_COMPLETION_SUGGESTIONS = 100

# MIME types allowed for binary resource content returned to the agent.
# Mirrors OpenCode's MAX_MCP_RESOURCE_BLOB_BYTES attachment filter.
_BLOB_MIME_ALLOWLIST = frozenset({
    "application/pdf",
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/webp",
})

# Maximum blob size (10 MB) for binary content returned to the agent.
_MAX_BLOB_SIZE_BYTES = 10 * 1024 * 1024


class _McpListCursor(BaseModel):
    """Validated opaque cursor state for cross-server pagination."""

    model_config = ConfigDict(frozen=True)

    version: Literal[1] = 1
    server_filter: str | None = None
    current_server: str
    upstream_cursor: str | None = None
    offset: int = Field(default=0, ge=0)


class _Page[T](Protocol):
    """Structural page shape shared by resource and template pages."""

    @property
    def entries(self) -> list[T]:
        """Return the entries on this upstream page."""
        ...

    @property
    def next_cursor(self) -> str | None:
        """Return the opaque upstream continuation cursor."""
        ...


@dataclass(frozen=True, slots=True)
class _PaginationOutcome[T]:
    """Internal result of paginating across ordered MCP providers."""

    items: list[T]
    next_state: _McpListCursor | None
    errors: list[McpResourceError]
    invalid_cursor: bool = False


class ResourceCapability(AbstractCapability[AgentDepsT]):
    """Unified resource access capability providing three agent-facing tools.

    Aggregates resources from all visible providers (MCP servers, local
    skills) via the ``ExtensionRegistry`` on ``AgentContextDeps``. The
    capability is stateless — no resources are held between turns.

    Tools route by URI scheme to ``ResourceAccess`` providers. ``skill://``
    resolution is retained as a silent, non-advertised fallback (see
    ``resource_resolver.resolve_resource_content``) for protocol consumers.
    """

    def __init__(self, *, toolset_id: str = "resource_access") -> None:
        """Initialize the resource capability.

        Args:
            toolset_id: Identifier for the produced ``FunctionToolset``.
        """
        self._toolset_id = toolset_id

    @property
    def name(self) -> str:
        """Return the capability name."""
        return "resource_capability"

    async def __aenter__(self) -> ResourceCapability[AgentDepsT]:
        """Enter async context — no-op (stateless capability)."""
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        """Exit async context — no-op (stateless capability)."""

    def get_instructions(self) -> str | None:
        """Return brief system prompt instructions about resource tools.

        Returns:
            A short instruction string describing available resource
            management tools and supported URI schemes.
        """
        return (
            "You can discover and read MCP resources with list_mcp_resources, "
            "list_mcp_resource_templates, and read_mcp_resource. Treat resource "
            "URIs as opaque: only read URIs returned by list, search, completion, "
            "or a previous resource response. Templates describe URI shapes; "
            "never invent template parameter values. Read progressively and only "
            "fetch binary content when it is needed."
        )

    @logfire.instrument("capability.resource_capability.get_toolset")
    def get_toolset(self) -> AgentToolset[AgentDepsT] | None:
        """Return the three model-visible MCP resource tools.

        The tools access ``ctx.deps`` at runtime, which must be an
        ``AgentContextDeps`` with an ``extension_registry`` field.
        """
        return FunctionToolset(
            [
                self.list_mcp_resources,
                self.list_mcp_resource_templates,
                self.read_mcp_resource,
            ],
            id=self._toolset_id,
        )

    async def get_tools(self) -> Sequence[Tool[object]]:
        """Return the formal tools for Host-side tool discovery endpoints."""
        from wolfharness.tools.base import FunctionTool

        return [
            FunctionTool.from_callable(self.list_mcp_resources),
            FunctionTool.from_callable(self.list_mcp_resource_templates),
            FunctionTool.from_callable(self.read_mcp_resource),
        ]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_agent_context(ctx: RunContext[AgentDepsT]) -> AgentContextDeps:
        """Extract the ``AgentContextDeps`` from the run context deps.

        Delegates to the shared ``resolve_agent_context_from_deps`` utility
        which handles both the production path (``RuntimeAgentContext.data``)
        and the test path (direct ``AgentContextDeps``).

        Args:
            ctx: The pydantic-ai run context.

        Returns:
            The ``AgentContextDeps`` instance from ``ctx.deps`` (or ``ctx.deps.data``).

        Raises:
            RuntimeError: If deps is None or AgentContextDeps is not found.
        """
        from wolfharness.capabilities.agent_context import resolve_agent_context_from_deps

        return resolve_agent_context_from_deps(ctx.deps, capability_name="ResourceCapability")

    @staticmethod
    def _make_scope(agent_ctx: AgentContextDeps) -> Scope:
        """Build a ``Scope`` from ``AgentContextDeps`` fields.

        Uses SESSION level to get the complete view (POOL + AGENT + SESSION).

        Args:
            agent_ctx: The per-turn agent context.

        Returns:
            A ``Scope`` at SESSION level with agent and session identifiers.
        """
        session_id = agent_ctx.session.session_id if agent_ctx.session else ""
        return Scope(
            level=ScopeLevel.SESSION,
            agent_name=agent_ctx.agent_name,
            session_id=session_id,
        )

    @staticmethod
    def _extract_skill_name(uri: str) -> str:
        """Extract the skill name from a ``skill://`` URI.

        Takes the first path segment after ``skill://``.

        Args:
            uri: A ``skill://`` URI.

        Returns:
            The skill name (first path segment).
        """
        path = uri[len("skill://") :]
        return path.split("/")[0] if path else ""

    @staticmethod
    def _encode_cursor(state: _McpListCursor) -> str:
        """Encode validated pagination state as an opaque URL-safe cursor."""
        raw = state.model_dump_json().encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    @staticmethod
    def _decode_cursor(cursor: str) -> _McpListCursor | None:
        """Decode an opaque cursor, returning ``None`` when it is invalid."""
        try:
            padding = "=" * (-len(cursor) % 4)
            raw = base64.urlsafe_b64decode((cursor + padding).encode())
            return _McpListCursor.model_validate_json(raw)
        except (binascii.Error, UnicodeDecodeError, ValidationError, ValueError):
            return None

    @staticmethod
    def _mcp_providers(agent_ctx: AgentContextDeps) -> list[McpServerCap[AgentDepsT]]:
        """Return visible MCP resource providers in stable server-name order."""
        registry = agent_ctx.extension_registry
        if registry is None:
            return []
        scope = ResourceCapability._make_scope(agent_ctx)
        providers = [
            provider
            for provider in registry.get_resource_access(scope)
            if isinstance(provider, McpServerCap)
        ]
        return sorted(providers, key=lambda provider: provider.client_name)

    @staticmethod
    def _provider_error(
        server: str,
        exc: OSError | RuntimeError,
        *,
        uri: str | None = None,
    ) -> McpResourceError:
        """Map provider exceptions to the stable agent-facing error taxonomy."""
        message = str(exc)
        normalized = message.lower()
        if isinstance(exc, PermissionError) or any(
            marker in normalized for marker in ("permission", "unauthorized", "forbidden")
        ):
            return McpResourceError(
                code="permission_denied",
                message=message,
                retryable=False,
                suggestion="Use a server connection with permission to read this resource.",
                server=server,
                uri=uri,
            )
        if isinstance(exc, TimeoutError) or "timeout" in normalized:
            return McpResourceError(
                code="timeout",
                message=message,
                retryable=True,
                suggestion="Retry the request or select a smaller resource.",
                server=server,
                uri=uri,
            )
        if "does not support resources" in normalized:
            return McpResourceError(
                code="resources_not_supported",
                message=message,
                retryable=False,
                suggestion="Choose a server returned by list_mcp_resources.",
                server=server,
                uri=uri,
            )
        return McpResourceError(
            code="provider_unavailable",
            message=message,
            retryable=True,
            suggestion="Check the MCP server status and retry.",
            server=server,
            uri=uri,
        )

    @staticmethod
    def _invalid_cursor_error(server: str | None = None) -> McpResourceError:
        """Build the stable invalid-cursor error."""
        return McpResourceError(
            code="invalid_cursor",
            message="The pagination cursor is invalid or no longer matches the server filter.",
            retryable=False,
            suggestion="Call the list tool again without cursor to restart pagination.",
            server=server,
        )

    async def _paginate[TEntry, TItem](
        self,
        providers: list[McpServerCap[AgentDepsT]],
        state: _McpListCursor | None,
        *,
        server_filter: str | None,
        limit: int,
        load_page: Callable[[McpServerCap[AgentDepsT], str | None], Awaitable[_Page[TEntry]]],
        map_entry: Callable[[str, TEntry], TItem],
    ) -> _PaginationOutcome[TItem]:
        """Page through stable server order while preserving upstream cursors."""
        provider_names = [provider.client_name for provider in providers]
        if state is not None and state.current_server not in provider_names:
            return _PaginationOutcome([], None, [], invalid_cursor=True)

        provider_index = provider_names.index(state.current_server) if state else 0
        upstream_cursor = state.upstream_cursor if state else None
        page_offset = state.offset if state else 0
        items: list[TItem] = []
        errors: list[McpResourceError] = []
        seen_pages: set[tuple[str, str | None, int]] = set()

        while provider_index < len(providers) and len(items) < limit:
            provider = providers[provider_index]
            page_key = (provider.client_name, upstream_cursor, page_offset)
            if page_key in seen_pages:
                errors.append(
                    McpResourceError(
                        code="provider_unavailable",
                        message=f"MCP server {provider.client_name!r} repeated a pagination state.",
                        retryable=False,
                        suggestion="Restart pagination without a cursor.",
                        server=provider.client_name,
                    )
                )
                provider_index += 1
                upstream_cursor = None
                page_offset = 0
                continue
            seen_pages.add(page_key)

            try:
                supports_resources = await provider.supports_resources()
                page = await load_page(provider, upstream_cursor) if supports_resources else None
            except (OSError, RuntimeError) as exc:
                errors.append(self._provider_error(provider.client_name, exc))
                provider_index += 1
                upstream_cursor = None
                page_offset = 0
                continue
            if page is None:
                errors.append(
                    McpResourceError(
                        code="resources_not_supported",
                        message=f"MCP server {provider.client_name!r} does not support resources.",
                        retryable=False,
                        suggestion="Choose a server returned by list_mcp_resources.",
                        server=provider.client_name,
                    )
                )
                provider_index += 1
                upstream_cursor = None
                page_offset = 0
                continue
            if page_offset > len(page.entries):
                return _PaginationOutcome([], None, [], invalid_cursor=True)

            available = page.entries[page_offset:]
            take_count = min(limit - len(items), len(available))
            items.extend(map_entry(provider.client_name, entry) for entry in available[:take_count])
            new_offset = page_offset + take_count
            if new_offset < len(page.entries):
                return _PaginationOutcome(
                    items,
                    _McpListCursor(
                        server_filter=server_filter,
                        current_server=provider.client_name,
                        upstream_cursor=upstream_cursor,
                        offset=new_offset,
                    ),
                    errors,
                )
            if page.next_cursor is not None:
                upstream_cursor = page.next_cursor
                page_offset = 0
                if len(items) == limit:
                    return _PaginationOutcome(
                        items,
                        _McpListCursor(
                            server_filter=server_filter,
                            current_server=provider.client_name,
                            upstream_cursor=upstream_cursor,
                        ),
                        errors,
                    )
                continue

            provider_index += 1
            upstream_cursor = None
            page_offset = 0
            if provider_index < len(providers) and len(items) == limit:
                return _PaginationOutcome(
                    items,
                    _McpListCursor(
                        server_filter=server_filter,
                        current_server=providers[provider_index].client_name,
                    ),
                    errors,
                )

        return _PaginationOutcome(items, None, errors)

    @staticmethod
    async def _load_resource_page(
        provider: McpServerCap[AgentDepsT], cursor: str | None
    ) -> ResourcePage:
        return await provider.list_resources_page(cursor)

    @staticmethod
    async def _load_template_page(
        provider: McpServerCap[AgentDepsT], cursor: str | None
    ) -> ResourceTemplatePage:
        return await provider.list_resource_templates_page(cursor)

    @staticmethod
    def _map_resource(server: str, entry: ResourceEntry) -> McpResourceItem:
        return McpResourceItem(
            server=server,
            uri=entry.uri,
            name=entry.name,
            title=entry.title,
            description=entry.description,
            mime_type=entry.mime_type,
            size=entry.size,
            annotations=entry.annotations,
            meta=entry.meta,
        )

    @staticmethod
    def _map_template(server: str, entry: ResourceTemplateEntry) -> McpResourceTemplateItem:
        return McpResourceTemplateItem(
            server=server,
            uri_template=entry.uri_template,
            name=entry.name,
            title=entry.title,
            description=entry.description,
            mime_type=entry.mime_type,
            annotations=entry.annotations,
            meta=entry.meta,
        )

    @staticmethod
    def _read_failure(
        error: McpResourceError, server: str, uri: str
    ) -> ToolReturn[McpResourceReadResult]:
        """Wrap a structured read failure in a typed tool return."""
        return ToolReturn(
            return_value=McpResourceReadResult(
                summary=error.message,
                server=server,
                requested_uri=uri,
                contents=[],
                error=error,
            )
        )

    @staticmethod
    def _convert_blob(
        server: str, item: BlobResourceContent
    ) -> tuple[McpBlobContentResult, BinaryContent | None, McpResourceError | None]:
        """Convert a base64 blob to safe metadata plus an optional attachment."""
        from pydantic_ai.messages import BinaryContent

        mime_type = item.mime_type or "application/octet-stream"
        try:
            blob = base64.b64decode(item.blob, validate=True) if item.blob else b""
        except (binascii.Error, ValueError) as exc:
            error = McpResourceError(
                code="provider_unavailable",
                message=f"Resource {item.uri!r} contained invalid base64 data: {exc}",
                retryable=True,
                suggestion="Retry the read or report the malformed resource to the server owner.",
                server=server,
                uri=item.uri,
            )
            result = McpBlobContentResult(
                type="blob",
                uri=item.uri,
                mime_type=mime_type,
                size=0,
                attached=False,
                omission_reason=error.message,
                meta=item.meta,
            )
            return result, None, error

        size = len(blob)
        if mime_type in _BLOB_MIME_ALLOWLIST and size <= _MAX_BLOB_SIZE_BYTES:
            result = McpBlobContentResult(
                type="blob",
                uri=item.uri,
                mime_type=mime_type,
                size=size,
                attached=True,
                meta=item.meta,
            )
            return result, BinaryContent(data=blob, media_type=mime_type), None

        if mime_type not in _BLOB_MIME_ALLOWLIST:
            code: Literal["unsupported_mime_type", "content_too_large"] = "unsupported_mime_type"
            reason = f"MIME type {mime_type!r} is not allowed for model attachment."
            suggestion = "Read a text or supported image/PDF representation instead."
        else:
            code = "content_too_large"
            reason = f"Binary resource is {size} bytes and exceeds the 10 MB limit."
            suggestion = "Use a smaller or ranged resource URI supplied by the server."
        error = McpResourceError(
            code=code,
            message=reason,
            retryable=False,
            suggestion=suggestion,
            server=server,
            uri=item.uri,
        )
        result = McpBlobContentResult(
            type="blob",
            uri=item.uri,
            mime_type=mime_type,
            size=size,
            attached=False,
            omission_reason=reason,
            meta=item.meta,
        )
        return result, None, error

    # ------------------------------------------------------------------
    # Model-visible MCP resource tools
    # ------------------------------------------------------------------

    @logfire.instrument("capability.resource_capability.list_mcp_resources")
    async def list_mcp_resources(
        self,
        ctx: RunContext[AgentDepsT],
        server: Annotated[
            str | None,
            Field(description="Optional configured MCP server name."),
        ] = None,
        cursor: Annotated[
            str | None,
            Field(description="Opaque cursor returned by the previous list call."),
        ] = None,
        limit: Annotated[
            int,
            Field(ge=1, le=100, description="Maximum resources to return, from 1 to 100."),
        ] = _DEFAULT_LIST_LIMIT,
    ) -> McpResourceListResult:
        """List MCP resources without guessing or expanding resource templates."""
        agent_ctx = self._resolve_agent_context(ctx)
        providers = self._mcp_providers(agent_ctx)
        if server is not None:
            providers = [provider for provider in providers if provider.client_name == server]
            if not providers:
                error = McpResourceError(
                    code="unknown_server",
                    message=(
                        f"MCP server {server!r} is not connected or does not provide resources."
                    ),
                    retryable=False,
                    suggestion="Call list_mcp_resources without a server filter.",
                    server=server,
                )
                return McpResourceListResult(
                    summary=error.message,
                    resources=[],
                    errors=[error],
                )
        if not providers:
            return McpResourceListResult(
                summary="No connected MCP server currently provides resources.",
                resources=[],
            )

        state = None
        if cursor is not None:
            state = self._decode_cursor(cursor)
            if state is None or state.server_filter != server:
                error = self._invalid_cursor_error(server)
                return McpResourceListResult(
                    summary=error.message,
                    resources=[],
                    errors=[error],
                )

        outcome = await self._paginate(
            providers,
            state,
            server_filter=server,
            limit=limit,
            load_page=self._load_resource_page,
            map_entry=self._map_resource,
        )
        if outcome.invalid_cursor:
            error = self._invalid_cursor_error(server)
            return McpResourceListResult(
                summary=error.message,
                resources=[],
                errors=[error],
            )
        next_cursor = self._encode_cursor(outcome.next_state) if outcome.next_state else None
        summary = f"Returned {len(outcome.items)} MCP resource(s)."
        if next_cursor:
            summary += " More resources are available; pass next_cursor as cursor."
        if outcome.errors:
            summary += f" {len(outcome.errors)} server error(s) were reported."
        return McpResourceListResult(
            summary=summary,
            resources=outcome.items,
            next_cursor=next_cursor,
            errors=outcome.errors,
        )

    @logfire.instrument("capability.resource_capability.list_mcp_resource_templates")
    async def list_mcp_resource_templates(
        self,
        ctx: RunContext[AgentDepsT],
        server: Annotated[
            str | None,
            Field(description="Optional configured MCP server name."),
        ] = None,
        cursor: Annotated[
            str | None,
            Field(description="Opaque cursor returned by the previous list call."),
        ] = None,
        limit: Annotated[
            int,
            Field(ge=1, le=100, description="Maximum templates to return, from 1 to 100."),
        ] = _DEFAULT_LIST_LIMIT,
    ) -> McpResourceTemplateListResult:
        """List MCP resource templates without expanding their parameters."""
        agent_ctx = self._resolve_agent_context(ctx)
        providers = self._mcp_providers(agent_ctx)
        if server is not None:
            providers = [provider for provider in providers if provider.client_name == server]
            if not providers:
                error = McpResourceError(
                    code="unknown_server",
                    message=(
                        f"MCP server {server!r} is not connected or does not provide resources."
                    ),
                    retryable=False,
                    suggestion="Call list_mcp_resource_templates without a server filter.",
                    server=server,
                )
                return McpResourceTemplateListResult(
                    summary=error.message,
                    templates=[],
                    errors=[error],
                )
        if not providers:
            return McpResourceTemplateListResult(
                summary="No connected MCP server currently provides resource templates.",
                templates=[],
            )

        state = None
        if cursor is not None:
            state = self._decode_cursor(cursor)
            if state is None or state.server_filter != server:
                error = self._invalid_cursor_error(server)
                return McpResourceTemplateListResult(
                    summary=error.message,
                    templates=[],
                    errors=[error],
                )

        outcome = await self._paginate(
            providers,
            state,
            server_filter=server,
            limit=limit,
            load_page=self._load_template_page,
            map_entry=self._map_template,
        )
        if outcome.invalid_cursor:
            error = self._invalid_cursor_error(server)
            return McpResourceTemplateListResult(
                summary=error.message,
                templates=[],
                errors=[error],
            )
        next_cursor = self._encode_cursor(outcome.next_state) if outcome.next_state else None
        summary = f"Returned {len(outcome.items)} MCP resource template(s)."
        if next_cursor:
            summary += " More templates are available; pass next_cursor as cursor."
        if outcome.errors:
            summary += f" {len(outcome.errors)} server error(s) were reported."
        return McpResourceTemplateListResult(
            summary=summary,
            templates=outcome.items,
            next_cursor=next_cursor,
            errors=outcome.errors,
        )

    @logfire.instrument("capability.resource_capability.read_mcp_resource")
    async def read_mcp_resource(
        self,
        ctx: RunContext[AgentDepsT],
        server: Annotated[str, Field(description="Configured MCP server name.")],
        uri: Annotated[
            str,
            Field(description="Exact resource URI returned by list, search, or a prior read."),
        ],
    ) -> ToolReturn[McpResourceReadResult]:
        """Read one exact MCP resource identified by ``server`` and opaque URI."""
        agent_ctx = self._resolve_agent_context(ctx)
        provider = next(
            (
                candidate
                for candidate in self._mcp_providers(agent_ctx)
                if candidate.client_name == server
            ),
            None,
        )
        if provider is None:
            error = McpResourceError(
                code="unknown_server",
                message=f"MCP server {server!r} is not connected or does not provide resources.",
                retryable=False,
                suggestion="Call list_mcp_resources to discover available server names.",
                server=server,
                uri=uri,
            )
            return self._read_failure(error, server, uri)

        try:
            supports_resources = await provider.supports_resources()
        except (OSError, RuntimeError) as exc:
            error = self._provider_error(server, exc, uri=uri)
            return self._read_failure(error, server, uri)
        if not supports_resources:
            error = McpResourceError(
                code="resources_not_supported",
                message=f"MCP server {server!r} does not support resources.",
                retryable=False,
                suggestion="Choose a server returned by list_mcp_resources.",
                server=server,
                uri=uri,
            )
            return self._read_failure(error, server, uri)
        try:
            contents = await provider.read_resource(uri)
        except (OSError, RuntimeError) as exc:
            error = self._provider_error(server, exc, uri=uri)
            return self._read_failure(error, server, uri)

        if not contents:
            error = McpResourceError(
                code="resource_not_found",
                message=f"Resource {uri!r} was not found on MCP server {server!r}.",
                retryable=False,
                suggestion="Use a URI returned by list_mcp_resources or a server search tool.",
                server=server,
                uri=uri,
            )
            return self._read_failure(error, server, uri)

        result_contents: list[McpTextContentResult | McpBlobContentResult] = []
        attachments: list[BinaryContent] = []
        partial_error: McpResourceError | None = None
        for item in contents:
            if isinstance(item, TextResourceContent):
                original_char_count = len(item.text)
                truncated = original_char_count > _DEFAULT_READ_TEXT_LIMIT
                text = item.text[:_DEFAULT_READ_TEXT_LIMIT] if truncated else item.text
                result_contents.append(
                    McpTextContentResult(
                        type="text",
                        uri=item.uri,
                        mime_type=item.mime_type,
                        text=text,
                        truncated=truncated,
                        original_char_count=original_char_count,
                        meta=item.meta,
                    )
                )
                continue

            blob_result, attachment, blob_error = self._convert_blob(server, item)
            result_contents.append(blob_result)
            if attachment is not None:
                attachments.append(attachment)
            if blob_error is not None:
                partial_error = partial_error or blob_error

        summary = f"Read {len(result_contents)} content block(s) from {server}:{uri}."
        if any(
            isinstance(item, McpTextContentResult) and item.truncated for item in result_contents
        ):
            summary += " One or more text blocks were truncated to 10,000 characters."
        if partial_error:
            summary += f" Partial content warning: {partial_error.message}"
        result = McpResourceReadResult(
            summary=summary,
            server=server,
            requested_uri=uri,
            contents=result_contents,
            error=partial_error,
        )
        return ToolReturn(
            return_value=result,
            content=attachments if attachments else None,
        )

    # ------------------------------------------------------------------
    # Legacy Python compatibility methods (not model-visible)
    # ------------------------------------------------------------------

    @logfire.instrument("capability.resource_capability.list_resources")
    async def list_resources(
        self,
        ctx: RunContext[AgentDepsT],
        server: Annotated[
            str | None,
            Field(
                description=(
                    "Optional server name to filter resources by. "
                    "If omitted, lists resources from all connected servers."
                )
            ),
        ] = None,
        limit: Annotated[
            int,
            Field(description="Maximum number of resources to return (default: 50)"),
        ] = _DEFAULT_LIST_LIMIT,
        offset: Annotated[
            int,
            Field(description="Number of resources to skip for pagination"),
        ] = 0,
    ) -> str:
        """List available resources from connected MCP servers and local files.

        Results are paginated. Use ``offset`` to page through large result sets.
        Pass ``server`` to filter resources from a specific provider.

        Args:
            ctx: The run context providing agent dependencies.
            server: Optional server name to filter by.
            limit: Maximum number of resources to return.
            offset: Number of resources to skip.
        """
        agent_ctx = self._resolve_agent_context(ctx)
        registry = agent_ctx.extension_registry
        if registry is None:
            return "No resources available."

        scope = self._make_scope(agent_ctx)
        rows: list[str] = []

        # ResourceAccess providers
        for resource_cap in registry.get_resource_access(scope):
            if isinstance(resource_cap, McpServerCap):
                source = resource_cap.client_name
            else:
                source = type(resource_cap).__name__
            if server is not None and source != server:
                continue
            try:
                resource_entries = await resource_cap.list_resources()
            except Exception:  # noqa: BLE001
                logfire.warning(
                    "Failed to list resources from {source}",
                    source=source,
                )
                continue
            rows.extend(
                f"{source:<25} {entry.uri:<45} {entry.name:<20} "
                f"{entry.description:<30} {entry.mime_type:<15}"
                for entry in resource_entries
            )

        if not rows:
            return "No resources available."

        total = len(rows)
        paginated = rows[offset : offset + limit]

        if not paginated:
            if offset > 0:
                return f"No resources at offset {offset}. Total: {total} resource(s)."
            return "No resources available."

        header = f"{'Source':<25} {'URI':<45} {'Name':<20} {'Description':<30} {'MIME Type':<15}"
        lines = [header, "-" * len(header)]
        lines.extend(paginated)

        remaining = total - offset - len(paginated)
        if remaining > 0:
            lines.append(
                f"\n... {remaining} more resources. "
                f"Call list_resources with offset={offset + len(paginated)} to see more."
            )

        return "\n".join(lines)

    @logfire.instrument("capability.resource_capability.read_resource")
    async def read_resource(
        self,
        ctx: RunContext[AgentDepsT],
        server: Annotated[
            str,
            Field(
                description=(
                    "Name of the MCP server providing this resource. "
                    "Use the server name shown in the Source column of list_resources. "
                    "Pass 'skill' for skill:// URIs."
                )
            ),
        ],
        uri: Annotated[
            str,
            Field(
                description=(
                    "Resource URI to read, e.g. 'file://path/to/file' or 'skill://skill-name/resource'"
                )
            ),
        ],
    ) -> ToolReturn:
        """Read content from a resource by server name and URI.

        Finds the resource provider matching ``server`` and reads the
        resource at ``uri`` from that provider only. This avoids ambiguity
        when multiple servers serve resources with the same URI.

        For ``skill://`` URIs, pass ``"skill"`` as the server name; the
        tool routes through the skill resource resolver.

        Text content is truncated to 10,000 characters. Binary content is
        returned only for supported MIME types (PDF, GIF, JPEG, PNG, WebP)
        up to 10 MB; larger or unsupported blobs are replaced with a text
        marker describing the omitted content.

        Args:
            ctx: The run context providing agent dependencies.
            server: Name of the MCP server (or ``"skill"`` for skill resources).
            uri: Resource URI to read.
        """
        import base64

        from pydantic_ai.messages import BinaryContent

        from wolfharness.capabilities.resource_resolver import resolve_resource_content

        agent_ctx = self._resolve_agent_context(ctx)
        registry = agent_ctx.extension_registry
        if registry is None:
            return ToolReturn(return_value=f"Resource not found: {uri}")

        scope = self._make_scope(agent_ctx)

        # skill:// URIs route through the skill resolver
        if uri.startswith("skill://"):
            skill_caps = registry.get_skill_resources(scope)
            resource_caps = registry.get_resource_access(scope)
            content = await resolve_resource_content(uri, resource_caps, skill_caps)
            if content is None:
                return ToolReturn(return_value=f"Resource not found: {uri}")
            text_parts = [p for p in content if isinstance(p, str)]
            return_value = "\n".join(text_parts) if text_parts else ""
            return ToolReturn(return_value=return_value, content=content)

        # Find the matching provider by server name
        matching_cap: ResourceAccess | None = None
        for cap in registry.get_resource_access(scope):
            cap_name = cap.client_name if isinstance(cap, McpServerCap) else type(cap).__name__
            if cap_name == server:
                matching_cap = cap
                break

        if matching_cap is None:
            return ToolReturn(
                return_value=f"Server '{server}' not found or does not provide resources.",
            )

        try:
            contents = await matching_cap.read_resource(uri)
        except RuntimeError as exc:
            return ToolReturn(
                return_value=f"Failed to read resource '{uri}' from '{server}': {exc}",
            )

        if not contents:
            return ToolReturn(return_value=f"Resource not found: {uri}")

        return_value_parts: list[str] = []
        content_parts: list[BinaryContent] = []
        for item in contents:
            if isinstance(item, TextResourceContent):
                return_value_parts.append(self._truncate_text(item.text))
            elif isinstance(item, BlobResourceContent):
                mime = item.mime_type or "application/octet-stream"
                blob_bytes = base64.b64decode(item.blob) if item.blob else b""
                size = len(blob_bytes)
                if mime in _BLOB_MIME_ALLOWLIST and size <= _MAX_BLOB_SIZE_BYTES:
                    return_value_parts.append(f"[Binary resource: {uri} ({mime}, {size} bytes)]")
                    content_parts.append(BinaryContent(data=blob_bytes, media_type=mime))
                else:
                    return_value_parts.append(
                        f"[Binary MCP resource omitted: {uri} ({mime}, {size} bytes) — "
                        f"not a supported attachment type or exceeds size limit]"
                    )

        return ToolReturn(
            return_value="\n".join(return_value_parts),
            content=content_parts if content_parts else None,
        )

    @logfire.instrument("capability.resource_capability.resource_exists")
    async def resource_exists(
        self,
        ctx: RunContext[AgentDepsT],
        uri: Annotated[str, Field(description="Resource URI to check")],
    ) -> bool:
        """Check if a resource exists.

        Routes by URI scheme:
        ``skill://`` → skill providers, other URIs → resource providers.

        Args:
            ctx: The run context providing agent dependencies.
            uri: Resource URI to check.

        Returns:
            True if any provider has the resource, False otherwise.
        """
        agent_ctx = self._resolve_agent_context(ctx)
        registry = agent_ctx.extension_registry
        if registry is None:
            return False

        scope = self._make_scope(agent_ctx)

        if uri.startswith("skill://"):
            from wolfharness.skills.uri_resolver import ResolvedSkillURI

            resolved = ResolvedSkillURI.parse(uri)
            skill_name = resolved.skill_name

            # If URI contains a reference path, check if the reference file exists.
            # Exception: "SKILL.md" (case-insensitive) is the skill's main file —
            # use skill_exists() for backward compatibility and virtual skill support.
            if (
                resolved.reference_path is not None
                and resolved.reference_path.upper() != "SKILL.MD"
            ):
                from wolfharness.capabilities.resource_resolver import _resolve_skill_reference

                try:
                    ref_content = await _resolve_skill_reference(
                        registry.get_skill_resources(scope),
                        skill_name,
                        resolved.reference_path,
                    )
                    return ref_content is not None
                except Exception:  # noqa: BLE001
                    return False

            # No reference path — check if the skill itself exists
            for skill_cap in registry.get_skill_resources(scope):
                try:
                    if await skill_cap.skill_exists(skill_name):
                        return True
                except Exception:  # noqa: BLE001
                    continue
            return False

        for resource_cap in registry.get_resource_access(scope):
            try:
                if await resource_cap.resource_exists(uri):
                    return True
            except Exception:  # noqa: BLE001
                continue
        return False

    @logfire.instrument("capability.resource_capability.list_resource_templates")
    async def list_resource_templates(
        self,
        ctx: RunContext[AgentDepsT],
        server: Annotated[
            str | None,
            Field(
                description=(
                    "Optional server name to filter templates by. "
                    "If omitted, lists templates from all connected servers."
                )
            ),
        ] = None,
        limit: Annotated[
            int,
            Field(description="Maximum number of templates to return (default: 50)"),
        ] = _DEFAULT_LIST_LIMIT,
        offset: Annotated[
            int,
            Field(description="Number of templates to skip for pagination"),
        ] = 0,
    ) -> str:
        """List URI templates for dynamic resource discovery.

        Results are paginated. Use ``offset`` to page through large result sets.
        Pass ``server`` to filter templates from a specific provider.

        Args:
            ctx: The run context providing agent dependencies.
            server: Optional server name to filter by.
            limit: Maximum number of templates to return.
            offset: Number of templates to skip.
        """
        agent_ctx = self._resolve_agent_context(ctx)
        registry = agent_ctx.extension_registry
        if registry is None:
            return "No resource templates available."

        scope = self._make_scope(agent_ctx)
        rows: list[str] = []

        for cap in registry.get_resource_template_access(scope):
            source = cap.client_name if isinstance(cap, McpServerCap) else type(cap).__name__
            if server is not None and source != server:
                continue
            try:
                entries = await cap.list_resource_templates()
            except Exception:  # noqa: BLE001
                logfire.warning(
                    "Failed to list resource templates from {source}",
                    source=source,
                )
                continue
            rows.extend(
                f"{source:<25} {entry.uri_template:<40} {entry.name:<20} "
                f"{entry.title:<15} {entry.description:<30} {entry.mime_type:<15}"
                for entry in entries
            )

        if not rows:
            return "No resource templates available."

        total = len(rows)
        paginated = rows[offset : offset + limit]

        header = (
            f"{'Source':<25} {'URI Template':<40} {'Name':<20} "
            f"{'Title':<15} {'Description':<30} {'MIME Type':<15}"
        )
        lines = [header, "-" * len(header)]
        lines.extend(paginated)

        remaining = total - offset - len(paginated)
        if remaining > 0:
            lines.append(
                f"\n... {remaining} more templates. "
                f"Call list_resource_templates with offset={offset + len(paginated)} to see more."
            )

        return "\n".join(lines)

    @logfire.instrument("capability.resource_capability.complete_resource_template")
    async def complete_resource_template(
        self,
        ctx: RunContext[AgentDepsT],
        uri_template: Annotated[str, Field(description="The URI template to complete")],
        argument_name: Annotated[str, Field(description="The parameter name being completed")],
        argument_value: Annotated[str, Field(description="The current value of the parameter")],
    ) -> str:
        """Get completion suggestions for a resource template parameter.

        Args:
            ctx: The run context providing agent dependencies.
            uri_template: The URI template to complete.
            argument_name: The parameter name being completed.
            argument_value: The current value of the parameter.
        """
        agent_ctx = self._resolve_agent_context(ctx)
        registry = agent_ctx.extension_registry
        if registry is None:
            return "No resource template providers available."

        scope = self._make_scope(agent_ctx)
        argument = CompletionArgument(name=argument_name, value=argument_value)

        for cap in registry.get_resource_template_access(scope):
            try:
                templates = await cap.list_resource_templates()
            except Exception:  # noqa: BLE001
                continue
            matching = any(t.uri_template == uri_template for t in templates)
            if not matching:
                continue
            try:
                result: CompletionResult = await cap.complete_resource_template(
                    uri_template,
                    argument,
                )
            except NotImplementedError:
                return f"Completion not supported for template: {uri_template}"
            return self._format_completion_result(result)

        return f"Completion not supported for template: {uri_template}"

    @staticmethod
    def _truncate_text(
        text: str,
        limit: int = _DEFAULT_READ_TEXT_LIMIT,
    ) -> str:
        """Truncate text content if it exceeds the limit.

        Args:
            text: The text to potentially truncate.
            limit: Maximum number of characters to keep.

        Returns:
            The original text if within limit, or a truncated version
            with a suffix indicating the total length.
        """
        if len(text) <= limit:
            return text
        return text[:limit] + f"\n\n... [truncated: {len(text)} chars total, showing first {limit}]"

    @staticmethod
    def _format_completion_result(result: CompletionResult) -> str:
        """Format a ``CompletionResult`` into a human-readable string.

        Args:
            result: The completion result to format.

        Returns:
            A formatted string with completion suggestions.
        """
        lines: list[str] = ["Completion suggestions:"]
        values = result.values[:_MAX_COMPLETION_SUGGESTIONS]
        lines.extend(f"  - {value}" for value in values)
        if len(result.values) > _MAX_COMPLETION_SUGGESTIONS:
            lines.append(
                f"  ... ({len(result.values)} total, showing first {_MAX_COMPLETION_SUGGESTIONS})"
            )
        elif result.has_more:
            lines.append(f"  ... ({result.total} total, more available)")
        elif result.total is not None and result.total > len(result.values):
            lines.append(f"  ... ({result.total} total)")
        return "\n".join(lines)


__all__ = ["ResourceCapability"]
