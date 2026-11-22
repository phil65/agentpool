"""Tests for first-class Session, SessionOverrides, SessionRegistry, and BaseAgent integration."""

from __future__ import annotations

import asyncio

from pydantic_ai.models.test import TestModel
import pytest

from agentpool import Agent, AgentPool
from agentpool.sessions import Session, SessionOverrides


async def test_agent_default_session_and_explicit_session_isolation() -> None:
    """Running with an explicit Session isolates history and session_id from the default session."""
    agent = Agent(name="coder", model=TestModel(custom_output_text="ok"))
    async with agent:
        default_msg = await agent.run("on default")
        assert agent.session_id is not None
        assert agent.session.session_id == agent.session_id
        assert len(agent.conversation.get_history()) == 2
        assert default_msg.session_id == agent.session_id

        child = agent.session.child("coder")
        assert child.parent_id == agent.session.session_id
        assert child.session_id != agent.session.session_id

        child_msg = await agent.run("on child", session=child)
        assert child_msg.session_id == child.session_id
        # Default session was untouched
        assert agent.session_id == default_msg.session_id
        assert len(agent.conversation.get_history()) == 2
        # Child session owns its own history
        assert len(child.history.get_history()) == 2
        assert child.history.get_history()[0].content == ["on child"]


async def test_session_fork_and_overrides_snapshot() -> None:
    """Session.fork() copies history up to cutoff and preserves overrides cleanly."""
    agent = Agent(name="writer", model=TestModel(custom_output_text="reply"))
    async with agent:
        await agent.run("turn 1")
        first_pair = agent.session.history.get_history()
        cutoff_id = first_pair[-1].message_id
        await agent.run("turn 2")
        assert len(agent.session.history.get_history()) == 4

        agent.session.add_instruction("Be concise")
        agent.session.set_env("FOO", "bar")
        snap = agent.session.snapshot()
        restored = Session.from_session_data(snap)
        assert restored.overrides == SessionOverrides(
            instructions=["Be concise"],
            env={"FOO": "bar"},
        )

        forked = agent.session.fork(at_message_id=cutoff_id)
        assert forked.parent_id == agent.session.session_id
        assert len(forked.history.get_history()) == 2
        assert all(m.session_id == forked.session_id for m in forked.history.get_history())
        assert forked.overrides.instructions == ["Be concise"]

        with pytest.raises(ValueError, match="not found"):
            agent.session.fork(at_message_id="missing_id")


async def test_parallel_explicit_sessions_on_single_agent() -> None:
    """Two explicit Sessions can run concurrently on the same NativeAgent without interleaving."""
    agent = Agent(name="shared", model=TestModel(custom_output_text="done"))
    async with AgentPool() as pool:
        pool.register("shared", agent)
        s1 = pool.sessions.create("shared")
        s2 = pool.sessions.create("shared")
        assert len(pool.sessions) == 2

        r1, r2 = await asyncio.gather(
            agent.run("alpha", session=s1),
            agent.run("beta", session=s2),
        )
        assert r1.session_id == s1.session_id
        assert r2.session_id == s2.session_id
        assert [m.content for m in s1.history.get_history()] == [["alpha"], "done"]
        assert [m.content for m in s2.history.get_history()] == [["beta"], "done"]
        assert agent.conversation.get_history() == []

        child = pool.sessions.fork(s1.session_id)
        assert pool.sessions.children_of(s1.session_id) == [child]
