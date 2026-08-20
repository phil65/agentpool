"""Tests for ResourceCapability and its three model-visible MCP resource tools."""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

from pydantic_ai import BinaryContent, ToolReturn
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.toolsets import FunctionToolset
import pytest

from wolfharness.capabilities.agent_context import AgentContextDeps
from wolfharness.capabilities.extension_registry import ExtensionRegistry, Scope, ScopeLevel
from wolfharness.capabilities.mcp_server_cap import McpServerCap
from wolfharness.capabilities.resource_capability import ResourceCapability
from wolfharness.capabilities.resource_protocols import (
    BlobResourceContent,
    CompletionArgument,
    CompletionResult,
    McpBlobContentResult,
    McpTextContentResult,
    ResourceEntry,
    ResourcePage,
    ResourceTemplateEntry,
    ResourceTemplatePage,
    SkillEntry,
    TextResourceContent,
)
from wolfharness.host.context import RunScope


if TYPE_CHECKING:
    from collections.abc import Sequence


pytestmark = pytest.mark.unit


# =============================================================================
# Fake providers
# =============================================================================


class FakeResourceAccess:
    """Minimal ResourceAccess implementation for testing."""

    def __init__(
        self,
        *,
        resources: list[ResourceEntry] | None = None,
        read_contents: list[TextResourceContent | BlobResourceContent] | None = None,
        exists_uris: set[str] | None = None,
        read_exception: Exception | None = None,
    ) -> None:
        self._resources = resources or []
        self._read_contents = read_contents
        self._exists_uris = exists_uris or set()
        self._read_exception = read_exception

    async def list_resources(self) -> Sequence[ResourceEntry]:
        return list(self._resources)

    async def read_resource(
        self, uri: str
    ) -> list[TextResourceContent | BlobResourceContent] | None:
        if self._read_exception is not None:
            raise self._read_exception
        if self._read_contents is None:
            return None
        return list(self._read_contents)

    async def resource_exists(self, uri: str) -> bool:
        return uri in self._exists_uris


class FakeSkillResource:
    """Minimal SkillResource implementation for testing."""

    def __init__(
        self,
        *,
        skills: list[SkillEntry] | None = None,
        read_content: str | None = None,
        exists_names: set[str] | None = None,
    ) -> None:
        self._skills = skills or []
        self._read_content = read_content
        self._exists_names = exists_names or set()

    async def list_skills(self) -> Sequence[SkillEntry]:
        return list(self._skills)

    async def read_skill(self, name: str) -> str | None:
        if name in self._exists_names:
            return self._read_content
        return None

    async def skill_exists(self, name: str) -> bool:
        return name in self._exists_names


class FakeResourceTemplateAccess:
    """Minimal ResourceTemplateAccess implementation for testing."""

    def __init__(
        self,
        *,
        templates: list[ResourceTemplateEntry] | None = None,
        completion_result: CompletionResult | None = None,
        raise_not_implemented: bool = False,
    ) -> None:
        self._templates = templates or []
        self._completion_result = completion_result
        self._raise_not_implemented = raise_not_implemented

    async def list_resource_templates(self) -> Sequence[ResourceTemplateEntry]:
        return list(self._templates)

    async def complete_resource_template(
        self,
        uri_template: str,
        argument: CompletionArgument,
        context: dict[str, str] | None = None,
    ) -> CompletionResult:
        if self._raise_not_implemented:
            raise NotImplementedError
        if self._completion_result is not None:
            return self._completion_result
        return CompletionResult(values=[])


class FakeMcpServerCap(McpServerCap[Any]):
    """In-memory named MCP provider with controllable pages and reads."""

    def __init__(
        self,
        name: str,
        *,
        resource_pages: dict[str | None, ResourcePage] | None = None,
        template_pages: dict[str | None, ResourceTemplatePage] | None = None,
        read_results: dict[str, list[TextResourceContent | BlobResourceContent] | None]
        | None = None,
        supports_resources: bool = True,
        page_error: RuntimeError | None = None,
        read_error: OSError | RuntimeError | None = None,
    ) -> None:
        config = MagicMock()
        config.client_id = name
        config.display_name = name
        super().__init__(config=config)
        self._resource_pages = resource_pages or {None: ResourcePage(entries=[])}
        self._template_pages = template_pages or {None: ResourceTemplatePage(entries=[])}
        self._read_results = read_results or {}
        self._supports_resources = supports_resources
        self._page_error = page_error
        self._read_error = read_error

    async def supports_resources(self) -> bool:
        return self._supports_resources

    async def list_resources_page(self, cursor: str | None = None) -> ResourcePage:
        if self._page_error is not None:
            raise self._page_error
        return self._resource_pages[cursor]

    async def list_resource_templates_page(self, cursor: str | None = None) -> ResourceTemplatePage:
        if self._page_error is not None:
            raise self._page_error
        return self._template_pages[cursor]

    async def read_resource(
        self, uri: str
    ) -> list[TextResourceContent | BlobResourceContent] | None:
        if self._read_error is not None:
            raise self._read_error
        return self._read_results.get(uri)


# =============================================================================
# Helpers
# =============================================================================


def _make_agent_context(
    registry: ExtensionRegistry | None = None,
) -> AgentContextDeps:
    """Build an AgentContextDeps with test doubles."""
    agent_registry = MagicMock()
    delegation = MagicMock()
    session = MagicMock()
    session.session_id = "test-session-001"
    host = MagicMock()
    return AgentContextDeps(
        agent_registry=agent_registry,
        delegation=delegation,
        session=session,
        scope=RunScope(),
        host=host,
        extension_registry=registry,
    )


def _make_ctx(agent_ctx: AgentContextDeps) -> Any:
    """Create a RunContext-like object with AgentContextDeps as deps."""
    ctx = MagicMock()
    ctx.deps = agent_ctx
    return ctx


def _make_registry_with_caps(
    *caps: Any,
) -> ExtensionRegistry:
    """Build an ExtensionRegistry and register caps at AGENT scope."""
    registry = ExtensionRegistry()
    scope = Scope(level=ScopeLevel.AGENT, session_id="test-session-001")
    for cap in caps:
        registry.register(cap, scope)
    return registry


# =============================================================================
# Tests
# =============================================================================


def test_is_abstract_capability() -> None:
    """ResourceCapability is an instance of AbstractCapability."""
    cap = ResourceCapability()
    assert isinstance(cap, AbstractCapability)


def test_get_toolset_returns_function_toolset() -> None:
    """get_toolset() exposes exactly the three stable MCP resource tools."""
    cap = ResourceCapability()
    toolset = cap.get_toolset()
    assert toolset is not None
    assert isinstance(toolset, FunctionToolset)
    assert set(toolset.tools) == {
        "list_mcp_resources",
        "list_mcp_resource_templates",
        "read_mcp_resource",
    }


def test_name_property() -> None:
    """Name property returns 'resource_capability'."""
    cap = ResourceCapability()
    assert cap.name == "resource_capability"


def test_get_instructions_returns_description() -> None:
    """Instructions describe the three tools and opaque-URI navigation."""
    cap = ResourceCapability()
    instructions = cap.get_instructions()
    assert instructions is not None
    assert "list_mcp_resources" in instructions
    assert "read_mcp_resource" in instructions
    assert "list_mcp_resource_templates" in instructions
    assert "never invent template parameter values" in instructions
    assert "skill://" not in instructions


async def test_stateless_lifecycle() -> None:
    """__aenter__ returns self, __aexit__ is a no-op."""
    cap = ResourceCapability[Any]()
    result = await cap.__aenter__()
    assert result is cap
    exit_result = await cap.__aexit__(None, None, None)
    assert exit_result is None


async def test_list_resources_with_providers() -> None:
    """list_resources aggregates from ResourceAccess only; skills NOT enumerated (D3)."""
    ra = FakeResourceAccess(
        resources=[
            ResourceEntry(
                uri="mcp://server/file.txt",
                name="file.txt",
                description="A text file",
                mime_type="text/plain",
            ),
        ],
    )
    sr = FakeSkillResource(
        skills=[
            SkillEntry(
                name="ponytail",
                description="Lazy senior dev mode",
                uri="skill://ponytail/SKILL.md",
                source="local",
            ),
        ],
    )
    registry = _make_registry_with_caps(ra, sr)
    agent_ctx = _make_agent_context(registry)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()
    result = await cap.list_resources(ctx)

    assert "FakeResourceAccess" in result
    assert "mcp://server/file.txt" in result
    assert "file.txt" in result
    # Skills are NOT enumerated in list_resources (D3)
    assert "FakeSkillResource" not in result
    assert "ponytail" not in result
    assert "skill://" not in result


async def test_list_resources_no_registry() -> None:
    """list_resources returns 'No resources available.' when extension_registry is None."""
    agent_ctx = _make_agent_context(registry=None)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()
    result = await cap.list_resources(ctx)

    assert result == "No resources available."


async def test_read_resource_skill_uri() -> None:
    """read_resource routes skill:// URIs to SkillResource providers."""
    sr = FakeSkillResource(
        skills=[SkillEntry(name="ponytail", uri="skill://ponytail/SKILL.md")],
        read_content="# Ponytail\nLazy dev mode.",
        exists_names={"ponytail"},
    )
    registry = _make_registry_with_caps(sr)
    agent_ctx = _make_agent_context(registry)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()
    result = await cap.read_resource(ctx, "skill", "skill://ponytail/SKILL.md")

    assert isinstance(result, ToolReturn)
    assert result.content is not None
    assert "# Ponytail" in result.return_value


async def test_read_resource_skill_reference_uri(tmp_path: Any) -> None:
    """read_resource with skill:// URI containing reference path reads the reference file."""
    from upathtools import UPath

    # Create a fake skill directory with a reference file
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# My Skill\nInstructions here.")
    ref_dir = skill_dir / "references"
    ref_dir.mkdir()
    (ref_dir / "guide.md").write_text("# Guide\nReference content here.")

    sr = FakeSkillResource(
        skills=[
            SkillEntry(
                name="my-skill",
                description="Test skill",
                uri="skill://my-skill",
                source="local",
                skill_path=UPath(str(skill_dir)),
            )
        ],
        read_content="# My Skill\nInstructions here.",
        exists_names={"my-skill"},
    )
    registry = _make_registry_with_caps(sr)
    agent_ctx = _make_agent_context(registry)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()
    result = await cap.read_resource(ctx, "skill", "skill://my-skill/references/guide.md")

    assert isinstance(result, ToolReturn)
    assert result.content is not None
    assert "Reference content here." in result.return_value
    assert "Instructions here." not in result.return_value


async def test_read_resource_skill_reference_not_found(tmp_path: Any) -> None:
    """read_resource with non-existent reference path returns 'Resource not found'."""
    from upathtools import UPath

    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# My Skill")

    sr = FakeSkillResource(
        skills=[
            SkillEntry(
                name="my-skill",
                description="Test skill",
                uri="skill://my-skill",
                source="local",
                skill_path=UPath(str(skill_dir)),
            )
        ],
        read_content="# My Skill",
        exists_names={"my-skill"},
    )
    registry = _make_registry_with_caps(sr)
    agent_ctx = _make_agent_context(registry)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()
    result = await cap.read_resource(ctx, "skill", "skill://my-skill/references/missing.md")

    assert isinstance(result, ToolReturn)
    assert "not found" in result.return_value.lower()


async def test_resource_exists_skill_reference_uri(tmp_path: Any) -> None:
    """resource_exists with skill:// URI containing reference path checks file existence."""
    from upathtools import UPath

    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# My Skill")
    ref_dir = skill_dir / "references"
    ref_dir.mkdir()
    (ref_dir / "guide.md").write_text("# Guide")

    sr = FakeSkillResource(
        skills=[
            SkillEntry(
                name="my-skill",
                description="Test skill",
                uri="skill://my-skill",
                source="local",
                skill_path=UPath(str(skill_dir)),
            )
        ],
        read_content="# My Skill",
        exists_names={"my-skill"},
    )
    registry = _make_registry_with_caps(sr)
    agent_ctx = _make_agent_context(registry)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()

    # Existing reference file → True
    result = await cap.resource_exists(ctx, "skill://my-skill/references/guide.md")
    assert result is True

    # Non-existing reference file → False
    result = await cap.resource_exists(ctx, "skill://my-skill/references/missing.md")
    assert result is False


async def test_read_resource_mcp_uri() -> None:
    """read_resource routes non-skill URIs to ResourceAccess providers with TextResourceContent."""
    ra = FakeResourceAccess(
        read_contents=[
            TextResourceContent(
                uri="mcp://server/file.txt",
                mime_type="text/plain",
                text="Hello, world!",
            ),
        ],
    )
    registry = _make_registry_with_caps(ra)
    agent_ctx = _make_agent_context(registry)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()
    result = await cap.read_resource(ctx, "FakeResourceAccess", "mcp://server/file.txt")

    assert isinstance(result, ToolReturn)
    assert "Hello, world!" in result.return_value


async def test_read_resource_blob() -> None:
    """read_resource converts BlobResourceContent to BinaryContent."""
    raw_data = b"\x89PNG\r\n\x1a\n"
    encoded = base64.b64encode(raw_data).decode("ascii")
    ra = FakeResourceAccess(
        read_contents=[
            BlobResourceContent(
                uri="mcp://server/image.png",
                mime_type="image/png",
                blob=encoded,
            ),
        ],
    )
    registry = _make_registry_with_caps(ra)
    agent_ctx = _make_agent_context(registry)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()
    result = await cap.read_resource(ctx, "FakeResourceAccess", "mcp://server/image.png")

    assert isinstance(result, ToolReturn)
    assert result.content is not None
    assert len(result.content) == 1
    binary = result.content[0]
    assert isinstance(binary, BinaryContent)
    assert binary.data == raw_data
    assert binary.media_type == "image/png"
    assert "[Binary resource:" in result.return_value


async def test_read_resource_not_found() -> None:
    """read_resource returns 'Resource not found' when no provider has the resource."""
    ra = FakeResourceAccess(read_contents=None)
    registry = _make_registry_with_caps(ra)
    agent_ctx = _make_agent_context(registry)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()
    result = await cap.read_resource(ctx, "FakeResourceAccess", "mcp://server/missing.txt")

    assert isinstance(result, ToolReturn)
    assert result.return_value == "Resource not found: mcp://server/missing.txt"


async def test_read_resource_no_registry() -> None:
    """read_resource returns 'Resource not found' when extension_registry is None."""
    agent_ctx = _make_agent_context(registry=None)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()
    result = await cap.read_resource(ctx, "FakeResourceAccess", "mcp://server/file.txt")

    assert isinstance(result, ToolReturn)
    assert result.return_value == "Resource not found: mcp://server/file.txt"


async def test_resource_exists_true() -> None:
    """resource_exists returns True when a provider has the resource."""
    ra = FakeResourceAccess(exists_uris={"mcp://server/file.txt"})
    registry = _make_registry_with_caps(ra)
    agent_ctx = _make_agent_context(registry)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()
    result = await cap.resource_exists(ctx, "mcp://server/file.txt")

    assert result is True


async def test_resource_exists_false() -> None:
    """resource_exists returns False when no provider has the resource."""
    ra = FakeResourceAccess(exists_uris=set())
    registry = _make_registry_with_caps(ra)
    agent_ctx = _make_agent_context(registry)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()
    result = await cap.resource_exists(ctx, "mcp://server/missing.txt")

    assert result is False


async def test_resource_exists_skill_uri() -> None:
    """resource_exists routes skill:// URIs to SkillResource providers."""
    sr = FakeSkillResource(exists_names={"ponytail"})
    registry = _make_registry_with_caps(sr)
    agent_ctx = _make_agent_context(registry)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()
    result = await cap.resource_exists(ctx, "skill://ponytail/SKILL.md")

    assert result is True


async def test_resource_exists_no_registry() -> None:
    """resource_exists returns False when extension_registry is None."""
    agent_ctx = _make_agent_context(registry=None)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()
    result = await cap.resource_exists(ctx, "mcp://server/file.txt")

    assert result is False


async def test_list_resource_templates() -> None:
    """list_resource_templates formats template table output."""
    rta = FakeResourceTemplateAccess(
        templates=[
            ResourceTemplateEntry(
                uri_template="file:///{path}",
                name="file_template",
                title="File Template",
                description="Access files by path",
                mime_type="text/plain",
            ),
        ],
    )
    registry = _make_registry_with_caps(rta)
    agent_ctx = _make_agent_context(registry)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()
    result = await cap.list_resource_templates(ctx)

    assert "FakeResourceTemplateAccess" in result
    assert "file:///{path}" in result
    assert "file_template" in result
    assert "File Template" in result


async def test_list_resource_templates_no_registry() -> None:
    """list_resource_templates returns graceful empty when extension_registry is None."""
    agent_ctx = _make_agent_context(registry=None)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()
    result = await cap.list_resource_templates(ctx)

    assert result == "No resource templates available."


async def test_list_resource_templates_empty() -> None:
    """list_resource_templates returns graceful empty when no templates exist."""
    rta = FakeResourceTemplateAccess(templates=[])
    registry = _make_registry_with_caps(rta)
    agent_ctx = _make_agent_context(registry)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()
    result = await cap.list_resource_templates(ctx)

    assert result == "No resource templates available."


async def test_complete_resource_template() -> None:
    """complete_resource_template returns formatted completion suggestions."""
    rta = FakeResourceTemplateAccess(
        templates=[
            ResourceTemplateEntry(uri_template="file:///{path}"),
        ],
        completion_result=CompletionResult(
            values=["file1.txt", "file2.txt"],
            total=2,
            has_more=False,
        ),
    )
    registry = _make_registry_with_caps(rta)
    agent_ctx = _make_agent_context(registry)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()
    result = await cap.complete_resource_template(ctx, "file:///{path}", "path", "file")

    assert "file1.txt" in result
    assert "file2.txt" in result


async def test_complete_resource_template_not_supported() -> None:
    """complete_resource_template handles NotImplementedError gracefully."""
    rta = FakeResourceTemplateAccess(
        templates=[
            ResourceTemplateEntry(uri_template="file:///{path}"),
        ],
        raise_not_implemented=True,
    )
    registry = _make_registry_with_caps(rta)
    agent_ctx = _make_agent_context(registry)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()
    result = await cap.complete_resource_template(ctx, "file:///{path}", "path", "file")

    assert "Completion not supported for template: file:///{path}" in result


async def test_complete_resource_template_no_matching_template() -> None:
    """complete_resource_template returns 'not supported' when no matching template found."""
    rta = FakeResourceTemplateAccess(
        templates=[
            ResourceTemplateEntry(uri_template="file:///{path}"),
        ],
    )
    registry = _make_registry_with_caps(rta)
    agent_ctx = _make_agent_context(registry)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()
    result = await cap.complete_resource_template(ctx, "unknown://template", "param", "val")

    assert "Completion not supported for template: unknown://template" in result


async def test_complete_resource_template_no_registry() -> None:
    """complete_resource_template returns graceful empty when extension_registry is None."""
    agent_ctx = _make_agent_context(registry=None)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()
    result = await cap.complete_resource_template(ctx, "file:///{path}", "path", "file")

    assert "No resource template providers available." in result


def test_toolset_id_customizable() -> None:
    """Toolset ID can be customized via constructor."""
    cap = ResourceCapability(toolset_id="custom_resources")
    toolset = cap.get_toolset()
    assert isinstance(toolset, FunctionToolset)
    assert toolset.id == "custom_resources"


def test_default_toolset_id() -> None:
    """Default toolset ID is 'resource_access'."""
    cap = ResourceCapability()
    toolset = cap.get_toolset()
    assert isinstance(toolset, FunctionToolset)
    assert toolset.id == "resource_access"


async def test_read_resource_skill_not_found() -> None:
    """read_resource returns 'Resource not found' for missing skill."""
    sr = FakeSkillResource(exists_names=set())
    registry = _make_registry_with_caps(sr)
    agent_ctx = _make_agent_context(registry)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()
    result = await cap.read_resource(ctx, "skill", "skill://nonexistent/SKILL.md")

    assert isinstance(result, ToolReturn)
    assert "Resource not found" in result.return_value


async def test_read_resource_provider_exception() -> None:
    """read_resource returns error message when provider raises RuntimeError."""
    ra = FakeResourceAccess(read_exception=RuntimeError("connection failed"))
    registry = _make_registry_with_caps(ra)
    agent_ctx = _make_agent_context(registry)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()
    result = await cap.read_resource(ctx, "FakeResourceAccess", "mcp://server/file.txt")

    assert isinstance(result, ToolReturn)
    assert "Failed to read resource" in result.return_value
    assert "connection failed" in result.return_value


async def test_read_resource_blob_default_mime_type() -> None:
    """read_resource uses 'application/octet-stream' when mime_type is None."""
    raw_data = b"\x00\x01\x02"
    encoded = base64.b64encode(raw_data).decode("ascii")
    ra = FakeResourceAccess(
        read_contents=[
            BlobResourceContent(
                uri="mcp://server/data.bin",
                mime_type=None,
                blob=encoded,
            ),
        ],
    )
    registry = _make_registry_with_caps(ra)
    agent_ctx = _make_agent_context(registry)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()
    result = await cap.read_resource(ctx, "FakeResourceAccess", "mcp://server/data.bin")

    assert isinstance(result, ToolReturn)
    assert result.content is None
    assert "[Binary MCP resource omitted:" in result.return_value
    assert "application/octet-stream" in result.return_value


def test_extract_skill_name() -> None:
    """_extract_skill_name takes the first path segment from a skill:// URI."""
    assert ResourceCapability._extract_skill_name("skill://ponytail/SKILL.md") == "ponytail"
    assert ResourceCapability._extract_skill_name("skill://my-skill") == "my-skill"
    assert ResourceCapability._extract_skill_name("skill://a/b/c") == "a"
    assert ResourceCapability._extract_skill_name("skill://") == ""


# =============================================================================
# Pagination tests
# =============================================================================


async def test_list_resources_pagination_default_limit() -> None:
    """list_resources truncates at default limit=50 and shows 'more' message."""
    entries = [ResourceEntry(uri=f"mcp://server/res{i}", name=f"res{i}") for i in range(60)]
    ra = FakeResourceAccess(resources=entries)
    registry = _make_registry_with_caps(ra)
    agent_ctx = _make_agent_context(registry)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()
    result = await cap.list_resources(ctx)

    # 2 header lines + 50 data rows + 1 "more" message
    lines = result.split("\n")
    data_lines = [line for line in lines if "mcp://server/res" in line]
    assert len(data_lines) == 50
    assert "10 more resources" in result
    assert "offset=50" in result


async def test_list_resources_pagination_custom_limit() -> None:
    """list_resources respects custom limit parameter."""
    entries = [ResourceEntry(uri=f"mcp://server/res{i}", name=f"res{i}") for i in range(30)]
    ra = FakeResourceAccess(resources=entries)
    registry = _make_registry_with_caps(ra)
    agent_ctx = _make_agent_context(registry)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()
    result = await cap.list_resources(ctx, limit=10)

    data_lines = [line for line in result.split("\n") if "mcp://server/res" in line]
    assert len(data_lines) == 10
    assert "20 more resources" in result
    assert "offset=10" in result


async def test_list_resources_pagination_offset() -> None:
    """list_resources respects offset parameter."""
    entries = [ResourceEntry(uri=f"mcp://server/res{i}", name=f"res{i}") for i in range(30)]
    ra = FakeResourceAccess(resources=entries)
    registry = _make_registry_with_caps(ra)
    agent_ctx = _make_agent_context(registry)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()
    result = await cap.list_resources(ctx, limit=10, offset=20)

    data_lines = [line for line in result.split("\n") if "mcp://server/res" in line]
    assert len(data_lines) == 10
    assert "res20" in result
    assert "res29" in result
    # No "more" message since we're at the end
    assert "more resources" not in result


async def test_list_resources_pagination_offset_beyond_total() -> None:
    """list_resources with offset beyond total returns empty message."""
    entries = [ResourceEntry(uri="mcp://server/only", name="only")]
    ra = FakeResourceAccess(resources=entries)
    registry = _make_registry_with_caps(ra)
    agent_ctx = _make_agent_context(registry)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()
    result = await cap.list_resources(ctx, offset=100)

    assert "No resources at offset 100" in result
    assert "Total: 1 resource" in result


async def test_list_resource_templates_pagination() -> None:
    """list_resource_templates truncates at default limit and shows 'more' message."""
    templates = [
        ResourceTemplateEntry(uri_template=f"file:///dir{i}/{{path}}", name=f"tpl{i}")
        for i in range(60)
    ]
    rta = FakeResourceTemplateAccess(templates=templates)
    registry = _make_registry_with_caps(rta)
    agent_ctx = _make_agent_context(registry)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()
    result = await cap.list_resource_templates(ctx)

    data_lines = [line for line in result.split("\n") if "tpl" in line and "Source" not in line]
    assert len(data_lines) == 50
    assert "10 more templates" in result
    assert "offset=50" in result


# =============================================================================
# Truncation tests
# =============================================================================


async def test_read_resource_text_truncation() -> None:
    """read_resource truncates text content exceeding 10,000 chars."""
    long_text = "A" * 15_000
    ra = FakeResourceAccess(
        read_contents=[TextResourceContent(uri="mcp://server/big.txt", text=long_text)],
    )
    registry = _make_registry_with_caps(ra)
    agent_ctx = _make_agent_context(registry)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()
    result = await cap.read_resource(ctx, "FakeResourceAccess", "mcp://server/big.txt")

    assert isinstance(result, ToolReturn)
    assert "[truncated: 15000 chars total" in result.return_value
    assert "showing first 10000" in result.return_value
    # The return value should contain the truncated text + suffix
    assert len(result.return_value) < len(long_text)


async def test_read_resource_text_no_truncation_at_limit() -> None:
    """read_resource does not truncate text at exactly 10,000 chars."""
    text = "B" * 10_000
    ra = FakeResourceAccess(
        read_contents=[TextResourceContent(uri="mcp://server/exact.txt", text=text)],
    )
    registry = _make_registry_with_caps(ra)
    agent_ctx = _make_agent_context(registry)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()
    result = await cap.read_resource(ctx, "FakeResourceAccess", "mcp://server/exact.txt")

    assert isinstance(result, ToolReturn)
    assert "[truncated" not in result.return_value
    # return_value is the joined text parts; with XML wrapper it contains the full text
    assert text in result.return_value


async def test_read_resource_skill_truncation() -> None:
    """read_resource truncates long skill content."""
    long_content = "C" * 12_000
    sr = FakeSkillResource(
        skills=[SkillEntry(name="big-skill", uri="skill://big-skill/SKILL.md")],
        read_content=long_content,
        exists_names={"big-skill"},
    )
    registry = _make_registry_with_caps(sr)
    agent_ctx = _make_agent_context(registry)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()
    result = await cap.read_resource(ctx, "skill", "skill://big-skill/SKILL.md")

    assert isinstance(result, ToolReturn)
    assert "[truncated: 12000 chars total" in result.return_value


# =============================================================================
# Completion suggestion cap tests
# =============================================================================


async def test_complete_resource_template_caps_suggestions() -> None:
    """complete_resource_template caps at 100 suggestions with total count."""
    values = [f"suggestion_{i}" for i in range(150)]
    rta = FakeResourceTemplateAccess(
        templates=[ResourceTemplateEntry(uri_template="file:///{path}")],
        completion_result=CompletionResult(values=values, total=150),
    )
    registry = _make_registry_with_caps(rta)
    agent_ctx = _make_agent_context(registry)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()
    result = await cap.complete_resource_template(ctx, "file:///{path}", "path", "f")

    # Should show first 100 suggestions
    assert "suggestion_0" in result
    assert "suggestion_99" in result
    assert "suggestion_100" not in result
    assert "150 total" in result
    assert "showing first 100" in result


# =============================================================================
# Multi-provider behavior tests
# =============================================================================


async def test_read_resource_provider_returns_none() -> None:
    """read_resource returns 'Resource not found' when matching provider returns None."""
    ra = FakeResourceAccess(read_contents=None)
    registry = _make_registry_with_caps(ra)
    agent_ctx = _make_agent_context(registry)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()
    result = await cap.read_resource(ctx, "FakeResourceAccess", "mcp://server/file.txt")

    assert isinstance(result, ToolReturn)
    assert result.return_value == "Resource not found: mcp://server/file.txt"


async def test_read_resource_mixed_text_and_blob() -> None:
    """read_resource handles mixed TextResourceContent and BlobResourceContent."""
    raw_data = b"\x89PNG\r\n\x1a\n"
    encoded = base64.b64encode(raw_data).decode("ascii")
    ra = FakeResourceAccess(
        read_contents=[
            TextResourceContent(uri="mcp://server/mixed", text="text part"),
            BlobResourceContent(
                uri="mcp://server/mixed",
                mime_type="image/png",
                blob=encoded,
            ),
        ],
    )
    registry = _make_registry_with_caps(ra)
    agent_ctx = _make_agent_context(registry)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()
    result = await cap.read_resource(ctx, "FakeResourceAccess", "mcp://server/mixed")

    assert isinstance(result, ToolReturn)
    assert result.content is not None
    assert len(result.content) == 1
    binary = result.content[0]
    assert isinstance(binary, BinaryContent)
    assert binary.data == raw_data
    assert binary.media_type == "image/png"
    assert "text part" in result.return_value
    assert "[Binary resource:" in result.return_value


async def test_resource_exists_multiple_providers_first_false_second_true() -> None:
    """resource_exists returns True if any provider has the resource."""
    ra_no = FakeResourceAccess(exists_uris=set())
    ra_yes = FakeResourceAccess(exists_uris={"mcp://server/file.txt"})
    registry = _make_registry_with_caps(ra_no, ra_yes)
    agent_ctx = _make_agent_context(registry)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()
    result = await cap.resource_exists(ctx, "mcp://server/file.txt")

    assert result is True


async def test_list_resources_multiple_providers_same_type() -> None:
    """list_resources aggregates from multiple ResourceAccess providers."""
    ra1 = FakeResourceAccess(
        resources=[ResourceEntry(uri="mcp://srv1/a", name="a")],
    )
    ra2 = FakeResourceAccess(
        resources=[ResourceEntry(uri="mcp://srv2/b", name="b")],
    )
    registry = _make_registry_with_caps(ra1, ra2)
    agent_ctx = _make_agent_context(registry)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()
    result = await cap.list_resources(ctx)

    assert "mcp://srv1/a" in result
    assert "mcp://srv2/b" in result


# =============================================================================
# Error and edge case tests
# =============================================================================


async def test_resolve_agent_context_wrong_deps_type() -> None:
    """_resolve_agent_context raises RuntimeError for non-AgentContextDeps deps."""
    ctx = MagicMock()
    ctx.deps = "not an AgentContextDeps"

    cap = ResourceCapability()
    with pytest.raises(RuntimeError, match="ResourceCapability requires AgentContextDeps"):
        await cap.list_resources(ctx)


async def test_resolve_agent_context_from_runtime_context() -> None:
    """_resolve_agent_context unwraps AgentContext from RuntimeAgentContext.data.

    In production, PydanticAI wraps our AgentContextDeps inside
    agents.context.AgentContext.data. The tool functions receive
    ctx.deps = agents.context.AgentContext, and our
    capabilities.agent_context.AgentContextDeps is at ctx.deps.data.
    """
    from wolfharness.agents.context import AgentContext as RuntimeAgentContext

    agent_ctx = _make_agent_context(registry=None)
    runtime_ctx = RuntimeAgentContext(node=MagicMock())
    runtime_ctx.data = agent_ctx

    ctx = MagicMock()
    ctx.deps = runtime_ctx

    cap = ResourceCapability()
    result = await cap.list_resources(ctx)

    # Should NOT raise — should return "No resources available." since
    # extension_registry is None on the inner AgentContextDeps.
    assert result == "No resources available."


async def test_resolve_agent_context_none_deps() -> None:
    """_resolve_agent_context raises RuntimeError when deps is None."""
    ctx = MagicMock()
    ctx.deps = None

    cap = ResourceCapability()
    with pytest.raises(
        RuntimeError, match=r"ResourceCapability requires AgentContextDeps as deps\. Got: None"
    ):
        await cap.list_resources(ctx)


async def test_resolve_agent_context_runtime_ctx_none_data() -> None:
    """_resolve_agent_context raises RuntimeError when RuntimeAgentContext.data is None."""
    from wolfharness.agents.context import AgentContext as RuntimeAgentContext

    runtime_ctx = RuntimeAgentContext(node=MagicMock())
    runtime_ctx.data = None

    ctx = MagicMock()
    ctx.deps = runtime_ctx

    cap = ResourceCapability()
    with pytest.raises(
        RuntimeError,
        match=r"ResourceCapability requires AgentContextDeps at deps\.data\. Got: None",
    ):
        await cap.list_resources(ctx)


async def test_resolve_agent_context_neither_type() -> None:
    """_resolve_agent_context raises RuntimeError for unknown deps type."""
    ctx = MagicMock()
    ctx.deps = object()

    cap = ResourceCapability()
    with pytest.raises(
        RuntimeError, match=r"ResourceCapability requires AgentContextDeps as deps\. Got: object"
    ):
        await cap.list_resources(ctx)


async def test_list_resources_providers_return_empty() -> None:
    """list_resources returns 'No resources available.' when providers return empty lists."""
    ra = FakeResourceAccess(resources=[])
    sr = FakeSkillResource(skills=[])
    registry = _make_registry_with_caps(ra, sr)
    agent_ctx = _make_agent_context(registry)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()
    result = await cap.list_resources(ctx)

    assert result == "No resources available."


async def test_resource_exists_skill_not_found_multiple_providers() -> None:
    """resource_exists returns False when no skill provider has the skill."""
    sr1 = FakeSkillResource(exists_names={"skill_a"})
    sr2 = FakeSkillResource(exists_names={"skill_b"})
    registry = _make_registry_with_caps(sr1, sr2)
    agent_ctx = _make_agent_context(registry)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()
    result = await cap.resource_exists(ctx, "skill://nonexistent/SKILL.md")

    assert result is False


async def test_complete_resource_template_multiple_matching_providers() -> None:
    """complete_resource_template returns first successful provider's result."""
    rta1 = FakeResourceTemplateAccess(
        templates=[ResourceTemplateEntry(uri_template="file:///{path}")],
        completion_result=CompletionResult(values=["from_first"]),
    )
    rta2 = FakeResourceTemplateAccess(
        templates=[ResourceTemplateEntry(uri_template="file:///{path}")],
        completion_result=CompletionResult(values=["from_second"]),
    )
    registry = _make_registry_with_caps(rta1, rta2)
    agent_ctx = _make_agent_context(registry)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()
    result = await cap.complete_resource_template(ctx, "file:///{path}", "path", "f")

    assert "from_first" in result
    assert "from_second" not in result


# =============================================================================
# Server-explicit read_resource and server-filter tests (OpenCode compat)
# =============================================================================


class AnotherFakeResourceAccess:
    """Second ResourceAccess implementation with a different class name for disambiguation tests."""

    def __init__(
        self,
        *,
        resources: list[ResourceEntry] | None = None,
        read_contents: list[TextResourceContent | BlobResourceContent] | None = None,
    ) -> None:
        self._resources = resources or []
        self._read_contents = read_contents

    async def list_resources(self) -> Sequence[ResourceEntry]:
        return list(self._resources)

    async def read_resource(
        self, uri: str
    ) -> list[TextResourceContent | BlobResourceContent] | None:
        if self._read_contents is None:
            return None
        return list(self._read_contents)

    async def resource_exists(self, uri: str) -> bool:
        return False


async def test_read_resource_unknown_server() -> None:
    """read_resource returns error when the named server is not in the registry."""
    ra = FakeResourceAccess(
        resources=[ResourceEntry(uri="mcp://server/file.txt", name="file.txt")],
    )
    registry = _make_registry_with_caps(ra)
    agent_ctx = _make_agent_context(registry)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()
    result = await cap.read_resource(ctx, "NonExistentServer", "mcp://server/file.txt")

    assert "not found" in result.return_value
    assert "NonExistentServer" in result.return_value
    assert result.content is None


async def test_read_resource_blob_non_allowlist_mime() -> None:
    """read_resource returns text marker for blob with non-allowlisted MIME type."""
    blob_data = base64.b64encode(b"binary data here").decode()
    ra = FakeResourceAccess(
        read_contents=[
            BlobResourceContent(
                uri="mcp://server/blob",
                blob=blob_data,
                mime_type="application/octet-stream",
            ),
        ],
    )
    registry = _make_registry_with_caps(ra)
    agent_ctx = _make_agent_context(registry)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()
    result = await cap.read_resource(ctx, "FakeResourceAccess", "mcp://server/blob")

    assert result.content is None
    assert "[Binary MCP resource omitted:" in result.return_value
    assert "application/octet-stream" in result.return_value


async def test_read_resource_blob_exceeds_size_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """read_resource returns text marker when blob exceeds the size limit.

    Uses monkeypatch to lower the size limit to 10 bytes so we don't need
    to allocate 10 MB of data in a unit test.
    """
    from wolfharness.capabilities import resource_capability as rc_module

    monkeypatch.setattr(rc_module, "_MAX_BLOB_SIZE_BYTES", 10)

    blob_data = base64.b64encode(b"x" * 100).decode()  # 100 bytes >> 10 byte limit
    ra = FakeResourceAccess(
        read_contents=[
            BlobResourceContent(
                uri="mcp://server/image.png",
                blob=blob_data,
                mime_type="image/png",
            ),
        ],
    )
    registry = _make_registry_with_caps(ra)
    agent_ctx = _make_agent_context(registry)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()
    result = await cap.read_resource(ctx, "FakeResourceAccess", "mcp://server/image.png")

    assert result.content is None
    assert "[Binary MCP resource omitted:" in result.return_value
    assert "exceeds size limit" in result.return_value


async def test_list_resources_server_filter() -> None:
    """list_resources filters results to the specified server."""
    ra1 = FakeResourceAccess(
        resources=[ResourceEntry(uri="mcp://srv1/a", name="a")],
    )
    ra2 = AnotherFakeResourceAccess(
        resources=[ResourceEntry(uri="mcp://srv2/b", name="b")],
    )
    registry = _make_registry_with_caps(ra1, ra2)
    agent_ctx = _make_agent_context(registry)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()

    # Filter to FakeResourceAccess only
    result_ra = await cap.list_resources(ctx, server="FakeResourceAccess")
    assert "mcp://srv1/a" in result_ra
    assert "mcp://srv2/b" not in result_ra

    # Filter to AnotherFakeResourceAccess only
    result_ara = await cap.list_resources(ctx, server="AnotherFakeResourceAccess")
    assert "mcp://srv1/a" not in result_ara
    assert "mcp://srv2/b" in result_ara

    # Non-existent server → empty
    result_empty = await cap.list_resources(ctx, server="NonExistent")
    assert result_empty == "No resources available."


async def test_list_resource_templates_server_filter() -> None:
    """list_resource_templates filters results to the specified server."""
    rta = FakeResourceTemplateAccess(
        templates=[ResourceTemplateEntry(uri_template="file:///{path}", name="tpl_a")],
    )
    registry = _make_registry_with_caps(rta)
    agent_ctx = _make_agent_context(registry)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()

    # Filter to FakeResourceTemplateAccess
    result = await cap.list_resource_templates(ctx, server="FakeResourceTemplateAccess")
    assert "file:///{path}" in result
    assert "tpl_a" in result

    # Non-existent server → empty
    result_empty = await cap.list_resource_templates(ctx, server="NonExistent")
    assert result_empty == "No resource templates available."


async def test_read_resource_server_explicit_disambiguation() -> None:
    """read_resource uses server param to select correct provider when two have same URI."""
    ra = FakeResourceAccess(
        read_contents=[
            TextResourceContent(uri="mcp://shared/uri", text="content from FakeResourceAccess"),
        ],
    )
    ara = AnotherFakeResourceAccess(
        read_contents=[
            TextResourceContent(
                uri="mcp://shared/uri",
                text="content from AnotherFakeResourceAccess",
            ),
        ],
    )
    registry = _make_registry_with_caps(ra, ara)
    agent_ctx = _make_agent_context(registry)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()

    # Read from FakeResourceAccess
    result_a = await cap.read_resource(ctx, "FakeResourceAccess", "mcp://shared/uri")
    assert "content from FakeResourceAccess" in result_a.return_value

    # Read from AnotherFakeResourceAccess
    result_b = await cap.read_resource(ctx, "AnotherFakeResourceAccess", "mcp://shared/uri")
    assert "content from AnotherFakeResourceAccess" in result_b.return_value


async def test_list_mcp_resources_pages_across_servers() -> None:
    """Opaque cursors preserve upstream pages and stable cross-server order."""
    alpha = FakeMcpServerCap(
        "alpha",
        resource_pages={
            None: ResourcePage(
                entries=[ResourceEntry(uri="kb://alpha/one", name="one")],
                next_cursor="alpha-next",
            ),
            "alpha-next": ResourcePage(
                entries=[ResourceEntry(uri="kb://alpha/two", name="two")],
            ),
        },
    )
    beta = FakeMcpServerCap(
        "beta",
        resource_pages={
            None: ResourcePage(entries=[ResourceEntry(uri="kb://beta/three", name="three")])
        },
    )
    registry = _make_registry_with_caps(beta, alpha)
    ctx = _make_ctx(_make_agent_context(registry))
    cap = ResourceCapability()

    first = await cap.list_mcp_resources(ctx, limit=2)
    assert [(item.server, item.uri) for item in first.resources] == [
        ("alpha", "kb://alpha/one"),
        ("alpha", "kb://alpha/two"),
    ]
    assert first.next_cursor is not None

    second = await cap.list_mcp_resources(ctx, cursor=first.next_cursor, limit=2)
    assert [(item.server, item.uri) for item in second.resources] == [("beta", "kb://beta/three")]
    assert second.next_cursor is None


async def test_list_mcp_resources_rejects_cursor_filter_mismatch() -> None:
    """A cursor cannot be reused with a different server filter."""
    provider = FakeMcpServerCap(
        "alpha",
        resource_pages={
            None: ResourcePage(
                entries=[
                    ResourceEntry(uri="kb://one", name="one"),
                    ResourceEntry(uri="kb://two", name="two"),
                ]
            )
        },
    )
    ctx = _make_ctx(_make_agent_context(_make_registry_with_caps(provider)))
    cap = ResourceCapability()

    first = await cap.list_mcp_resources(ctx, limit=1)
    assert first.next_cursor is not None
    invalid = await cap.list_mcp_resources(
        ctx,
        server="alpha",
        cursor=first.next_cursor,
    )

    assert invalid.resources == []
    assert invalid.errors[0].code == "invalid_cursor"


async def test_list_mcp_resources_preserves_partial_results() -> None:
    """One provider failure is reported without discarding later results."""
    failing = FakeMcpServerCap("alpha", page_error=RuntimeError("server offline"))
    healthy = FakeMcpServerCap(
        "beta",
        resource_pages={None: ResourcePage(entries=[ResourceEntry(uri="kb://ok", name="ok")])},
    )
    ctx = _make_ctx(_make_agent_context(_make_registry_with_caps(failing, healthy)))

    result = await ResourceCapability().list_mcp_resources(ctx)

    assert [(item.server, item.uri) for item in result.resources] == [("beta", "kb://ok")]
    assert result.errors[0].code == "provider_unavailable"
    assert result.errors[0].server == "alpha"


async def test_list_mcp_resource_templates_returns_server_attribution() -> None:
    """Template fields and server identity are preserved for consumers."""
    provider = FakeMcpServerCap(
        "unikb",
        template_pages={
            None: ResourceTemplatePage(
                entries=[
                    ResourceTemplateEntry(
                        uri_template="kb:///resources/{resource_id}",
                        name="resource",
                        title="Knowledge resource",
                        description="Read one resource",
                        mime_type="application/json",
                        annotations={"audience": ["assistant"]},
                        meta={"source": "unikb"},
                    )
                ]
            )
        },
    )
    ctx = _make_ctx(_make_agent_context(_make_registry_with_caps(provider)))

    result = await ResourceCapability().list_mcp_resource_templates(ctx)

    assert result.templates[0].server == "unikb"
    assert result.templates[0].title == "Knowledge resource"
    assert result.templates[0].annotations == {"audience": ["assistant"]}
    assert result.templates[0].meta == {"source": "unikb"}


async def test_read_mcp_resource_routes_by_server_and_preserves_html() -> None:
    """Reads use exact server plus URI and preserve model-readable text."""
    shared_uri = "kb:///resources/manual/sections/ch-03?view=text"
    alpha = FakeMcpServerCap(
        "alpha",
        read_results={shared_uri: [TextResourceContent(uri=shared_uri, text="wrong server")]},
    )
    unikb = FakeMcpServerCap(
        "unikb",
        read_results={
            shared_uri: [
                TextResourceContent(
                    uri=shared_uri,
                    mime_type="text/html",
                    text="<div><table><tr><td>原始内容</td></tr></table></div>",
                    meta={"image_uri": "kb:///resources/manual/images/img-1"},
                )
            ]
        },
    )
    ctx = _make_ctx(_make_agent_context(_make_registry_with_caps(alpha, unikb)))

    result = await ResourceCapability().read_mcp_resource(ctx, "unikb", shared_uri)

    assert isinstance(result, ToolReturn)
    assert isinstance(result.return_value.contents[0], McpTextContentResult)
    assert result.return_value.contents[0].text == (
        "<div><table><tr><td>原始内容</td></tr></table></div>"
    )
    assert result.return_value.contents[0].meta == {
        "image_uri": "kb:///resources/manual/images/img-1"
    }


async def test_read_mcp_resource_blob_exposes_metadata_not_base64() -> None:
    """Supported binary content is attached while structured data stays small."""
    uri = "kb:///resources/manual/images/img-1"
    encoded = base64.b64encode(b"png-bytes").decode()
    provider = FakeMcpServerCap(
        "unikb",
        read_results={
            uri: [
                BlobResourceContent(
                    uri=uri,
                    mime_type="image/png",
                    blob=encoded,
                )
            ]
        },
    )
    ctx = _make_ctx(_make_agent_context(_make_registry_with_caps(provider)))

    result = await ResourceCapability().read_mcp_resource(ctx, "unikb", uri)

    content = result.return_value.contents[0]
    assert isinstance(content, McpBlobContentResult)
    assert content.attached is True
    assert content.size == len(b"png-bytes")
    assert encoded not in result.return_value.model_dump_json()
    assert result.content is not None
    assert isinstance(result.content[0], BinaryContent)


async def test_mcp_resource_tools_return_structured_discovery_errors() -> None:
    """Unknown servers and unsupported resource servers are actionable."""
    unsupported = FakeMcpServerCap("tools-only", supports_resources=False)
    ctx = _make_ctx(_make_agent_context(_make_registry_with_caps(unsupported)))
    cap = ResourceCapability()

    unknown = await cap.list_mcp_resources(ctx, server="missing")
    unsupported_read = await cap.read_mcp_resource(
        ctx,
        "tools-only",
        "kb:///resources",
    )

    assert unknown.errors[0].code == "unknown_server"
    assert unknown.errors[0].suggestion
    assert unsupported_read.return_value.error is not None
    assert unsupported_read.return_value.error.code == "resources_not_supported"


@pytest.mark.parametrize(
    ("exception", "expected_code", "retryable"),
    [
        (TimeoutError("request timeout"), "timeout", True),
        (PermissionError("forbidden"), "permission_denied", False),
        (ConnectionError("connection refused"), "provider_unavailable", True),
    ],
)
async def test_read_mcp_resource_maps_provider_errors(
    exception: OSError,
    expected_code: str,
    retryable: bool,
) -> None:
    """Transport failures map to the stable structured error taxonomy."""
    provider = FakeMcpServerCap("unikb", read_error=exception)
    ctx = _make_ctx(_make_agent_context(_make_registry_with_caps(provider)))

    result = await ResourceCapability().read_mcp_resource(
        ctx,
        "unikb",
        "kb:///resources",
    )

    assert result.return_value.error is not None
    assert result.return_value.error.code == expected_code
    assert result.return_value.error.retryable is retryable
    assert result.return_value.error.suggestion


async def test_read_mcp_resource_returns_not_found_and_text_truncation() -> None:
    """Missing and oversized text reads stay explicit and machine-readable."""
    missing_uri = "kb:///missing"
    long_uri = "kb:///long"
    provider = FakeMcpServerCap(
        "unikb",
        read_results={long_uri: [TextResourceContent(uri=long_uri, text="x" * 10_001)]},
    )
    ctx = _make_ctx(_make_agent_context(_make_registry_with_caps(provider)))
    cap = ResourceCapability()

    missing = await cap.read_mcp_resource(ctx, "unikb", missing_uri)
    long_text = await cap.read_mcp_resource(ctx, "unikb", long_uri)

    assert missing.return_value.error is not None
    assert missing.return_value.error.code == "resource_not_found"
    content = long_text.return_value.contents[0]
    assert isinstance(content, McpTextContentResult)
    assert content.truncated is True
    assert content.original_char_count == 10_001
    assert len(content.text) == 10_000


@pytest.mark.parametrize(
    ("mime_type", "size_limit", "expected_code"),
    [
        ("application/octet-stream", None, "unsupported_mime_type"),
        ("image/png", 1, "content_too_large"),
    ],
)
async def test_read_mcp_resource_reports_blob_omission(
    monkeypatch: pytest.MonkeyPatch,
    mime_type: str,
    size_limit: int | None,
    expected_code: str,
) -> None:
    """Unsupported and oversized blobs return metadata plus omission errors."""
    if size_limit is not None:
        from wolfharness.capabilities import resource_capability as rc_module

        monkeypatch.setattr(rc_module, "_MAX_BLOB_SIZE_BYTES", size_limit)
    uri = "kb:///blob"
    provider = FakeMcpServerCap(
        "unikb",
        read_results={
            uri: [
                BlobResourceContent(
                    uri=uri,
                    mime_type=mime_type,
                    blob=base64.b64encode(b"blob").decode(),
                )
            ]
        },
    )
    ctx = _make_ctx(_make_agent_context(_make_registry_with_caps(provider)))

    result = await ResourceCapability().read_mcp_resource(ctx, "unikb", uri)

    assert result.return_value.error is not None
    assert result.return_value.error.code == expected_code
    content = result.return_value.contents[0]
    assert isinstance(content, McpBlobContentResult)
    assert content.attached is False
    assert content.omission_reason
    assert result.content is None
