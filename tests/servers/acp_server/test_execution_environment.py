"""Tests for choosing the session execution environment from client capabilities."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aclientp.client.implementations import HeadlessACPClient
from aclientp.filesystem import ACPFileSystem
from aclientp.schema import (
    ClientCapabilities,
    FileSystemCapabilities,
    InitializeRequest,
    NewSessionRequest,
)
from exxec.acp_provider import ACPExecutionEnvironment
from exxec.local_provider import LocalExecutionEnvironment
import pytest

from agentpool.sessions.environment import use_session_environment
from agentpool_server.acp_server.acp_agent import AgentPoolACPAgent
from agentpool_server.acp_server.session import supports_client_execution


if TYPE_CHECKING:
    from pathlib import Path

    from agentpool import Agent
    from agentpool_server.acp_server.session import ACPSession


FULL_CAPS = ClientCapabilities(
    fs=FileSystemCapabilities(read_text_file=True, write_text_file=True), terminal=True
)
# What T3 Code advertises: it renders output but offers neither terminal nor fs.
NO_CAPS = ClientCapabilities(
    fs=FileSystemCapabilities(read_text_file=False, write_text_file=False), terminal=False
)


@pytest.mark.parametrize(
    ("capabilities", "expected"),
    [
        (FULL_CAPS, True),
        (NO_CAPS, False),
        (ClientCapabilities(fs=None, terminal=True), False),
        (
            ClientCapabilities(
                fs=FileSystemCapabilities(read_text_file=True, write_text_file=True),
                terminal=False,
            ),
            False,
        ),
        (
            ClientCapabilities(
                fs=FileSystemCapabilities(read_text_file=True, write_text_file=False),
                terminal=True,
            ),
            False,
        ),
    ],
)
def test_supports_client_execution(capabilities: ClientCapabilities, expected: bool) -> None:
    assert supports_client_execution(capabilities) is expected


async def _new_session(
    tmp_path: Path, agent: Agent, capabilities: ClientCapabilities
) -> tuple[AgentPoolACPAgent, ACPSession]:
    client = HeadlessACPClient(working_dir=tmp_path)
    acp_agent = AgentPoolACPAgent(client=client, default_agent=agent)
    request = InitializeRequest(protocol_version=1, client_capabilities=capabilities)
    await acp_agent.initialize(request)
    response = await acp_agent.new_session(NewSessionRequest(cwd=str(tmp_path)))
    await acp_agent.tasks.complete_tasks()
    session = acp_agent.session_manager.get_session(response.session_id)
    assert session is not None
    return acp_agent, session


async def test_capable_client_gets_acp_environment(
    tmp_path: Path, default_test_agent: Agent
) -> None:
    _, session = await _new_session(tmp_path, default_test_agent, FULL_CAPS)

    assert isinstance(session.env, ACPExecutionEnvironment)
    assert isinstance(session.fs, ACPFileSystem)
    with use_session_environment(session.environment):  # As in the session's turns
        assert default_test_agent.env is session.env
    assert default_test_agent.env is not session.env  # The shared agent keeps its own


async def test_client_without_capabilities_gets_local_environment(
    tmp_path: Path, default_test_agent: Agent
) -> None:
    (tmp_path / "AGENTS.md").write_text("Always answer in haiku.", encoding="utf-8")

    _, session = await _new_session(tmp_path, default_test_agent, NO_CAPS)

    assert isinstance(session.env, LocalExecutionEnvironment)
    assert session.env.cwd == str(tmp_path)
    assert not isinstance(session.fs, ACPFileSystem)
    with use_session_environment(session.environment):  # As in the session's turns
        assert default_test_agent.env is session.env
    assert default_test_agent.env is not session.env  # The shared agent keeps its own
    # Rules are read from the local filesystem instead of failing on terminal/create.
    assert session.rules_loading is not None
    rules = await session.rules_loading
    assert rules is not None
    assert "Always answer in haiku." in rules


async def test_client_without_capabilities_loads_local_skills(
    tmp_path: Path, default_test_agent: Agent
) -> None:
    skill_dir = tmp_path / ".claude" / "skills" / "local-skill"
    skill_dir.mkdir(parents=True)
    content = "---\nname: local-skill\ndescription: A local skill\n---\n\nDo local things.\n"
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")

    await _new_session(tmp_path, default_test_agent, NO_CAPS)

    pool = default_test_agent.agent_pool
    assert pool is not None
    assert "local-skill" in {skill.name for skill in pool.skills.list_skills()}


async def test_commands_run_in_the_session_cwd_without_a_client_terminal(
    tmp_path: Path, default_test_agent: Agent
) -> None:
    """Slash commands (/git-diff, ...) run on this host when the client has no terminal."""
    _, session = await _new_session(tmp_path, default_test_agent, NO_CAPS)

    output, exit_code = await session.run_command("pwd", [])

    assert exit_code == 0
    assert output.strip() == str(tmp_path)


async def test_run_command_takes_a_cwd_and_reports_failures(
    tmp_path: Path, default_test_agent: Agent
) -> None:
    sub = tmp_path / "with space"
    sub.mkdir()
    _, session = await _new_session(tmp_path, default_test_agent, NO_CAPS)

    output, exit_code = await session.run_command("pwd", [], cwd=str(sub))
    assert output.strip() == str(sub)

    output, exit_code = await session.run_command("sh", ["-c", "echo oops >&2; exit 3"])
    assert exit_code == 3
    assert "oops" in output


async def test_commands_run_in_the_client_terminal_when_it_has_one(
    tmp_path: Path, default_test_agent: Agent
) -> None:
    _, session = await _new_session(tmp_path, default_test_agent, FULL_CAPS)
    assert isinstance(session.env, ACPExecutionEnvironment)

    output, exit_code = await session.run_command("pwd", [])

    assert exit_code == 0
    assert output.strip() == str(tmp_path)


async def test_git_diff_command_runs_without_a_client_terminal(
    tmp_path: Path, default_test_agent: Agent, monkeypatch: pytest.MonkeyPatch
) -> None:
    """/git-diff used to refuse when the client had no terminal (T3 Code); it runs here."""
    import os
    import subprocess

    # Drop GIT_DIR & co. that git exports to hooks (pre-commit runs this test).
    for key in [k for k in os.environ if k.startswith("GIT_")]:
        monkeypatch.delenv(key)

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (tmp_path / "f.txt").write_text("one\n", encoding="utf-8")
    git("add", "f.txt")
    git("commit", "-qm", "first")
    (tmp_path / "f.txt").write_text("one\ntwo\n", encoding="utf-8")
    git("commit", "-qam", "second")
    _, session = await _new_session(tmp_path, default_test_agent, NO_CAPS)

    await session.execute_slash_command("/git-diff HEAD")

    staged = await default_test_agent.staged_content.consume_as_text()
    assert staged is not None
    assert "+two" in staged
