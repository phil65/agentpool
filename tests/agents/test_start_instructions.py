"""External agents give a backend session the session's instructions when it starts."""

from dataclasses import dataclass, field
from typing import Any

from exxec import LocalExecutionEnvironment

from agentpool.agents.claude_code_agent import ClaudeCodeAgent
from agentpool.agents.codex_agent import CodexAgent
from agentpool.sessions import Session
from agentpool.sessions.environment import (
    SessionEnvironment,
    instructions_update,
    use_session_environment,
)


ENV = SessionEnvironment(env=LocalExecutionEnvironment(), instructions="Working directory: /w")


def test_updates_carry_the_instructions_in_full_once_changed() -> None:
    assert instructions_update("same", "same") is None
    assert (
        instructions_update("", "Use tabs")
        == "<session-instructions>\nUse tabs\n</session-instructions>"
    )
    cleared = instructions_update("Use tabs", "")
    assert cleared is not None
    assert "cleared" in cleared


async def test_claude_code_puts_them_into_the_process_system_prompt() -> None:
    agent = ClaudeCodeAgent(name="cc", system_prompt="Be terse.", enable_logging=False)
    with agent.use_session(Session.create("cc")), use_session_environment(ENV):
        process = await agent._open_process()
        assert process.client is not None
        system_prompt = process.client.options.system_prompt
        assert process.instructions == "Working directory: /w"
        await agent._close_process(process)
    assert isinstance(system_prompt, str)
    assert "Be terse." in system_prompt
    assert "Working directory: /w" in system_prompt


@dataclass
class _Thread:
    thread_id: str = "t1"


@dataclass
class _Client:
    started: list[dict[str, Any]] = field(default_factory=list)

    async def thread_start(self, **kwargs: Any) -> _Thread:
        self.started.append(kwargs)
        return _Thread()


async def test_codex_puts_them_into_the_threads_developer_instructions() -> None:
    agent = CodexAgent(name="cx", developer_instructions="Own rules.", enable_logging=False)
    client = _Client()
    agent._client = client  # type: ignore[assignment]  # ty:ignore[invalid-assignment]
    with agent.use_session(Session.create("cx")), use_session_environment(ENV):
        await agent._start_thread()
    [started] = client.started
    assert started["developer_instructions"] == "Own rules.\n\nWorking directory: /w"
    assert agent._thread_instructions == {"t1": "Working directory: /w"}
