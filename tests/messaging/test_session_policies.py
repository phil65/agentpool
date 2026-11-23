"""Which session routed messages and triggered events run in (session policies)."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from pydantic_ai.models.test import TestModel
import pytest

from agentpool import Agent, AgentPool, Team
from agentpool.utils.tasks import TaskManager


if TYPE_CHECKING:
    from agentpool.sessions import Session


def _agent(name: str) -> Agent[None, str]:
    return Agent(name, model=TestModel(custom_output_text=f"from {name}"))


def _count(session: Session | None) -> int:
    assert session is not None
    return len(session.history.chat_messages)


async def _pool_with(a: Agent[None, str], b: Agent[None, str]) -> AgentPool:
    pool = AgentPool()
    await pool.__aenter__()
    await pool.add_agent(a)
    await pool.add_agent(b)
    return pool


async def test_target_policy_uses_the_targets_own_session() -> None:
    a, b = _agent("a"), _agent("b")
    pool = await _pool_with(a, b)
    try:
        a.connect_to(b)
        await a.run("hi")
        assert len(b.conversation.chat_messages) == 2
    finally:
        await pool.__aexit__(None, None, None)


async def test_per_source_session_follows_the_source_session() -> None:
    a, b = _agent("a"), _agent("b")
    pool = await _pool_with(a, b)
    try:
        talk = a.connect_to(b, name="ab", session="per_source_session")
        first, second = pool.sessions.create("a"), pool.sessions.create("a")
        await a.run("one", session=first)
        await a.run("two", session=second)
        await a.run("three", session=first)
        child = pool.sessions.get(f"{first.session_id}/{talk.name}/b")
        assert child is not None
        assert child.parent_id == first.session_id
        assert _count(child) == 4  # Both turns of the first session
        assert _count(pool.sessions.get(f"{second.session_id}/{talk.name}/b")) == 2
        assert len(b.conversation.chat_messages) == 0  # Not b's own session
    finally:
        await pool.__aexit__(None, None, None)


async def test_standing_session_keeps_one_conversation() -> None:
    a, b = _agent("a"), _agent("b")
    pool = await _pool_with(a, b)
    try:
        talk = a.connect_to(b, name="ab", session="standing")
        await a.run("one")
        await a.run("two", session=pool.sessions.create("a"))
        assert _count(pool.sessions.get(f"connection/{talk.name}/b")) == 4
    finally:
        await pool.__aexit__(None, None, None)


async def test_per_message_runs_each_message_in_a_new_session() -> None:
    a, b = _agent("a"), _agent("b")
    pool = await _pool_with(a, b)
    try:
        a.connect_to(b, session="per_message")
        await a.run("one")
        await a.run("two")
        sessions = [s for s in pool.sessions.list_sessions() if s.agent_name == "b"]
        assert [_count(s) for s in sessions] == [2, 2]
        assert {s.parent_id for s in sessions} == {a.session_id}
    finally:
        await pool.__aexit__(None, None, None)


async def test_standing_session_outside_a_pool() -> None:
    a, b = _agent("a"), _agent("b")
    talk = a.connect_to(b, session="standing")
    async with a, b:
        await a.run("one")
        await a.run("two")
        session = await b.router.sessions.open(b, f"connection/{talk.name}/b", parent_id=None)
    assert _count(session) == 4
    assert len(b.conversation.chat_messages) == 0


async def test_policies_other_than_target_need_an_agent() -> None:
    a = _agent("a")
    team = Team([_agent("x"), _agent("y")], name="crew")
    with pytest.raises(ValueError, match="needs an agent"):
        a.connect_to(team, session="standing")


async def test_delayed_run_connection_runs_in_the_background() -> None:
    a, b = _agent("a"), _agent("b")
    a.connect_to(b, delay=timedelta(milliseconds=50))
    async with a, b:
        await a.run("hi")
        assert len(b.conversation.chat_messages) == 0  # Not yet: a didn't wait
        await a.connections.wait_for_connections()
        assert len(b.conversation.chat_messages) == 2


async def test_delayed_task_runs_and_is_awaited() -> None:
    manager = TaskManager()
    done: list[str] = []

    async def work() -> None:
        done.append("ran")

    manager.create_task(work(), delay=timedelta(milliseconds=20))
    assert manager.is_busy()
    await asyncio.wait_for(manager.complete_tasks(), timeout=5)
    assert done == ["ran"]


@pytest.mark.usefixtures("one_shot_sources")
async def test_triggers_with_standing_and_per_event_sessions() -> None:
    from agentpool.models.manifest import AgentsManifest

    timer: dict[str, Any] = {"type": "time", "schedule": "0 9 * * *", "prompt": "tick"}
    manifest = AgentsManifest.model_validate({
        "agents": {
            "a": {
                "type": "native",
                "model": "test",
                "triggers": [
                    {**timer, "name": "kept", "session": "standing"},
                    {**timer, "name": "fresh", "session": "per_event"},
                ],
            }
        }
    })
    async with AgentPool(manifest) as pool:
        agent = pool.get_agent("a")
        for _ in range(200):
            sessions = [s for s in pool.sessions.list_sessions() if s.agent_name == "a"]
            if len(sessions) == 2 and all(_count(s) == 2 for s in sessions):
                break
            await asyncio.sleep(0.01)
        assert _count(pool.sessions.get("trigger/a/kept")) == 2
        assert len(sessions) == 2
        assert len(agent.conversation.chat_messages) == 0
