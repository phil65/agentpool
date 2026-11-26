"""Tests for the skill model, the MCP skills filesystem, and Skills over MCP (SEP-2640)."""

import base64
from pathlib import Path
import sys
from typing import TYPE_CHECKING

from fastmcp import Client, FastMCP
from fastmcp.skills import MCPSkill, SkillsClientExtension
from fastmcp.skills.provider import SkillsDirectoryProvider
from mcp.types import BlobResourceContents, TextResourceContents
from pydantic_ai import ModelRetry
import pytest
from upathtools import to_upath

from agentpool import Agent, AgentPool
from agentpool.agents.context import AgentContext
from agentpool.mcp_server.skills_fs import MCPSkillsFileSystem
from agentpool.resource_providers.mcp_provider import MCPResourceProvider
from agentpool.skills.skill import Skill, SkillVerificationError
from agentpool_config.mcp_server import StdioMCPServerConfig
from agentpool_config.pool_server import MCPPoolServerConfig
from agentpool_server.mcp_server.server import MCPServer
from agentpool_toolsets.builtin.skills import (
    MAX_READ_CHARS,
    SkillsTools,
    list_skills,
    load_skill,
    read_skill_file,
)


if TYPE_CHECKING:
    from collections.abc import AsyncIterator


SERVER_SCRIPT = Path(__file__).parent / "skills_server.py"

DEMO_SKILL_MD = """\
---
name: demo-skill
description: Demo skill for testing
allowed-tools: Bash
when_to_use: Use when testing skills.
hooks: something
custom-key: custom value
---

Read references/guide.md from ${SKILL_DIR}.
"""


def write_skill(root: Path, name: str, content: str, files: dict[str, str] | None = None) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8", newline="")
    for rel, text in (files or {}).items():
        target = skill_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="")
    return skill_dir


@pytest.fixture
def skills_root(tmp_path: Path) -> Path:
    root = tmp_path / "skills"
    write_skill(root, "demo-skill", DEMO_SKILL_MD, {"references/guide.md": "guide text\n"})
    return root


# --- Skill model ---------------------------------------------------------------------


def test_frontmatter_is_lenient_and_reports_behavioral_fields(skills_root: Path) -> None:
    skill = Skill.from_skill_dir(to_upath(skills_root / "demo-skill"))

    assert skill.when_to_use == "Use when testing skills."
    assert skill.extra_frontmatter == {"hooks": "something", "custom-key": "custom value"}
    assert skill.ignored_behavioral_fields == ("allowed-tools", "hooks")
    assert skill.catalog_description == "Demo skill for testing Use when testing skills."
    assert skill.qualified_name == "demo-skill"


def test_name_directory_mismatch_still_loads(tmp_path: Path) -> None:
    content = "---\nname: other-name\ndescription: Mismatched\n---\nBody"
    skill_dir = write_skill(tmp_path, "dir-name", content)

    skill = Skill.from_skill_dir(to_upath(skill_dir))

    assert skill.name == "other-name"


def test_empty_compatibility_is_rejected(tmp_path: Path) -> None:
    content = "---\nname: compat\ndescription: Compat\ncompatibility: ''\n---\nBody"
    skill_dir = write_skill(tmp_path, "compat", content)

    with pytest.raises(ValueError, match="compatibility"):
        Skill.from_skill_dir(to_upath(skill_dir))


async def test_render_local_skill(skills_root: Path) -> None:
    skill = Skill.from_skill_dir(to_upath(skills_root / "demo-skill"))

    rendered = await skill.render()

    skill_dir = str(skills_root / "demo-skill")
    assert rendered.startswith('<skill_content name="demo-skill">')
    assert rendered.endswith("</skill_content>")
    assert f"Read references/guide.md from {skill_dir}." in rendered
    assert f"Skill directory: {skill_dir}" in rendered
    assert "<file>references/guide.md</file>" in rendered
    assert "<file>SKILL.md</file>" not in rendered


async def test_read_file_stays_inside_skill(skills_root: Path, tmp_path: Path) -> None:
    (tmp_path / "secret.txt").write_text("secret")
    (skills_root / "demo-skill" / "link.txt").symlink_to(tmp_path / "secret.txt")
    skill = Skill.from_skill_dir(to_upath(skills_root / "demo-skill"))

    assert await skill.read_file("references/guide.md") == b"guide text\n"
    for bad in ("../secret.txt", "/etc/passwd", "references/../../secret.txt", "link.txt"):
        with pytest.raises(ValueError, match="skill directory"):
            await skill.read_file(bad)
    with pytest.raises(FileNotFoundError):
        await skill.read_file("missing.md")


# --- MCP skills filesystem (in-memory FastMCP server) ---------------------------------


class InMemoryReader:
    """Resource reader over an in-memory FastMCP client, optionally tampering with files."""

    def __init__(self, client: Client[object], tamper: set[str] | None = None) -> None:
        self.client = client
        self.tamper = tamper or set()
        self.reads: list[str] = []

    async def read_resource_bytes(self, uri: str) -> bytes:
        self.reads.append(uri)
        contents = await self.client.read_resource(uri)
        match contents[0]:
            case TextResourceContents(text=text):
                data = text.encode("utf-8")
            case BlobResourceContents(blob=blob):
                data = base64.b64decode(blob)
        return data.replace(b"guide", b"GUIDE") if uri in self.tamper else data


@pytest.fixture
async def skills_client(skills_root: Path) -> AsyncIterator[Client[object]]:
    server = FastMCP("skills")
    server.add_provider(SkillsDirectoryProvider(skills_root))
    async with Client(server, extensions=[SkillsClientExtension()]) as client:
        yield client


def mcp_skill(fs: MCPSkillsFileSystem, root: str, entry: MCPSkill) -> Skill:
    return Skill.from_frontmatter(
        fs.get_upath(root), entry.frontmatter.root, server="srv", agent_accessible=False
    )


async def test_fs_lists_from_entries_and_verifies_reads(skills_client: Client[object]) -> None:
    reader = InMemoryReader(skills_client)
    fs = MCPSkillsFileSystem(reader)
    accepted = fs.set_entries(await skills_client.list_skills())
    assert list(accepted) == ["demo-skill"]
    skill = mcp_skill(fs, "demo-skill", accepted["demo-skill"])

    assert await skill.list_files() == ["SKILL.md", "references/guide.md"]
    assert reader.reads == []  # listing needs no network traffic
    assert await skill.read_file("references/guide.md") == b"guide text\n"
    assert await skill.read_file("references/guide.md") == b"guide text\n"
    assert len(reader.reads) == 1  # verified bytes are cached
    with pytest.raises(FileNotFoundError):
        await skill.read_file("not-listed.md")


async def test_fs_rejects_tampered_content(skills_client: Client[object]) -> None:
    reader = InMemoryReader(skills_client, tamper={"skill://demo-skill/references/guide.md"})
    fs = MCPSkillsFileSystem(reader)
    accepted = fs.set_entries(await skills_client.list_skills())
    skill = mcp_skill(fs, "demo-skill", accepted["demo-skill"])

    with pytest.raises(SkillVerificationError, match="digest"):
        await skill.read_file("references/guide.md")


async def test_frontmatter_must_match_entry(skills_client: Client[object]) -> None:
    fs = MCPSkillsFileSystem(InMemoryReader(skills_client))
    entry = (await skills_client.list_skills())[0]
    forged = entry.model_copy(
        update={
            "frontmatter": type(entry.frontmatter)({**entry.frontmatter.root, "description": "x"})
        }
    )
    accepted = fs.set_entries([forged])
    skill = mcp_skill(fs, "demo-skill", accepted["demo-skill"])

    with pytest.raises(SkillVerificationError, match="advertised entry"):
        await skill.load_instructions()


async def test_fs_skips_dynamic_entries(skills_client: Client[object]) -> None:
    fs = MCPSkillsFileSystem(InMemoryReader(skills_client))
    entry = (await skills_client.list_skills())[0]
    dynamic = MCPSkill(uri=entry.uri, frontmatter=entry.frontmatter, resources="dynamic")

    assert fs.set_entries([dynamic]) == {}
    assert not await fs._exists("demo-skill/SKILL.md")


async def test_fs_is_read_only(skills_client: Client[object]) -> None:
    fs = MCPSkillsFileSystem(InMemoryReader(skills_client))
    fs.set_entries(await skills_client.list_skills())

    with pytest.raises(PermissionError):
        await fs._pipe_file("demo-skill/new.md", b"x")


# --- MCPResourceProvider over a stdio server ---------------------------------------------


def stdio_config(root: Path, *, skills: bool = True) -> StdioMCPServerConfig:
    return StdioMCPServerConfig(
        name="skillsrv",
        command=sys.executable,
        args=[str(SERVER_SCRIPT), str(root)],
        skills=skills,
    )


async def test_provider_publishes_mcp_skills(skills_root: Path) -> None:
    async with MCPResourceProvider(stdio_config(skills_root)) as provider:
        skills = await provider.get_skills()

        assert [s.qualified_name for s in skills] == ["skillsrv/demo-skill"]
        skill = skills[0]
        assert skill.agent_accessible is False
        assert skill.allowed_tools is None
        rendered = await skill.render()
        assert '<skill_content name="skillsrv/demo-skill">' in rendered
        # The agent cannot reach the directory, so the placeholder stays and the
        # rendered text points at read_skill_file instead of a directory.
        assert "${SKILL_DIR}" in rendered
        assert "Skill directory:" not in rendered
        assert "read_skill_file" in rendered
        assert "<file>references/guide.md</file>" in rendered
        assert "Allowed tools" not in rendered
        assert await provider.get_skill_instructions("demo-skill") == (
            "Read references/guide.md from ${SKILL_DIR}."
        )


async def test_provider_respects_disabled_skills(skills_root: Path) -> None:
    async with MCPResourceProvider(stdio_config(skills_root, skills=False)) as provider:
        assert await provider.get_skills() == []


# --- Skills tools --------------------------------------------------------------------


async def test_tools_combine_pool_and_mcp_skills_without_shadowing(
    skills_root: Path, tmp_path: Path
) -> None:
    local_root = tmp_path / "local"
    write_skill(
        local_root, "demo-skill", "---\nname: demo-skill\ndescription: Local\n---\nLocal body"
    )
    hidden = "---\nname: hidden\ndescription: Hidden\ndisable-model-invocation: true\n---\nB"
    write_skill(local_root, "hidden", hidden)
    async with AgentPool() as pool:
        await pool.skills.add_skills_directory(local_root)
        agent = Agent(
            name="skills_agent",
            model="test",
            mcp_servers=[stdio_config(skills_root)],
            toolsets=[SkillsTools()],
        )
        await pool.add_agent(agent)
        ctx = AgentContext(node=agent, pool=pool)

        listing = await list_skills(ctx)
        assert "**demo-skill**: Local" in listing
        assert "**skillsrv/demo-skill**: Demo skill for testing Use when testing skills." in listing
        assert "hidden" not in listing

        assert "Local body" in await load_skill(ctx, "demo-skill")
        assert "${SKILL_DIR}" in await load_skill(ctx, "skillsrv/demo-skill")
        with pytest.raises(ModelRetry, match="not found"):
            await load_skill(ctx, "hidden")

        text = await read_skill_file(ctx, "skillsrv/demo-skill", "references/guide.md")
        assert text == "guide text\n"
        with pytest.raises(ModelRetry, match="no file"):
            await read_skill_file(ctx, "skillsrv/demo-skill", "missing.md")
        with pytest.raises(ModelRetry, match="skill directory"):
            await read_skill_file(ctx, "demo-skill", "../outside.md")


async def test_read_skill_file_windows_long_files(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    long_text = "x" * (MAX_READ_CHARS + 10)
    write_skill(root, "long", "---\nname: long\ndescription: Long\n---\nB", {"big.txt": long_text})
    async with AgentPool() as pool:
        await pool.skills.add_skills_directory(root)
        agent = Agent(name="long_agent", model="test")
        await pool.add_agent(agent)
        ctx = AgentContext(node=agent, pool=pool)

        first = await read_skill_file(ctx, "long", "big.txt")
        assert first.startswith("x" * MAX_READ_CHARS)
        assert f"read on from offset={MAX_READ_CHARS}" in first
        assert await read_skill_file(ctx, "long", "big.txt", offset=MAX_READ_CHARS) == "x" * 10
        with pytest.raises(ModelRetry, match="past the end"):
            await read_skill_file(ctx, "long", "big.txt", offset=len(long_text))


# --- Serving the pool's skills -------------------------------------------------------


async def test_mcp_server_serves_pool_skills(skills_root: Path) -> None:
    oversized = {f"f{i}.txt": "x" for i in range(513)}
    write_skill(skills_root, "too-big", "---\nname: too-big\ndescription: Big\n---\nB", oversized)
    async with AgentPool() as pool:
        await pool.skills.add_skills_directory(skills_root)
        server = MCPServer(pool, MCPPoolServerConfig())
        async with Client(server.fastmcp, extensions=[SkillsClientExtension()]) as client:
            uris = {e.uri for e in await client.list_skills()}
            # The pool also discovers the default skill directories, so check membership.
            assert "skill://demo-skill/SKILL.md" in uris
            assert "skill://too-big/SKILL.md" not in uris
            served = await client.read_skill("skill://demo-skill/SKILL.md")

    assert served.files["references/guide.md"] == b"guide text\n"
    assert served.files["SKILL.md"] == DEMO_SKILL_MD.encode()


async def test_mcp_server_skills_can_be_disabled(skills_root: Path) -> None:
    async with AgentPool() as pool:
        await pool.skills.add_skills_directory(skills_root)
        server = MCPServer(pool, MCPPoolServerConfig(serve_skills=False))
        async with Client(server.fastmcp, extensions=[SkillsClientExtension()]) as client:
            capabilities = client.server_capabilities
            extensions = capabilities.extensions if capabilities else None
            assert SkillsClientExtension.identifier not in (extensions or {})
