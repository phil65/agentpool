"""Tests for discovering client-side skills over the ACP filesystem."""

from __future__ import annotations

from textwrap import dedent
from typing import TYPE_CHECKING

from acp.client.implementations import HeadlessACPClient
from acp.filesystem import ACPFileSystem, ACPPath
from acp.schema import ForkSessionRequest, InitializeRequest, NewSessionRequest
from agentpool.skills.registry import SkillsRegistry
from agentpool_server.acp_server.acp_agent import AgentPoolACPAgent


if TYPE_CHECKING:
    from pathlib import Path

    from agentpool import Agent


def _write_skill(root: Path, name: str) -> None:
    skill_dir = root / ".claude" / "skills" / name
    skill_dir.mkdir(parents=True)
    content = f"""\
    ---
    name: {name}
    description: Skill {name}
    ---

    Instructions for {name}.
    """
    (skill_dir / "SKILL.md").write_text(dedent(content), encoding="utf-8")


def _skill_names(agent: Agent) -> set[str]:
    pool = agent.agent_pool
    assert pool is not None
    return {skill.name for skill in pool.skills.list_skills()}


async def test_registry_binds_skills_to_source_filesystem(tmp_path: Path) -> None:
    _write_skill(tmp_path, "remote-skill")
    client = HeadlessACPClient(working_dir=tmp_path)
    fs = ACPFileSystem(client=client, session_id="test")
    registry = SkillsRegistry()

    await registry.register_skills_from_path(fs, base_path=str(tmp_path / ".claude" / "skills"))

    skill = registry.get("remote-skill")
    assert isinstance(skill.skill_path, ACPPath)
    assert skill.skill_path.path == str(tmp_path / ".claude" / "skills" / "remote-skill")
    # Instructions come from the content read over ACP, not a later local read.
    assert skill.instructions == "Instructions for remote-skill."


async def test_session_loads_skills_from_cwd_and_additional_directories(
    tmp_path: Path, default_test_agent: Agent
) -> None:
    project = tmp_path / "project"
    shared = tmp_path / "shared"
    project.mkdir()
    shared.mkdir()
    _write_skill(project, "project-skill")
    _write_skill(shared, "shared-skill")
    client = HeadlessACPClient(working_dir=tmp_path)
    acp_agent = AgentPoolACPAgent(client=client, default_agent=default_test_agent)
    await acp_agent.initialize(InitializeRequest(protocol_version=1))
    request = NewSessionRequest(cwd=str(project), additional_directories=[str(shared)])
    await acp_agent.new_session(request)
    await acp_agent.tasks.complete_tasks()

    names = _skill_names(default_test_agent)
    assert {"project-skill", "shared-skill"} <= names


async def test_forked_session_loads_client_skills(
    tmp_path: Path, default_test_agent: Agent
) -> None:
    _write_skill(tmp_path, "fork-skill")
    client = HeadlessACPClient(working_dir=tmp_path)
    acp_agent = AgentPoolACPAgent(client=client, default_agent=default_test_agent)
    await acp_agent.initialize(InitializeRequest(protocol_version=1))

    await acp_agent.fork_session(ForkSessionRequest(session_id="original", cwd=str(tmp_path)))
    await acp_agent.tasks.complete_tasks()

    names = _skill_names(default_test_agent)
    assert "fork-skill" in names
