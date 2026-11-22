"""Tests for long-running steerable session streams (BaseAgent.subscribe / submit / steer)."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from pydantic_ai.models.test import TestModel

from agentpool import Agent
from agentpool.agents.codex_agent import CodexAgent
from agentpool.agents.events import (
    NoticeEvent,
    RunErrorEvent,
    RunStartedEvent,
    StreamCompleteEvent,
)


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import pytest


async def test_submit_and_subscribe_multi_turn_and_fanout() -> None:
    """submit() returns immediately, fans out events to all subscribers, and drains queued work."""
    agent = Agent(name="worker", model=TestModel(custom_output_text="ok"))
    seen_a: list[Any] = []
    seen_b: list[Any] = []

    async with agent:
        assert agent.state == "idle"
        async with agent.subscribe() as stream_a, agent.subscribe() as stream_b:

            async def _collect(stream: AsyncIterator[Any], target: list[Any]) -> None:
                async for ev in stream:
                    target.append(ev)
                    if sum(isinstance(e, StreamCompleteEvent) for e in target) == 2:
                        break

            task_a = asyncio.create_task(_collect(stream_a, seen_a))
            task_b = asyncio.create_task(_collect(stream_b, seen_b))

            msg_id_1 = await agent.submit("first")
            assert msg_id_1
            assert agent.is_running
            assert agent.state == "running"

            msg_id_2 = await agent.submit("second", delivery="queue")
            assert msg_id_2 != msg_id_1

            await agent.wait_idle()
            await asyncio.wait_for(asyncio.gather(task_a, task_b), timeout=5.0)
            assert agent.state == "idle"

    assert len(seen_a) == len(seen_b)
    assert sum(isinstance(e, RunStartedEvent) for e in seen_a) == 2
    assert sum(isinstance(e, StreamCompleteEvent) for e in seen_a) == 2


async def test_submit_steer_routes_into_active_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    """submit(delivery='steer') calls agent.steer() when a turn is already running."""
    agent = Agent(name="steerable", model=TestModel(custom_output_text="done"))
    started = asyncio.Event()
    release = asyncio.Event()
    steered: list[tuple[str, bool]] = []

    async def fake_stream_events(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        started.set()
        await release.wait()
        yield RunStartedEvent(session_id="s1", run_id="r1", agent_name="steerable")

    async def fake_steer(message: str, *, interrupt: bool = False) -> None:
        steered.append((message, interrupt))
        release.set()

    monkeypatch.setattr(agent, "_stream_events", fake_stream_events)
    monkeypatch.setattr(agent, "steer", fake_steer)

    async with agent:
        await agent.submit("initial")
        await asyncio.wait_for(started.wait(), timeout=5.0)
        await agent.submit("course correct", delivery="steer", interrupt=True)
        await agent.wait_idle()

    assert steered == [("course correct", True)]


async def test_idle_emit_event_and_error_surface_on_subscribe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Idle emit_event reaches subscribe() + background_event; submit errors emit RunErrorEvent."""
    agent = Agent(name="emitter", model=TestModel(custom_output_text="ok"))
    bg_events: list[Any] = []
    agent.background_event.connect(bg_events.append)

    async def boom(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        raise RuntimeError("boom!")
        yield

    monkeypatch.setattr(agent, "_stream_events", boom)

    collected: list[Any] = []
    async with agent, agent.subscribe() as stream:
        await agent.emit_event(NoticeEvent(title="Heads up", severity="warning"))
        await agent.submit("trigger failure")
        await agent.wait_idle()
        assert agent.state == "idle"

        async for ev in stream:
            collected.append(ev)
            if isinstance(ev, RunErrorEvent):
                break

    assert len(bg_events) == 1
    assert isinstance(bg_events[0].event, NoticeEvent)
    assert any(isinstance(e, NoticeEvent) and e.title == "Heads up" for e in collected)
    assert any(isinstance(e, RunErrorEvent) and "boom!" in e.message for e in collected)


async def test_subscribe_closes_cleanly_on_agent_exit() -> None:
    """Exiting the agent closes all active subscribe() iterators."""
    agent = Agent(name="closing", model=TestModel(custom_output_text="ok"))
    finished = asyncio.Event()

    async with agent, agent.subscribe() as stream:

        async def _drain() -> None:
            async for _ in stream:
                pass
            finished.set()

        reader = asyncio.create_task(_drain())
        await asyncio.sleep(0)
        # Trigger agent teardown while subscriber is listening
        await agent.__aexit__(None, None, None)
        await asyncio.wait_for(finished.wait(), timeout=5.0)
        await reader


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
    agent._adapter = _FakeAdapter()  # type: ignore[assignment]

    await agent.steer("use pytest")
    assert fake_session.steered == [("use pytest", "turn_42")]
    assert not agent.has_queued_prompts()

    await agent.steer("stop and switch", interrupt=True)
    assert fake_session.interrupted == ["turn_42"]
    assert agent.has_queued_prompts()
