"""An unstored Claude Code turn runs on a fork of the CLI session (issue #50.1a/b)."""

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

from clawd_code_sdk.models import ResultSuccessMessage, StatusSystemMessage, Usage
import pytest

from agentpool.agents.claude_code_agent import ClaudeCodeAgent
from agentpool.agents.claude_code_agent.cli_process import CliProcess
from agentpool.agents.claude_code_agent.idle_watch import IdleWatcher


if TYPE_CHECKING:
    from collections.abc import AsyncIterator


pytestmark = pytest.mark.timeout(30)


class _FakeClient:
    """A CLI client that answers each query on ``session_id``."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.session_state = "idle"
        self.query_usage = Usage(input_tokens=1, output_tokens=1)
        self.query_cost = 0.0
        self.prompt_uuid = ""
        self.connected = False
        self.disconnected = False
        self.interrupt = AsyncMock()

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.disconnected = True

    async def query(self, *prompts: Any, uuid: str) -> None:
        self.prompt_uuid = uuid

    async def receive_response(self) -> AsyncIterator[Any]:
        yield StatusSystemMessage(uuid="1", session_id=self.session_id, status=None)
        yield ResultSuccessMessage(
            uuid="2",
            session_id=self.session_id,
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            total_cost_usd=0.0,
            usage=self.query_usage,
            stop_reason="end_turn",
            result="done",
            user_message_uuids=[self.prompt_uuid],
        )


def _agent(main: _FakeClient) -> ClaudeCodeAgent:
    agent = ClaudeCodeAgent(name="cc", enable_logging=False)
    process = CliProcess("", idle_watch=IdleWatcher(agent))
    process.client = main  # type: ignore[assignment]  # ty:ignore[invalid-assignment]
    process.idle_watch = MagicMock(stop=AsyncMock())
    agent._processes[""] = process
    agent._sdk_session_id = "cli-1"
    return agent


async def test_unstored_turn_keeps_the_sessions_cli_id(monkeypatch: pytest.MonkeyPatch) -> None:
    main, fork = _FakeClient("cli-1"), _FakeClient("fork-1")
    agent = _agent(main)
    monkeypatch.setattr(agent, "_get_client", lambda fork_session=False: fork)
    recorded = AsyncMock()
    monkeypatch.setattr(agent, "_record_sdk_session_id", recorded)
    await agent.run("what did we discuss?", store_history=False)
    assert fork.connected
    assert fork.disconnected
    assert agent._sdk_session_id == "cli-1"  # The fork's ID would resume the wrong session
    recorded.assert_not_awaited()


async def test_a_failed_fork_connect_is_disconnected(monkeypatch: pytest.MonkeyPatch) -> None:
    main, fork = _FakeClient("cli-1"), _FakeClient("fork-1")
    fork.connect = AsyncMock(side_effect=RuntimeError("no CLI"))  # type: ignore[method-assign]
    agent = _agent(main)
    monkeypatch.setattr(agent, "_get_client", lambda fork_session=False: fork)
    with pytest.raises(RuntimeError, match="no CLI"):
        await agent.run("hi", store_history=False)
    assert fork.disconnected


async def test_compact_runs_on_the_session_itself(monkeypatch: pytest.MonkeyPatch) -> None:
    main = _FakeClient("cli-2")  # The CLI may move to a new ID when compacting
    agent = _agent(main)
    monkeypatch.setattr(agent, "_get_client", MagicMock(side_effect=AssertionError("forked")))
    monkeypatch.setattr(agent, "_record_sdk_session_id", AsyncMock())
    await agent.compact()
    assert agent._sdk_session_id == "cli-2"
