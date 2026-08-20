"""Tests for resource resolution utility and converter integration.

Covers the shared resource resolution utility (L1 unit tests) and the OpenCode
converter's integration of that utility when handling ``FilePartInput`` with
``ResourceSource`` (L2 integration tests).

The tests are written against the target API where:
- ``resolve_resource_content()`` lives in ``wolfharness.capabilities.resource_resolver``
- ``_resolve_resource()`` in the converter delegates to ``resolve_resource_content()``
  by filtering ``agent._all_capabilities`` via ``isinstance`` checks.
"""

from __future__ import annotations

import base64
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from pydantic_ai import BinaryContent
import pytest

from wolfharness.capabilities.resource_protocols import (
    BlobResourceContent,
    ResourceEntry,
    SkillEntry,
    TextResourceContent,
)
from wolfharness.capabilities.resource_resolver import resolve_resource_content


if TYPE_CHECKING:
    from collections.abc import Sequence


pytestmark = pytest.mark.unit


# =============================================================================
# Fake capability implementations
# =============================================================================


class FakeResourceAccess:
    """Minimal ``ResourceAccess`` implementation for testing.

    Implements the three async methods required by the ``ResourceAccess``
    protocol: ``list_resources``, ``read_resource``, ``resource_exists``.
    """

    def __init__(
        self,
        read_result: list[TextResourceContent | BlobResourceContent] | None = None,
        raise_exc: Exception | None = None,
        client_name: str = "test",
    ) -> None:
        self._read_result = read_result
        self._raise_exc = raise_exc
        self.client_name = client_name

    async def list_resources(self) -> Sequence[ResourceEntry]:
        return []

    async def read_resource(
        self, uri: str
    ) -> list[TextResourceContent | BlobResourceContent] | None:
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._read_result

    async def resource_exists(self, uri: str) -> bool:
        return self._read_result is not None


class FakeSkillResource:
    """Minimal ``SkillResource`` implementation for testing.

    Implements the three async methods required by the ``SkillResource``
    protocol: ``list_skills``, ``read_skill``, ``skill_exists``.
    """

    def __init__(
        self,
        read_result: str | None = None,
        raise_exc: Exception | None = None,
    ) -> None:
        self._read_result = read_result
        self._raise_exc = raise_exc

    async def list_skills(self) -> Sequence[SkillEntry]:
        return []

    async def read_skill(self, name: str) -> str | None:
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._read_result

    async def skill_exists(self, name: str) -> bool:
        return self._read_result is not None


class FakeAgent:
    """Minimal agent with ``host_context`` for integration tests.

    Provides an ``ExtensionRegistry`` with the given capabilities registered
    at POOL scope so ``_resolve_resource`` can find them via the registry.
    """

    def __init__(self, capabilities: list[Any], name: str = "fake-agent") -> None:
        from wolfharness.capabilities.extension_registry import ExtensionRegistry, Scope, ScopeLevel

        registry = ExtensionRegistry()
        pool_scope = Scope(level=ScopeLevel.POOL)
        for cap in capabilities:
            registry.register(cap, pool_scope)
        # Lightweight fake host_context — only extension_registry is accessed
        self.name = name
        self._host_context = SimpleNamespace(extension_registry=registry)

    @property
    def host_context(self) -> Any:
        return self._host_context


# =============================================================================
# L1 Unit Tests — resolve_resource_content()
# =============================================================================


async def test_resolve_resource_text_content() -> None:
    """Text resource via ResourceAccess returns XML-wrapped text."""
    cap = FakeResourceAccess(read_result=[TextResourceContent(text="hello", uri="viking://doc.md")])
    result = await resolve_resource_content("viking://doc.md", resource_caps=[cap], skill_caps=[])
    assert result is not None
    assert result == ['<resource uri="viking://doc.md">\nhello\n</resource>']


async def test_resolve_resource_binary_content() -> None:
    """Binary resource via ResourceAccess returns [str, BinaryContent, str]."""
    blob_data = base64.b64encode(b"img").decode()
    cap = FakeResourceAccess(
        client_name="viking",
        read_result=[
            BlobResourceContent(blob=blob_data, mime_type="image/png", uri="viking://img.png")
        ],
    )
    result = await resolve_resource_content("viking://img.png", resource_caps=[cap], skill_caps=[])
    assert result is not None
    assert len(result) == 3
    assert result[0] == '<resource uri="viking://img.png">\n'
    assert isinstance(result[1], BinaryContent)
    assert result[1].data == b"img"
    assert result[1].media_type == "image/png"
    assert result[2] == "\n</resource>"


async def test_resolve_resource_not_found() -> None:
    """No provider returns content → result is None."""
    cap = FakeResourceAccess(read_result=None)
    result = await resolve_resource_content("viking://missing", resource_caps=[cap], skill_caps=[])
    assert result is None


async def test_resolve_resource_read_returns_empty() -> None:
    """``read_resource()`` returns empty list → returns None."""
    cap = FakeResourceAccess(read_result=[])
    result = await resolve_resource_content("viking://empty", resource_caps=[cap], skill_caps=[])
    assert result is None


async def test_resolve_resource_read_raises_exception() -> None:
    """``read_resource()`` raises → logs, continues, returns None."""
    cap = FakeResourceAccess(raise_exc=RuntimeError("connection lost"))
    result = await resolve_resource_content("viking://error", resource_caps=[cap], skill_caps=[])
    assert result is None


async def test_resolve_resource_mixed_text_and_binary() -> None:
    """Both TextResourceContent and BlobResourceContent returned → all items in output."""
    blob_data = base64.b64encode(b"pic").decode()
    cap = FakeResourceAccess(
        read_result=[
            TextResourceContent(text="desc", uri="viking://mixed"),
            BlobResourceContent(blob=blob_data, mime_type="image/png", uri="viking://mixed"),
        ]
    )
    result = await resolve_resource_content("viking://mixed", resource_caps=[cap], skill_caps=[])
    assert result is not None
    # Text item → single XML-wrapped string
    assert result[0] == '<resource uri="viking://mixed">\ndesc\n</resource>'
    # Binary item → [str, BinaryContent, str]
    assert result[1] == '<resource uri="viking://mixed">\n'
    assert isinstance(result[2], BinaryContent)
    assert result[2].data == b"pic"
    assert result[2].media_type == "image/png"
    assert result[3] == "\n</resource>"


async def test_resolve_resource_multiple_providers() -> None:
    """First provider returns None, second returns content → returns content from second."""
    cap1 = FakeResourceAccess(read_result=None)
    cap2 = FakeResourceAccess(read_result=[TextResourceContent(text="found", uri="viking://doc")])
    result = await resolve_resource_content(
        "viking://doc", resource_caps=[cap1, cap2], skill_caps=[]
    )
    assert result is not None
    assert result == ['<resource uri="viking://doc">\nfound\n</resource>']


async def test_resolve_resource_skill_uri() -> None:
    """``skill://`` URI routed to SkillResource, not ResourceAccess."""
    skill_cap = FakeSkillResource(read_result="content")
    # ResourceAccess cap that would fail if called — proves routing skips it
    resource_cap = FakeResourceAccess(raise_exc=AssertionError("should not be called"))
    result = await resolve_resource_content(
        "skill://ponytail/SKILL.md",
        resource_caps=[resource_cap],
        skill_caps=[skill_cap],
    )
    assert result is not None
    assert result == ['<resource uri="skill://ponytail/SKILL.md">\ncontent\n</resource>']


async def test_resolve_resource_skill_reference_uri(tmp_path: Any) -> None:
    """``skill://`` URI with reference path reads the reference file, not SKILL.md."""
    from upathtools import UPath

    # Create a fake skill directory with a reference file
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# My Skill\nInstructions here.")
    ref_dir = skill_dir / "references"
    ref_dir.mkdir()
    (ref_dir / "guide.md").write_text("# Guide\nReference content here.")

    skill_entry = SkillEntry(
        name="my-skill",
        description="Test skill",
        uri="skill://my-skill",
        source="local",
        skill_path=UPath(str(skill_dir)),
    )

    class FakeSkillWithRef:
        """SkillResource that returns a skill with a real filesystem path."""

        async def list_skills(self) -> Sequence[SkillEntry]:
            return [skill_entry]

        async def read_skill(self, name: str) -> str | None:
            return "# My Skill\nInstructions here."

        async def skill_exists(self, name: str) -> bool:
            return name == "my-skill"

    skill_cap = FakeSkillWithRef()
    result = await resolve_resource_content(
        "skill://my-skill/references/guide.md",
        resource_caps=[],
        skill_caps=[skill_cap],
    )
    assert result is not None
    assert len(result) == 1
    content: str = result[0]  # type: ignore[assignment]
    assert "Reference content here." in content
    assert "Instructions here." not in content  # Should NOT return SKILL.md content


async def test_resolve_resource_skill_reference_not_found(tmp_path: Any) -> None:
    """``skill://`` URI with non-existent reference path returns None."""
    from upathtools import UPath

    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# My Skill")

    skill_entry = SkillEntry(
        name="my-skill",
        description="Test skill",
        uri="skill://my-skill",
        source="local",
        skill_path=UPath(str(skill_dir)),
    )

    class FakeSkillWithRef:
        async def list_skills(self) -> Sequence[SkillEntry]:
            return [skill_entry]

        async def read_skill(self, name: str) -> str | None:
            return "# My Skill"

        async def skill_exists(self, name: str) -> bool:
            return name == "my-skill"

    skill_cap = FakeSkillWithRef()
    result = await resolve_resource_content(
        "skill://my-skill/references/missing.md",
        resource_caps=[],
        skill_caps=[skill_cap],
    )
    assert result is None


async def test_resolve_resource_skill_reference_virtual_skill() -> None:
    """``skill://`` URI with reference path but virtual skill (no filesystem) returns None."""
    from pathlib import PurePosixPath

    skill_entry = SkillEntry(
        name="virtual-skill",
        description="Virtual skill",
        uri="skill://virtual-skill",
        source="remote",
        skill_path=PurePosixPath("skill://virtual-skill"),
    )

    class FakeVirtualSkill:
        async def list_skills(self) -> Sequence[SkillEntry]:
            return [skill_entry]

        async def read_skill(self, name: str) -> str | None:
            return "Virtual content"

        async def skill_exists(self, name: str) -> bool:
            return name == "virtual-skill"

    skill_cap = FakeVirtualSkill()
    result = await resolve_resource_content(
        "skill://virtual-skill/references/guide.md",
        resource_caps=[],
        skill_caps=[skill_cap],
    )
    assert result is None


async def test_resolve_resource_skill_reference_traversal_blocked(tmp_path: Any) -> None:
    """Path traversal in reference path is blocked by ResolvedSkillURI.parse()."""
    from upathtools import UPath

    from wolfharness.skills.exceptions import SecurityError

    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# My Skill")

    skill_entry = SkillEntry(
        name="my-skill",
        description="Test skill",
        uri="skill://my-skill",
        source="local",
        skill_path=UPath(str(skill_dir)),
    )

    class FakeSkillWithRef:
        async def list_skills(self) -> Sequence[SkillEntry]:
            return [skill_entry]

        async def read_skill(self, name: str) -> str | None:
            return "# My Skill"

        async def skill_exists(self, name: str) -> bool:
            return name == "my-skill"

    skill_cap = FakeSkillWithRef()
    # ResolvedSkillURI.parse() raises SecurityError for ".." in path
    with pytest.raises(SecurityError):
        await resolve_resource_content(
            "skill://my-skill/references/../../../etc/passwd",
            resource_caps=[],
            skill_caps=[skill_cap],
        )


async def test_resolve_resource_skill_reference_name_alternatives(tmp_path: Any) -> None:
    """Reference resolution tries underscore/hyphen name alternatives."""
    from upathtools import UPath

    skill_dir = tmp_path / "my_skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# My Skill")
    ref_dir = skill_dir / "references"
    ref_dir.mkdir()
    (ref_dir / "guide.md").write_text("# Guide\nAlt-name content.")

    skill_entry = SkillEntry(
        name="my_skill",
        description="Test skill with underscore",
        uri="skill://my_skill",
        source="local",
        skill_path=UPath(str(skill_dir)),
    )

    class FakeSkillWithRef:
        async def list_skills(self) -> Sequence[SkillEntry]:
            return [skill_entry]

        async def read_skill(self, name: str) -> str | None:
            return "# My Skill"

        async def skill_exists(self, name: str) -> bool:
            return name == "my_skill"

    skill_cap = FakeSkillWithRef()
    # URI uses hyphen, skill name uses underscore — should still resolve
    result = await resolve_resource_content(
        "skill://my-skill/references/guide.md",
        resource_caps=[],
        skill_caps=[skill_cap],
    )
    assert result is not None
    content: str = result[0]  # type: ignore[assignment]
    assert "Alt-name content." in content


async def test_resolve_resource_xml_wrapper_format() -> None:
    r"""Verify exact XML format for text: ``<resource uri="{uri}">\n{content}\n</resource>``."""
    cap = FakeResourceAccess(read_result=[TextResourceContent(text="hello", uri="viking://doc.md")])
    result = await resolve_resource_content("viking://doc.md", resource_caps=[cap], skill_caps=[])
    assert result is not None
    assert len(result) == 1
    expected = '<resource uri="viking://doc.md">\nhello\n</resource>'
    assert result[0] == expected


async def test_resolve_resource_text_truncation() -> None:
    """Text > max_text_chars → truncated with suffix."""
    long_text = "x" * 15_000
    cap = FakeResourceAccess(read_result=[TextResourceContent(text=long_text, uri="viking://big")])
    result = await resolve_resource_content(
        "viking://big", resource_caps=[cap], skill_caps=[], max_text_chars=10_000
    )
    assert result is not None
    assert len(result) == 1
    wrapped: str = result[0]  # type: ignore[assignment]
    # The XML wrapper contains the truncated text
    assert '<resource uri="viking://big">' in wrapped
    assert "</resource>" in wrapped
    # The body is the first 10_000 chars + suffix
    suffix = f"\n\n... [truncated: {len(long_text)} chars total, showing first 10000]"
    expected_body = long_text[:10_000] + suffix
    assert f'<resource uri="viking://big">\n{expected_body}\n</resource>' == wrapped


# =============================================================================
# L2 Integration Tests — extract_user_prompt_from_parts()
# =============================================================================


async def test_extract_user_prompt_with_binary_resource() -> None:
    """Resource returns BlobResourceContent → result contains BinaryContent in XML sandwich."""
    from wolfharness_server.opencode_server.converters import extract_user_prompt_from_parts
    from wolfharness_server.opencode_server.models import FilePartInput
    from wolfharness_server.opencode_server.models.common import TextSpan
    from wolfharness_server.opencode_server.models.parts import ResourceSource

    blob_data = base64.b64encode(b"img").decode()
    cap = FakeResourceAccess(
        client_name="viking",
        read_result=[
            BlobResourceContent(blob=blob_data, mime_type="image/png", uri="viking://img.png")
        ],
    )
    agent = FakeAgent(capabilities=[cap])

    source = ResourceSource(
        text=TextSpan(value="@viking:img.png", start=0, end=15),
        client_name="viking",
        uri="viking://img.png",
    )
    part = FilePartInput(mime="image/png", url="", source=source)

    result = await extract_user_prompt_from_parts([part], "test-session", agent=agent)
    result_list = list(result)
    assert len(result_list) == 3
    assert result_list[0] == '<resource uri="viking://img.png">\n'
    assert isinstance(result_list[1], BinaryContent)
    assert result_list[1].data == b"img"
    assert result_list[1].media_type == "image/png"
    assert result_list[2] == "\n</resource>"


async def test_extract_user_prompt_resource_no_agent() -> None:
    """agent=None → FilePartInput falls through to generic file handler."""
    from wolfharness_server.opencode_server.converters import extract_user_prompt_from_parts
    from wolfharness_server.opencode_server.models import FilePartInput
    from wolfharness_server.opencode_server.models.common import TextSpan
    from wolfharness_server.opencode_server.models.parts import ResourceSource

    source = ResourceSource(
        text=TextSpan(value="@viking:doc.md", start=0, end=14),
        client_name="viking",
        uri="viking://doc.md",
    )
    # Provide a data: URL so the generic file handler can produce content
    part = FilePartInput(
        mime="text/plain",
        url="data:text/plain;base64,aGVsbG8=",  # "hello" base64-encoded
        source=source,
    )

    result = await extract_user_prompt_from_parts([part], "test-session", agent=None)
    result_list = list(result)
    # The generic handler should produce some content (not None)
    assert len(result_list) >= 1


async def test_extract_user_prompt_mixed_parts() -> None:
    """Text + resource + agent parts all processed."""
    from wolfharness_server.opencode_server.converters import extract_user_prompt_from_parts
    from wolfharness_server.opencode_server.models import (
        AgentPartInput,
        FilePartInput,
        TextPartInput,
    )
    from wolfharness_server.opencode_server.models.common import TextSpan
    from wolfharness_server.opencode_server.models.parts import ResourceSource

    cap = FakeResourceAccess(
        client_name="viking",
        read_result=[TextResourceContent(text="resource content", uri="viking://doc")],
    )
    agent = FakeAgent(capabilities=[cap])

    text_part = TextPartInput(text="prefix text")
    source = ResourceSource(
        text=TextSpan(value="@viking:doc", start=0, end=10),
        client_name="viking",
        uri="viking://doc",
    )
    resource_part = FilePartInput(mime="text/plain", url="", source=source)
    agent_part = AgentPartInput(name="researcher")

    result = await extract_user_prompt_from_parts(
        [text_part, resource_part, agent_part], "test-session", agent=agent
    )
    result_list = list(result)
    # 1 text + 1 resource (XML-wrapped) + 1 agent instruction
    assert len(result_list) == 3
    assert result_list[0] == "prefix text"
    assert result_list[1] == '<resource uri="viking://doc">\nresource content\n</resource>'
    assert "researcher" in result_list[2]


# =============================================================================
# Timing bug reproducer — config-defined capabilities not yet in registry
# =============================================================================


async def test_resolve_resource_timing_bug() -> None:
    """Reproduce: ResourceAccess cap in _all_capabilities but NOT in ExtensionRegistry.

    Uses a real ``AgentPool`` with a ``NativeAgent`` that has a config-defined
    ``ResourceAccess`` capability (``TestResourceAccessCap``). The capability
    is eagerly built in ``NativeAgent.__init__()`` and stored in
    ``_all_capabilities``. However, it is only registered in the
    ``ExtensionRegistry`` during ``get_agentlet()``, which runs AFTER
    ``extract_user_prompt_from_parts()`` in the message handling pipeline.

    This test reproduces the production timing issue: before ``get_agentlet()``
    is called, the ExtensionRegistry does not contain the config-defined
    capability, so ``_resolve_resource()`` cannot find it.

    Expected behavior after fix: ``_resolve_resource()`` should find the cap
    even when the registry hasn't registered it yet.
    """
    from wolfharness import AgentPool, AgentsManifest, NativeAgentConfig
    from wolfharness_config.capabilities import GenericCapabilityConfig
    from wolfharness_server.opencode_server.converters import extract_user_prompt_from_parts
    from wolfharness_server.opencode_server.models import FilePartInput
    from wolfharness_server.opencode_server.models.common import TextSpan
    from wolfharness_server.opencode_server.models.parts import ResourceSource

    # Build a real agent config with a GenericCapabilityConfig pointing to
    # our test ResourceAccess capability.
    cap_config = GenericCapabilityConfig(
        type="tests.fixtures.test_resource_cap.TestResourceAccessCap",
        args={"read_text": "hello world", "read_uri": "test://doc.md"},
    )
    agent_config = NativeAgentConfig(
        name="test_agent",
        model="test",
        capabilities=[cap_config],
    )
    manifest = AgentsManifest(agents={"test_agent": agent_config})

    async with AgentPool(manifest) as pool:
        # Create the template agent directly from config.
        # This populates _config_capabilities_built (eager build in __init__)
        # but does NOT register them in the ExtensionRegistry — that only
        # happens in get_agentlet(), which we deliberately do NOT call.
        from wolfharness.agents.native_agent import Agent

        agent = Agent.from_config(
            agent_config,
            agent_pool=pool,
        )

        assert agent._config_capabilities_built, "No config capabilities built"

        # Verify the cap is in _all_capabilities but NOT in the registry
        from wolfharness.capabilities.extension_registry import Scope, ScopeLevel
        from wolfharness.capabilities.resource_protocols import ResourceAccess

        ra_caps = [c for c in agent._all_capabilities if isinstance(c, ResourceAccess)]
        assert len(ra_caps) >= 1, "No ResourceAccess cap in _all_capabilities"

        host_ctx = agent.host_context
        assert host_ctx is not None
        registry = host_ctx.extension_registry
        assert registry is not None
        scope = Scope(
            level=ScopeLevel.SESSION,
            agent_name="test_agent",
            session_id="test-session",
        )
        registry_caps = registry.get_resource_access(scope)
        # After the fix, the registry SHOULD have the config-defined cap
        # at AGENT scope (registered during pool init via factory.compile()).
        assert len(registry_caps) >= 1, "Registry should have config-defined cap at AGENT scope"
        assert any(isinstance(c, type(ra_caps[0])) for c in registry_caps), (
            "Registry should contain the same type of ResourceAccess cap"
        )

        # Verify ResourceCapability is NOT in the registry (it's a tool wrapper,
        # not a ResourceAccess provider).
        from wolfharness.capabilities.resource_capability import ResourceCapability

        assert not any(isinstance(c, ResourceCapability) for c in registry_caps), (
            "ResourceCapability should not be in get_resource_access() results"
        )

        # Now test _resolve_resource — should find the resource via the registry.
        source = ResourceSource(
            text=TextSpan(value="@test:doc.md", start=0, end=12),
            client_name="test",
            uri="test://doc.md",
        )
        part = FilePartInput(mime="text/plain", url="", source=source)

        result = await extract_user_prompt_from_parts([part], "test-session", agent=agent)
        result_list = list(result)

        # Should resolve the resource content — not drop it silently.
        assert len(result_list) == 1
        assert result_list[0] == '<resource uri="test://doc.md">\nhello world\n</resource>'


# =============================================================================
# L2 Integration Tests — end-to-end @-mention flow with real AgentPool
# =============================================================================


def _make_pool_config(
    agent_name: str = "test_agent",
    cap_type: str = "tests.fixtures.test_resource_cap.TestResourceAccessCap",
    cap_args: dict[str, Any] | None = None,
) -> str:
    """Build inline YAML config with a config-defined capability."""
    import json

    args_json = json.dumps(cap_args or {})
    return f"""\
agents:
  {agent_name}:
    type: native
    model: test
    system_prompt: "You are a test agent."
    capabilities:
      - type: {cap_type}
        args: {args_json}
"""


def _make_resource_part(uri: str, client_name: str = "test") -> Any:
    """Build a FilePartInput with a ResourceSource for @-mention testing."""
    from wolfharness_server.opencode_server.models import FilePartInput
    from wolfharness_server.opencode_server.models.common import TextSpan
    from wolfharness_server.opencode_server.models.parts import ResourceSource

    return FilePartInput(
        mime="text/plain",
        url="",
        source=ResourceSource(
            text=TextSpan(value=f"@{client_name}:{uri}", start=0, end=20),
            client_name=client_name,
            uri=uri,
        ),
    )


async def test_e2e_at_mention_resolves_resource() -> None:
    """L2: Real AgentPool → pool init → @-mention → resource content resolved.

    End-to-end flow:
    1. AgentPool created with config containing TestResourceAccessCap
    2. Pool init → factory.compile() → cap registered at AGENT scope
    3. Template agent created with cap in _all_capabilities
    4. extract_user_prompt_from_parts() → _resolve_resource() → registry query
    5. Resource content returned as XML-wrapped text
    """
    import yamling

    from wolfharness import AgentPool, AgentsManifest
    from wolfharness_server.opencode_server.converters import extract_user_prompt_from_parts

    config = _make_pool_config(
        cap_args={"read_text": "hello world", "read_uri": "test://doc.md"},
    )
    manifest = AgentsManifest.model_validate(yamling.load_yaml(config, verify_type=dict))

    async with AgentPool(manifest) as pool:
        from wolfharness.agents.native_agent import Agent

        agent = Agent.from_config(
            pool.agent_configs["test_agent"],
            agent_pool=pool,
        )

        part = _make_resource_part("test://doc.md")
        result = await extract_user_prompt_from_parts([part], "test-session", agent=agent)
        result_list = list(result)

        assert len(result_list) == 1
        assert result_list[0] == '<resource uri="test://doc.md">\nhello world\n</resource>'


async def test_e2e_at_mention_wrong_uri_returns_empty() -> None:
    """L2: @-mention with a URI that no cap can resolve → content dropped."""
    import yamling

    from wolfharness import AgentPool, AgentsManifest
    from wolfharness_server.opencode_server.converters import extract_user_prompt_from_parts

    config = _make_pool_config(
        cap_args={"read_text": "hello world", "read_uri": "test://doc.md"},
    )
    manifest = AgentsManifest.model_validate(yamling.load_yaml(config, verify_type=dict))

    async with AgentPool(manifest) as pool:
        from wolfharness.agents.native_agent import Agent

        agent = Agent.from_config(
            pool.agent_configs["test_agent"],
            agent_pool=pool,
        )

        # Wrong URI — no cap can resolve it
        part = _make_resource_part("test://nonexistent.md")
        result = await extract_user_prompt_from_parts([part], "test-session", agent=agent)
        result_list = list(result)

        # Resource content dropped — only text parts (if any) remain
        # Since we only sent a resource part, result should be empty
        assert len(result_list) == 0


async def test_e2e_resource_capability_excluded_from_registry() -> None:
    """L2: ResourceCapability is NOT in get_resource_access() results.

    ResourceCapability is a tool wrapper, not a ResourceAccess provider.
    It must not appear in registry query results to prevent signature
    mismatch errors (read_resource(ctx, uri) vs read_resource(uri)).
    """
    import yamling

    from wolfharness import AgentPool, AgentsManifest
    from wolfharness.capabilities.extension_registry import Scope, ScopeLevel
    from wolfharness.capabilities.resource_capability import ResourceCapability

    config = _make_pool_config()
    manifest = AgentsManifest.model_validate(yamling.load_yaml(config, verify_type=dict))

    async with AgentPool(manifest) as pool:
        registry = pool.extension_registry
        scope = Scope(
            level=ScopeLevel.SESSION,
            agent_name="test_agent",
            session_id="test-session",
        )
        resource_caps = list(registry.get_resource_access(scope))

        assert not any(isinstance(c, ResourceCapability) for c in resource_caps), (
            "ResourceCapability must not be in get_resource_access()"
        )


async def test_e2e_multiple_agents_resource_isolation() -> None:
    """L2: Two agents with different config caps → query finds only matching cap.

    Agent A has TestResourceAccessCap with read_uri="test://a.md".
    Agent B has TestResourceAccessCap with read_uri="test://b.md".
    Querying with agent_name="a" should NOT find agent B's cap.
    """
    import yamling

    from wolfharness import AgentPool, AgentsManifest
    from wolfharness.capabilities.extension_registry import Scope, ScopeLevel
    from wolfharness.capabilities.resource_protocols import ResourceAccess

    config = """\
agents:
  agent_a:
    type: native
    model: test
    capabilities:
      - type: tests.fixtures.test_resource_cap.TestResourceAccessCap
        args: {"read_text": "from A", "read_uri": "test://a.md"}
  agent_b:
    type: native
    model: test
    capabilities:
      - type: tests.fixtures.test_resource_cap.TestResourceAccessCap
        args: {"read_text": "from B", "read_uri": "test://b.md"}
"""
    manifest = AgentsManifest.model_validate(yamling.load_yaml(config, verify_type=dict))

    async with AgentPool(manifest) as pool:
        registry = pool.extension_registry

        # Query for agent_a → should find agent_a's cap, not agent_b's
        scope_a = Scope(level=ScopeLevel.AGENT, agent_name="agent_a")
        caps_a = [c for c in registry.get_resource_access(scope_a) if isinstance(c, ResourceAccess)]
        # Agent A's cap should be there. Agent B's should NOT (different agent_name).
        a_uris = [c.read_uri for c in caps_a if hasattr(c, "read_uri")]
        assert "test://a.md" in a_uris, "Agent A's cap should be visible"
        assert "test://b.md" not in a_uris, "Agent B's cap should NOT be visible from agent_a scope"

        # Query for agent_b → should find agent_b's cap, not agent_a's
        scope_b = Scope(level=ScopeLevel.AGENT, agent_name="agent_b")
        caps_b = [c for c in registry.get_resource_access(scope_b) if isinstance(c, ResourceAccess)]
        b_uris = [c.read_uri for c in caps_b if hasattr(c, "read_uri")]
        assert "test://b.md" in b_uris, "Agent B's cap should be visible"
        assert "test://a.md" not in b_uris, "Agent A's cap should NOT be visible from agent_b scope"


async def test_e2e_skill_resource_resolution() -> None:
    """L2: SkillResource cap → skill:// URI resolved via _resolve_resource."""
    import yamling

    from wolfharness import AgentPool, AgentsManifest
    from wolfharness_server.opencode_server.converters import extract_user_prompt_from_parts

    config = _make_pool_config(
        cap_type="tests.fixtures.test_resource_cap.TestSkillResourceCap",
        cap_args={"skill_text": "skill body", "skill_name": "test-skill"},
    )
    manifest = AgentsManifest.model_validate(yamling.load_yaml(config, verify_type=dict))

    async with AgentPool(manifest) as pool:
        from wolfharness.agents.native_agent import Agent

        agent = Agent.from_config(
            pool.agent_configs["test_agent"],
            agent_pool=pool,
        )

        part = _make_resource_part("skill://test-skill", client_name="test")
        result = await extract_user_prompt_from_parts([part], "test-session", agent=agent)
        result_list = list(result)

        assert len(result_list) == 1
        assert "skill body" in result_list[0]


# =============================================================================
# Parameterized Matrix Tests — scope combinations for get_resource_access
# =============================================================================


@pytest.mark.parametrize(
    ("cap_scope_level", "query_scope_level", "expected_found"),
    [
        # Cap at AGENT scope → query at different scopes
        ("AGENT", "SESSION", True),  # SESSION includes AGENT
        ("AGENT", "AGENT", True),  # AGENT sees AGENT
        ("AGENT", "POOL", False),  # POOL does NOT see AGENT
        # Cap at POOL scope → query at different scopes
        ("POOL", "SESSION", True),  # SESSION includes POOL
        ("POOL", "AGENT", True),  # AGENT includes POOL
        ("POOL", "POOL", True),  # POOL sees POOL
    ],
    ids=[
        "agent_cap_visible_at_session",
        "agent_cap_visible_at_agent",
        "agent_cap_invisible_at_pool",
        "pool_cap_visible_at_session",
        "pool_cap_visible_at_agent",
        "pool_cap_visible_at_pool",
    ],
)
async def test_scope_visibility_matrix(
    cap_scope_level: str,
    query_scope_level: str,
    expected_found: bool,
) -> None:
    """Matrix: cap registered at scope X → query at scope Y → found or not.

    Scope hierarchy: POOL > AGENT > SESSION > TURN
    Query at a scope sees everything at that scope level and above (outer).
    """
    from wolfharness.capabilities.extension_registry import (
        ExtensionRegistry,
        Scope,
        ScopeLevel,
    )

    registry = ExtensionRegistry()
    cap = FakeResourceAccess(read_result=[TextResourceContent(text="x", uri="test://x")])

    cap_scope = Scope(
        level=ScopeLevel[cap_scope_level],
        agent_name="test_agent" if cap_scope_level == "AGENT" else "",
    )
    registry.register(cap, cap_scope)

    query_scope = Scope(
        level=ScopeLevel[query_scope_level],
        agent_name="test_agent" if query_scope_level in ("AGENT", "SESSION") else "",
        session_id="s1" if query_scope_level == "SESSION" else "",
    )
    results = list(registry.get_resource_access(query_scope))
    found = any(c is cap for c in results)
    assert found == expected_found


@pytest.mark.parametrize(
    ("agent_name_cap", "agent_name_query", "expected_found"),
    [
        ("agent_a", "agent_a", True),  # Same agent → found
        ("agent_a", "agent_b", False),  # Different agent → NOT found
    ],
    ids=[
        "same_agent_finds_cap",
        "different_agent_does_not_find_cap",
    ],
)
async def test_agent_isolation_matrix(
    agent_name_cap: str,
    agent_name_query: str,
    expected_found: bool,
) -> None:
    """Matrix: cap at AGENT scope for agent A → query for agent B → not found."""
    from wolfharness.capabilities.extension_registry import (
        ExtensionRegistry,
        Scope,
        ScopeLevel,
    )

    registry = ExtensionRegistry()
    cap = FakeResourceAccess(read_result=[TextResourceContent(text="x", uri="test://x")])

    registry.register(cap, Scope(level=ScopeLevel.AGENT, agent_name=agent_name_cap))

    query_scope = Scope(
        level=ScopeLevel.SESSION,
        agent_name=agent_name_query,
        session_id="s1",
    )
    results = list(registry.get_resource_access(query_scope))
    found = any(c is cap for c in results)
    assert found == expected_found


# =============================================================================
# L3 Full agent creation — catches duplicate-instance tool conflicts
# =============================================================================


async def test_config_cap_no_duplicate_tools_after_get_agentlet() -> None:
    """Config-defined cap with tools must not conflict after get_agentlet().

    This test catches the duplicate-instance bug: if _compile_agent_capabilities()
    includes config-defined caps in its RETURNED list (not just registering them
    in the ExtensionRegistry), those caps get injected as _extra_capabilities
    (factory.py line ~438) AND the agent builds its own copies in __init__()
    as _external_capabilities. Both sets end up in _all_capabilities → same
    tool names → pydantic-ai raises a tool conflict error.

    The fix: register config-defined caps directly at AGENT scope inside
    _compile_agent_capabilities() but do NOT include them in the returned list.
    """
    from wolfharness import AgentPool, AgentsManifest, NativeAgentConfig
    from wolfharness_config.capabilities import GenericCapabilityConfig

    cap_config = GenericCapabilityConfig(
        type="tests.fixtures.test_resource_cap.TestToolAndResourceCap",
        args={"read_text": "hello", "read_uri": "test://doc.md"},
    )
    agent_config = NativeAgentConfig(
        name="test_agent",
        model="test",
        capabilities=[cap_config],
    )
    manifest = AgentsManifest(agents={"test_agent": agent_config})

    async with AgentPool(manifest) as pool:
        # Create a per-session agent via the full factory path.
        # This triggers _extra_capabilities injection (line ~438) which
        # would conflict with _external_capabilities if config caps
        # are in the returned list from _compile_agent_capabilities().
        agent = await pool.session_pool.sessions.get_or_create_session_agent(
            session_id="test-session",
            agent_name="test_agent",
        )

        # _extra_capabilities is populated from _capability_registry
        # (factory.py line ~438). Config-defined caps must NOT be in
        # _extra_capabilities — they're already in _external_capabilities
        # (built in __init__). If both lists have the same cap type,
        # get_agentlet() will assemble duplicate tools → pydantic-ai
        # raises "tool name conflicts" error.
        from wolfharness.capabilities.resource_protocols import ResourceAccess

        extra_ra = [c for c in agent._extra_capabilities if isinstance(c, ResourceAccess)]
        assert len(extra_ra) == 0, (
            f"_extra_capabilities has {len(extra_ra)} ResourceAccess caps — "
            "config caps leaked from _compile_agent_capabilities() return list. "
            "This causes duplicate tool conflicts in get_agentlet()."
        )

        # _external_capabilities should have exactly 1 (from __init__).
        ext_ra = [c for c in agent._external_capabilities if isinstance(c, ResourceAccess)]
        assert len(ext_ra) == 1, (
            f"_external_capabilities has {len(ext_ra)} ResourceAccess caps, expected exactly 1"
        )
