"""Claude Code compacts with the CLI's ``/compact`` and forks the CLI's session."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest

from agentpool.agents.claude_code_agent import ClaudeCodeAgent
from agentpool.agents.events import CompactionEvent


if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def _agent(prompts: list[tuple[str, bool]], phase: str = "completed") -> ClaudeCodeAgent:
    agent = ClaudeCodeAgent(name="cc")
    agent._sdk_session_id = "cli-1"

    async def run_stream(prompt: str, **kwargs: Any) -> AsyncIterator[Any]:
        prompts.append((prompt, kwargs["store_history"]))
        yield CompactionEvent(session_id="s", phase=phase, error="too short")  # type: ignore[arg-type]

    agent.run_stream = run_stream  # type: ignore[method-assign]
    return agent


async def test_compact_runs_the_clis_compact_as_an_unstored_turn() -> None:
    prompts: list[tuple[str, bool]] = []
    agent = _agent(prompts)
    assert await agent.compact() == (0, 0)  # Our copy of the conversation stays
    assert prompts == [("/compact", False)]


async def test_a_failed_compaction_is_raised() -> None:
    agent = _agent([], phase="failed")
    with pytest.raises(RuntimeError, match="too short"):
        await agent.compact()


@dataclass
class _Forked:
    session_id: str


async def test_fork_copies_the_cli_session_for_the_fork(monkeypatch: pytest.MonkeyPatch) -> None:
    import clawd_code_sdk

    forked_from: list[tuple[str, str | None]] = []

    def fork_session(session_id: str, directory: str | None = None) -> _Forked:
        forked_from.append((session_id, directory))
        return _Forked(session_id="cli-2")

    monkeypatch.setattr(clawd_code_sdk, "fork_session", fork_session)
    agent = ClaudeCodeAgent(name="cc")
    agent._sdk_session_id = "cli-1"
    forked = await agent.fork_session()
    assert [source for source, _ in forked_from] == ["cli-1"]
    assert forked.sdk_session_id == "cli-2"  # The fork's turns resume the copy
    assert agent._sdk_session_id == "cli-1"  # The source keeps its own


async def test_fork_at_a_message_is_refused() -> None:
    from agentpool.agents.exceptions import UnsupportedOperationError

    agent = ClaudeCodeAgent(name="cc")
    agent._sdk_session_id = "cli-1"
    with pytest.raises(UnsupportedOperationError):
        await agent.fork_session(at_message_id="msg-1")
