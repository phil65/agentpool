"""Native steering of a running turn (``steer()``)."""

from __future__ import annotations

from agentpool.agents.codex_agent import CodexAgent


async def test_codex_agent_native_steer() -> None:
    """CodexAgent.steer() delegates to codexed's session.turn_steer / turn_interrupt."""

    class _FakeSession:
        def __init__(self) -> None:
            self.steered: list[tuple[str, str]] = []
            self.interrupted: list[str] = []

        async def turn_steer(self, user_input: str, *, expected_turn_id: str) -> None:
            self.steered.append((user_input, expected_turn_id))

        async def turn_interrupt(self, turn_id: str) -> None:
            self.interrupted.append(turn_id)

    class _FakeAdapter:
        _current_turn_id = "turn_42"

    agent = CodexAgent(name="codex-steer", enable_logging=False)
    fake_session = _FakeSession()
    agent._client = object()  # type: ignore[assignment]
    agent._sdk_session_id = "thread_1"
    agent._sessions["thread_1"] = fake_session  # type: ignore[assignment]
    agent._adapters["thread_1"] = _FakeAdapter()  # type: ignore[assignment]

    await agent.steer("use pytest")
    assert fake_session.steered == [("use pytest", "turn_42")]
    assert not agent.has_queued_prompts()

    await agent.steer("stop and switch", interrupt=True)
    assert fake_session.interrupted == ["turn_42"]
    assert agent.has_queued_prompts()
