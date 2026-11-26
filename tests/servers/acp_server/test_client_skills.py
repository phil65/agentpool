"""Tests for discovering client-side skills over the ACP filesystem."""

from textwrap import dedent
from typing import TYPE_CHECKING

from aclientp.client.implementations import HeadlessACPClient
from aclientp.filesystem import ACPFileSystem, ACPPath
from aclientp.schema import ForkSessionRequest, InitializeRequest, NewSessionRequest

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


async def _skill_names(acp_agent: AgentPoolACPAgent, session_id: str) -> set[str]:
    session = acp_agent.session_manager.get_session(session_id)
    assert session is not None
    return {skill.name for skill in await session.workspace_skills.get_skills()}


def _pool_skill_names(agent: Agent) -> set[str]:
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
    response = await acp_agent.new_session(request)
    await acp_agent.tasks.complete_tasks()

    names = await _skill_names(acp_agent, response.session_id)
    assert {"project-skill", "shared-skill"} <= names


async def test_forked_session_loads_client_skills(
    tmp_path: Path, default_test_agent: Agent
) -> None:
    _write_skill(tmp_path, "fork-skill")
    client = HeadlessACPClient(working_dir=tmp_path)
    acp_agent = AgentPoolACPAgent(client=client, default_agent=default_test_agent)
    await acp_agent.initialize(InitializeRequest(protocol_version=1))

    source = await acp_agent.new_session(NewSessionRequest(cwd=str(tmp_path)))
    request = ForkSessionRequest(session_id=source.session_id, cwd=str(tmp_path))
    fork = await acp_agent.fork_session(request)
    await acp_agent.tasks.complete_tasks()

    assert "fork-skill" in await _skill_names(acp_agent, fork.session_id)


async def test_client_skills_stay_in_their_session(
    tmp_path: Path, default_test_agent: Agent
) -> None:
    """Skills of one workspace don't leak into the pool or other workspaces' sessions."""
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    _write_skill(first, "first-skill")
    client = HeadlessACPClient(working_dir=tmp_path)
    acp_agent = AgentPoolACPAgent(client=client, default_agent=default_test_agent)
    await acp_agent.initialize(InitializeRequest(protocol_version=1))

    a = await acp_agent.new_session(NewSessionRequest(cwd=str(first)))
    b = await acp_agent.new_session(NewSessionRequest(cwd=str(second)))
    await acp_agent.tasks.complete_tasks()

    assert "first-skill" in await _skill_names(acp_agent, a.session_id)
    assert "first-skill" not in await _skill_names(acp_agent, b.session_id)
    assert "first-skill" not in _pool_skill_names(default_test_agent)
