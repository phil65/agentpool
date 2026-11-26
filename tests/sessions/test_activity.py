"""The pool's activity feed: session lifecycle and foreground state (ACP v2 style)."""

import asyncio
from typing import TYPE_CHECKING, Any

from fastmcp import Client
from fastmcp.experimental.events import EventsClientExtension, subscribe_events
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
import pytest

from agentpool import Agent, AgentContext, AgentPool
from agentpool.sessions.activity import ActivityFeed
from agentpool_config.pool_server import MCPPoolServerConfig
from agentpool_server.mcp_server.server import MCPServer


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from pydantic_ai import ModelMessage


def test_feed_reads_after_a_position_and_reports_gaps() -> None:
    feed = ActivityFeed(max_entries=3)
    for n in range(5):
        feed.state(f"s{n}", "a", "running")
    assert feed.position == 5
    assert [e.seq for e in feed.since(3).entries] == [4, 5]
    assert not feed.since(2).truncated  # Entries 3-5 are all kept
    assert feed.since(0).truncated  # Entries 1-2 were dropped
    assert not feed.since(5).truncated
    assert feed.since(5).entries == []


async def _stream_text(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
    yield "done"


def _states(feed: ActivityFeed, agent: str) -> list[dict[str, Any]]:
    return [e.data for e in feed.since(0).entries if e.kind == "state_update" and e.agent == agent]


async def test_a_turn_reports_running_then_idle_with_usage() -> None:
    async with AgentPool() as pool:
        await pool.add_agent(Agent("a", model=FunctionModel(stream_function=_stream_text)))
        await pool.get_agent("a").run("hi")
        states = _states(pool.activity, "a")
    assert [s["state"] for s in states] == ["running", "idle"]
    assert states[1]["stop_reason"] == "end_turn"
    assert set(states[1]["usage"]) == {"input_tokens", "output_tokens"}


async def test_a_failed_turn_reports_its_error() -> None:
    async def fail(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        raise RuntimeError("model down")
        yield "unreachable"

    async with AgentPool() as pool:
        await pool.add_agent(Agent("a", model=FunctionModel(stream_function=fail)))
        with pytest.raises(RuntimeError):
            await pool.get_agent("a").run("hi")
        idle = _states(pool.activity, "a")[-1]
    assert (idle["state"], idle["stop_reason"]) == ("idle", "error")
    assert "model down" in idle["error"]


async def test_waiting_for_the_user_requires_action() -> None:
    async def ask_user(ctx: AgentContext) -> str:
        """Ask the user something."""
        with ctx.agent.awaiting_user():
            await asyncio.sleep(0)
        return "yes"

    agent = Agent("a", model=TestModel(call_tools=["ask_user"]), tools=[ask_user])
    async with AgentPool() as pool:
        await pool.add_agent(agent)
        await agent.run("hi")
        states = [s["state"] for s in _states(pool.activity, "a")]
    assert states == ["running", "requires_action", "running", "idle"]


async def test_new_sessions_are_reported() -> None:
    async with AgentPool() as pool:
        parent = pool.sessions.create("a")
        child = pool.sessions.create("b", parent_id=parent.session_id)
        infos = [e for e in pool.activity.since(0).entries if e.kind == "info_update"]
    assert [(e.session_id, e.agent) for e in infos] == [
        (parent.session_id, "a"),
        (child.session_id, "b"),
    ]
    assert infos[1].data["parent_id"] == parent.session_id


async def _collect(
    client: Client[Any], event: str, arguments: dict[str, Any], into: list[Any]
) -> None:
    subscription = subscribe_events(
        client, event, arguments, delivery="poll", min_poll_interval=0.05
    )
    async for occurrence in subscription:  # As they arrive (the subscription never ends)
        into.append(occurrence.data)  # noqa: PERF401


async def test_serve_mcp_publishes_session_state() -> None:
    async with AgentPool() as pool:
        for name in ("a", "b"):
            await pool.add_agent(Agent(name, model=FunctionModel(stream_function=_stream_text)))
        server = MCPServer(pool, MCPPoolServerConfig(serve_events=True))
        received: list[dict[str, Any]] = []
        async with Client(server.fastmcp, extensions=[EventsClientExtension()]) as client:
            watch = asyncio.create_task(
                _collect(client, "session.state_update", {"agent": "a"}, received)
            )
            await asyncio.sleep(0.2)  # Subscribed: from now on
            await pool.get_agent("b").run("not watched")
            await pool.get_agent("a").run("watched")
            for _ in range(100):
                if len(received) >= 2:
                    break
                await asyncio.sleep(0.05)
            watch.cancel()
    assert [(d["agent"], d["state"]) for d in received] == [("a", "running"), ("a", "idle")]
    assert received[1]["stop_reason"] == "end_turn"


def test_compaction_steps_share_an_id() -> None:
    from agentpool.agents.events import CompactionEvent, NoticeEvent

    feed = ActivityFeed()
    feed.observe("s", "a", CompactionEvent(session_id="backend", phase="starting"))
    feed.observe("s", "a", CompactionEvent(session_id="backend", phase="completed", pre_tokens=9))
    feed.observe("s", "a", CompactionEvent(session_id="backend", phase="failed", error="boom"))
    feed.observe("s", "a", NoticeEvent(title="Rate limited", severity="warning"))
    compactions = [e.data for e in feed.since(0).entries if e.kind == "compaction_update"]
    assert [c["status"] for c in compactions] == ["in_progress", "completed", "failed"]
    assert compactions[0]["compaction_id"] == compactions[1]["compaction_id"]
    assert compactions[2]["compaction_id"] != compactions[1]["compaction_id"]  # Unstarted
    assert compactions[1]["pre_tokens"] == 9
    [notice] = [e.data for e in feed.since(0).entries if e.kind == "notice"]
    assert (notice["severity"], notice["title"]) == ("warning", "Rate limited")


async def test_a_turn_reports_usage() -> None:
    async with AgentPool() as pool:
        agent = Agent("a", model=FunctionModel(stream_function=_stream_text))
        await pool.add_agent(agent)
        await agent.run("hi")
        assert agent.model_name is not None
        agent._model_context_windows[agent.model_name] = 1000  # As a lookup would cache it
        await agent.run("again")
        usages = [e.data for e in pool.activity.since(0).entries if e.kind == "usage_update"]
    assert len(usages) == 2
    assert usages[0]["used"] is not None
    assert usages[0]["size"] is None  # Not looked up: no network request in a turn
    assert usages[1]["size"] == 1000


async def test_events_outside_a_turn_reach_the_feed() -> None:
    from agentpool.agents.events import NoticeEvent

    async with AgentPool() as pool:
        agent = Agent("a", model=FunctionModel(stream_function=_stream_text))
        await pool.add_agent(agent)
        await agent.run("hi")  # The agent has a session now
        await agent.emit_event(NoticeEvent(title="Background work done"))
        notices = [e for e in pool.activity.since(0).entries if e.kind == "notice"]
    assert [(n.session_id, n.data["title"]) for n in notices] == [
        (agent.session_id, "Background work done")
    ]


async def test_serve_mcp_lists_every_activity_event() -> None:
    from fastmcp.experimental.events import list_events

    async with AgentPool() as pool:
        server = MCPServer(pool, MCPPoolServerConfig(serve_events=True))
        async with Client(server.fastmcp, extensions=[EventsClientExtension()]) as client:
            names = sorted(event.name for event in await list_events(client))
    assert names == [
        "session.compaction_update",
        "session.info_update",
        "session.notice",
        "session.state_update",
        "session.usage_update",
    ]
