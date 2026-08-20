"""Domain-specific Resource Protocols for unified extension access.

Defines Protocol interfaces for skill, MCP, and command resource access.
Each protocol is ``@runtime_checkable`` so capabilities can be queried via
``isinstance(cap, SkillResource)`` etc.

The deprecated ``McpResource`` Protocol has been split into three focused
protocols: ``ToolAccess``, ``ResourceAccess``, and ``ResourceTemplateAccess``.
``McpResource`` remains as a ``DeprecatedAlias`` that emits ``DeprecationWarning``
on ``isinstance()`` checks.

ChangeEvent is imported from ``wolfharness.capabilities.change_event`` —
this module does NOT define a duplicate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable
import warnings

from pydantic import BaseModel, ConfigDict, Field as PydanticField

from wolfharness.common_types import JsonObject


if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence
    from pathlib import PurePosixPath

    from upath import UPath

    from wolfharness.capabilities.change_event import ChangeEvent


# ---- Dataclasses ----


@dataclass(frozen=True, slots=True)
class SkillEntry:
    """A skill descriptor returned by ``SkillResource.list_skills()``.

    Attributes:
        name: Skill name (e.g., ``"ponytail"``).
        description: Short human-readable description.
        uri: Canonical URI (e.g., ``"skill://ponytail/SKILL.md"``).
        source: Where the skill comes from — ``"local"`` or ``"remote"``.
        skill_path: Real filesystem path for local skills (``UPath``) or
            ``None`` for virtual/MCP skills.
    """

    name: str
    description: str = ""
    uri: str = ""
    source: str = "local"
    skill_path: UPath | PurePosixPath | None = None


@dataclass(frozen=True, slots=True)
class ToolEntry:
    """A tool descriptor returned by ``McpResource.list_tools()``.

    Attributes:
        name: Tool name as known to the MCP server.
        description: Tool description from the server.
        schema: JSON schema dict for the tool's input parameters.
    """

    name: str
    description: str = ""
    schema: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ToolResult:
    """Result of an MCP tool call.

    Attributes:
        content: The tool output content as text.
        is_error: Whether the tool returned an error.
    """

    content: str
    is_error: bool = False


@dataclass(frozen=True, slots=True)
class ResourceEntry:
    """A resource descriptor returned by ``McpResource.list_resources()``.

    Attributes:
        uri: Resource URI (e.g., ``"file:///path/to/resource"``).
        name: Human-readable resource name.
        title: Optional display title.
        description: Optional description.
        mime_type: MIME type of the resource content.
        size: Optional resource size in bytes.
        annotations: Optional MCP annotations.
        meta: Optional MCP protocol metadata.
    """

    uri: str
    name: str = ""
    title: str = ""
    description: str = ""
    mime_type: str = ""
    size: int | None = None
    annotations: JsonObject | None = None
    meta: JsonObject | None = None


@dataclass(frozen=True, slots=True)
class CommandEntry:
    """A command descriptor returned by ``CommandResource.list_commands()``.

    Attributes:
        name: Command name (e.g., ``"ponytail"``).
        description: Short description of what the command does.
        skill_uri: URI of the skill backing this command, if any.
        source: Where the command comes from — ``"local"`` or ``"remote"``.
    """

    name: str
    description: str = ""
    skill_uri: str = ""
    source: str = "local"


# ---- Resource Content Types (MCP-aligned) ----


@dataclass(frozen=True, slots=True)
class ResourceContent:
    """Base type for resource content returned by ``read_resource()``.

    Mirrors MCP's ``ResourceContents`` structure.

    Attributes:
        uri: The URI of the resource that was read.
        mime_type: MIME type of the resource content, or ``None``.
        meta: Optional metadata dict from the MCP server.
    """

    uri: str
    mime_type: str | None = None
    meta: JsonObject | None = None


@dataclass(frozen=True, slots=True)
class TextResourceContent(ResourceContent):
    """Text resource content returned by ``read_resource()``.

    Mirrors MCP's ``TextResourceContents``.

    Attributes:
        text: The text content of the resource.
    """

    text: str = ""


@dataclass(frozen=True, slots=True)
class BlobResourceContent(ResourceContent):
    """Binary resource content returned by ``read_resource()``.

    Mirrors MCP's ``BlobResourceContents``. The ``blob`` field contains
    base64-encoded bytes — decoding happens at the agent tool layer.

    Attributes:
        blob: Base64-encoded string of the binary content.
    """

    blob: str = ""


# ---- Resource Template & Completion Types ----


@dataclass(frozen=True, slots=True)
class ResourceTemplateEntry:
    """A resource template descriptor returned by ``list_resource_templates()``.

    Mirrors MCP's ``ResourceTemplate`` structure.

    Attributes:
        uri_template: URI template pattern (e.g., ``"file:///{path}"``).
        name: Human-readable template name.
        title: Display title (may be empty).
        description: Optional description.
        mime_type: MIME type of expanded resources.
        annotations: Optional MCP annotations dict.
        meta: Optional MCP protocol metadata.
    """

    uri_template: str
    name: str = ""
    title: str = ""
    description: str = ""
    mime_type: str = ""
    annotations: JsonObject | None = None
    meta: JsonObject | None = None


@dataclass(frozen=True, slots=True)
class ResourcePage:
    """One upstream MCP ``resources/list`` page."""

    entries: list[ResourceEntry]
    next_cursor: str | None = None


@dataclass(frozen=True, slots=True)
class ResourceTemplatePage:
    """One upstream MCP ``resources/templates/list`` page."""

    entries: list[ResourceTemplateEntry]
    next_cursor: str | None = None


type McpResourceErrorCode = Literal[
    "unknown_server",
    "resources_not_supported",
    "invalid_cursor",
    "resource_not_found",
    "permission_denied",
    "timeout",
    "provider_unavailable",
    "unsupported_mime_type",
    "content_too_large",
]


class _McpResourceResultModel(BaseModel):
    """Frozen base model for agent-facing structured resource results."""

    model_config = ConfigDict(frozen=True)


class McpResourceError(_McpResourceResultModel):
    """Actionable error returned by an agent-facing MCP resource tool."""

    code: McpResourceErrorCode
    message: str
    retryable: bool
    suggestion: str
    server: str | None = None
    uri: str | None = None


class McpResourceItem(_McpResourceResultModel):
    """Server-attributed resource descriptor returned to an agent."""

    server: str
    uri: str
    name: str
    title: str = ""
    description: str = ""
    mime_type: str = ""
    size: int | None = None
    annotations: JsonObject | None = None
    meta: JsonObject | None = None


class McpResourceTemplateItem(_McpResourceResultModel):
    """Server-attributed resource template returned to an agent."""

    server: str
    uri_template: str
    name: str
    title: str = ""
    description: str = ""
    mime_type: str = ""
    annotations: JsonObject | None = None
    meta: JsonObject | None = None


class McpResourceListResult(_McpResourceResultModel):
    """Structured result for ``list_mcp_resources``."""

    summary: str
    resources: list[McpResourceItem]
    next_cursor: str | None = None
    errors: list[McpResourceError] = PydanticField(default_factory=list)


class McpResourceTemplateListResult(_McpResourceResultModel):
    """Structured result for ``list_mcp_resource_templates``."""

    summary: str
    templates: list[McpResourceTemplateItem]
    next_cursor: str | None = None
    errors: list[McpResourceError] = PydanticField(default_factory=list)


class McpTextContentResult(_McpResourceResultModel):
    """Model-visible text content from an MCP resource read."""

    type: Literal["text"]
    uri: str
    mime_type: str | None
    text: str
    truncated: bool
    original_char_count: int
    meta: JsonObject | None = None


class McpBlobContentResult(_McpResourceResultModel):
    """Metadata for binary MCP content; base64 is never exposed to the model."""

    type: Literal["blob"]
    uri: str
    mime_type: str
    size: int
    attached: bool
    omission_reason: str | None = None
    meta: JsonObject | None = None


class McpResourceReadResult(_McpResourceResultModel):
    """Structured result for ``read_mcp_resource``."""

    summary: str
    server: str
    requested_uri: str
    contents: list[McpTextContentResult | McpBlobContentResult]
    error: McpResourceError | None = None


def escape_catalog_client_name(client_name: str) -> str:
    """Escape a client name for use in a catalog key.

    Mirrors the upstream OpenCode ``mcp/catalog.ts`` escaping: percent signs
    are encoded first (``%`` → ``%25``), then colons (``:`` → ``%3A``),
    so the resulting string is safe to join with ``:`` as a key separator.

    Args:
        client_name: The raw client (server display) name.

    Returns:
        The escaped client name, with ``%`` and ``:`` percent-encoded.
    """
    return client_name.replace("%", "%25").replace(":", "%3A")


def make_catalog_key(client_name: str, uri: str) -> str:
    """Construct a catalog key in the canonical ``{escapedClient}:{uri}`` form.

    The client name is escaped via :func:`escape_catalog_client_name` so that
    any ``%`` or ``:`` inside it cannot collide with the key separator.  The
    URI portion is used verbatim (URIs are not expected to contain raw colons
    in positions that would break parsing — the scheme ``:`` is unambiguous).

    Args:
        client_name: The raw client (server display) name.
        uri: The resource URI (or template URI pattern).

    Returns:
        A string of the form ``"{escaped_client}:{uri}"``.
    """
    return f"{escape_catalog_client_name(client_name)}:{uri}"


@dataclass(frozen=True, slots=True)
class CompletionArgument:
    """Argument for a resource template completion request.

    Mirrors MCP's ``CompletionArgument``.

    Attributes:
        name: The parameter name being completed.
        value: The current value of the parameter.
    """

    name: str
    value: str


@dataclass(frozen=True, slots=True)
class CompletionResult:
    """Result of a resource template completion request.

    Mirrors MCP's ``Completion`` type.

    Attributes:
        values: List of completion suggestions.
        total: Total number of completions available (may exceed ``len(values)``).
        has_more: Whether more completions exist beyond those returned.
    """

    values: list[str]
    total: int | None = None
    has_more: bool | None = None


# ---- Protocols ----


@runtime_checkable
class SkillResource(Protocol):
    """Protocol for accessing skill resources."""

    async def list_skills(self) -> Sequence[SkillEntry]:
        """Return all available skills.

        Returns:
            Sequence of ``SkillEntry`` descriptors.
        """
        ...

    async def read_skill(self, name: str) -> str | None:
        """Read skill content by name.

        Args:
            name: Skill name to read.

        Returns:
            Skill content as string, or ``None`` if not found.
        """
        ...

    async def skill_exists(self, name: str) -> bool:
        """Check if a skill exists without reading it.

        Args:
            name: Skill name to check.

        Returns:
            ``True`` if the skill exists, ``False`` otherwise.
        """
        ...


@runtime_checkable
class ToolAccess(Protocol):
    """Protocol for accessing MCP tools.

    This is the tool-access portion of the deprecated ``McpResource``.
    Capabilities implementing this protocol provide tool listing and invocation.
    """

    async def list_tools(self) -> Sequence[ToolEntry]:
        """List available MCP tools.

        Returns:
            Sequence of ``ToolEntry`` descriptors.
        """
        ...

    async def call_tool(self, name: str, args: dict[str, Any]) -> ToolResult:
        """Call an MCP tool.

        Args:
            name: Tool name to call.
            args: Arguments to pass to the tool.

        Returns:
            ``ToolResult`` with the tool output.
        """
        ...


@runtime_checkable
class ResourceAccess(Protocol):
    """Protocol for accessing MCP resources.

    This is the resource-access portion of the deprecated ``McpResource``.
    Capabilities implementing this protocol provide resource listing, reading,
    and existence checking.
    """

    async def list_resources(self) -> Sequence[ResourceEntry]:
        """List available MCP resources.

        Returns:
            Sequence of ``ResourceEntry`` descriptors.
        """
        ...

    async def read_resource(
        self, uri: str
    ) -> list[TextResourceContent | BlobResourceContent] | None:
        """Read an MCP resource by URI.

        Args:
            uri: Resource URI to read.

        Returns:
            List of ``TextResourceContent`` and/or ``BlobResourceContent``
            instances, or ``None`` if the resource is not found.
        """
        ...

    async def resource_exists(self, uri: str) -> bool:
        """Check if an MCP resource exists.

        Args:
            uri: Resource URI to check.

        Returns:
            ``True`` if the resource exists, ``False`` otherwise.
        """
        ...


@runtime_checkable
class NamedResourceAccess(ResourceAccess, Protocol):
    """Resource provider with a stable server/client routing identity."""

    @property
    def client_name(self) -> str:
        """Return the configured provider name used with resource URIs."""
        ...


@runtime_checkable
class ResourceTemplateAccess(Protocol):
    """Protocol for accessing MCP resource templates.

    Capabilities implementing this protocol provide resource template listing
    and parameter completion. Capabilities that do not support completion
    SHALL raise ``NotImplementedError`` from ``complete_resource_template()``.
    """

    async def list_resource_templates(self) -> Sequence[ResourceTemplateEntry]:
        """List available resource templates.

        Returns:
            Sequence of ``ResourceTemplateEntry`` descriptors.
        """
        ...

    async def complete_resource_template(
        self,
        uri_template: str,
        argument: CompletionArgument,
        context: dict[str, str] | None = None,
    ) -> CompletionResult:
        """Complete a resource template parameter.

        Args:
            uri_template: The URI template to complete.
            argument: The argument being completed.
            context: Optional context arguments.

        Returns:
            ``CompletionResult`` with suggestion values.

        Raises:
            NotImplementedError: If completion is not supported.
        """
        ...


# ---- Deprecated McpResource alias ----


class _McpResourceMeta(type):
    """Metaclass for the deprecated ``McpResource`` alias.

    Implements ``__instancecheck__`` to check against both ``ToolAccess``
    and ``ResourceAccess`` protocols, emitting a ``DeprecationWarning`` on
    each check. This is necessary because Python's ``@runtime_checkable``
    Protocol does not support custom ``__instancecheck__`` — a plain
    ``Union[ToolAccess, ResourceAccess]`` type alias cannot emit warnings
    or be used with ``isinstance()``.
    """

    def __instancecheck__(cls, obj: object) -> bool:
        """Check if obj implements both ToolAccess and ResourceAccess.

        Emits ``DeprecationWarning`` on each call.
        """
        warnings.warn(
            "McpResource is deprecated. Use ToolAccess and ResourceAccess "
            "instead for isinstance() checks.",
            DeprecationWarning,
            stacklevel=2,
        )
        return isinstance(obj, (ToolAccess, ResourceAccess))


class McpResource(metaclass=_McpResourceMeta):
    """Deprecated alias for ``ToolAccess`` + ``ResourceAccess``.

    This class has been replaced by three focused protocols:
        - ``ToolAccess``: tool listing and invocation
        - ``ResourceAccess``: resource listing, reading, existence checking
        - ``ResourceTemplateAccess``: resource template listing and completion

    ``isinstance(obj, McpResource)`` still works but emits a
    ``DeprecationWarning``. Migrate to explicit ``ToolAccess`` /
    ``ResourceAccess`` checks.

    Note:
        ``wolfharness_server.opencode_server.models.mcp.McpResource`` (Pydantic
        model) is unrelated to this deprecated Protocol and is NOT affected.
    """


@runtime_checkable
class CommandResource(Protocol):
    """Protocol for accessing commands (slash commands)."""

    async def list_commands(self) -> Sequence[CommandEntry]:
        """List available commands.

        Returns:
            Sequence of ``CommandEntry`` descriptors.
        """
        ...

    async def get_command(self, name: str) -> CommandEntry | None:
        """Get a specific command by name.

        Args:
            name: Command name to retrieve.

        Returns:
            ``CommandEntry`` if found, ``None`` otherwise.
        """
        ...


@runtime_checkable
class ChangeObservable(Protocol):
    """Protocol for capabilities that emit change notifications."""

    def on_change(self) -> AsyncIterator[ChangeEvent] | None:
        """Return an async iterator of change events, or ``None``.

        Returns:
            An ``AsyncIterator[ChangeEvent]`` if the capability supports
            change notifications, ``None`` otherwise.
        """
        ...
