"""Tests for first-class Session, SessionOverrides, SessionRegistry, and BaseAgent integration."""

import asyncio
from typing import Any

from pydantic_ai.models.test import TestModel
import pytest

from agentpool import Agent, AgentPool
from agentpool.sessions import Session, SessionOverrides
from agentpool.tools.exceptions import ToolError


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

        agent.session.overrides = SessionOverrides(model="test")
        snap = agent.session.snapshot()
        restored = Session.from_session_data(snap)
        assert restored.overrides == SessionOverrides(model="test")

        forked = agent.session.fork(at_message_id=cutoff_id)
        assert forked.parent_id == agent.session.session_id
        assert len(forked.history.get_history()) == 2
        assert all(m.session_id == forked.session_id for m in forked.history.get_history())
        assert forked.overrides.model == "test"

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


async def test_subagent_task_uses_isolated_forked_and_resumed_child_sessions() -> None:
    """SubagentTools.task() creates isolated/forked child sessions and resumes by session_id."""
    from agentpool_toolsets.builtin.subagent_tools import SubagentTools

    async with AgentPool() as pool:
        helper = Agent(name="helper", model=TestModel(custom_output_text="helped"))
        lead = Agent(name="lead", model=TestModel(custom_output_text="ok"))
        await pool.add_agent(helper)
        await pool.add_agent(lead)
        await lead.run("parent context")

        tools = SubagentTools()
        ctx = lead.get_context()

        # 1. Default isolated child session does not pollute helper's default conversation
        res1 = await tools.task(ctx, "helper", "do part 1", "Part 1")
        assert helper.conversation.get_history() == []
        children = pool.sessions.children_of(lead.session.session_id)
        assert len(children) == 1
        assert len(children[0].history.get_history()) == 2
        # The result names the child session, so the model can continue it
        assert (
            res1
            == f"helped\n\nSession ID: {children[0].session_id} (pass it as session_id to continue)"
        )

        # 2. Forked child session starts with a copy of parent's history
        await tools.task(ctx, "helper", "use parent context", "Forked", context="fork")
        children = pool.sessions.children_of(lead.session.session_id)
        assert len(children) == 2
        forked = children[1]
        assert len(forked.history.get_history()) == 4  # 2 from parent + 2 from child turn

        # 3. Continuing an existing child session appends to that session
        await tools.task(ctx, "helper", "follow up", "Continue", session_id=children[0].session_id)
        assert len(children[0].history.get_history()) == 4
        assert len(pool.sessions.children_of(lead.session.session_id)) == 2

        # 4. Only this conversation's child sessions of that agent can be continued
        stranger = pool.sessions.register(Session.create("helper"))
        for foreign in ("ses_unknown", stranger.session_id):
            with pytest.raises(ToolError, match="in this conversation"):
                await tools.task(ctx, "helper", "x", "Foreign", session_id=foreign)
        with pytest.raises(ToolError, match="in this conversation"):
            await tools.task(ctx, "lead", "x", "Other agent", session_id=children[0].session_id)


async def test_workers_tools_delegate_via_child_session_without_mutating_worker() -> None:
    """WorkersTools runs worker turns in child sessions without swapping worker.conversation."""
    from agentpool_config.workers import AgentWorkerConfig
    from agentpool_toolsets.builtin.workers import WorkersTools

    async with AgentPool() as pool:
        worker = Agent(name="worker", model=TestModel(custom_output_text="worked"))
        lead = Agent(name="lead", model=TestModel(custom_output_text="ok"))
        await pool.add_agent(worker)
        await pool.add_agent(lead)
        await worker.run("worker own memory")
        await lead.run("lead context")

        from agentpool.tools.base import FunctionTool

        provider = WorkersTools([AgentWorkerConfig(name="worker", pass_message_history=True)])
        (tool,) = await provider.get_tools()
        assert isinstance(tool, FunctionTool)
        result = await tool.fn(lead.get_context(), "help me")

        assert result == "worked"
        # Worker's own default session history was never touched or swapped
        assert len(worker.conversation.get_history()) == 2
        assert worker.conversation.get_history()[0].content == ["worker own memory"]
        # The child session is not kept: nothing can continue a worker's session
        assert pool.sessions.children_of(lead.session.session_id) == []


async def test_agent_capabilities_and_base_fork_and_compact() -> None:
    """Agents advertise AgentCapabilities and support first-class fork_session() / compact()."""
    from agentpool.agents import (
        ACPAgent,
        AgentCapabilities,
        AntigravityAgent,
        ClaudeCodeAgent,
        CodexAgent,
        OpenCodeV2Agent,
    )
    from agentpool.messaging.compaction import CompactionPipeline, KeepLastMessages

    expected_native = AgentCapabilities(
        structured_output=True,
        multimodal_input=True,
        live_model_discovery=True,
        native_compaction=True,
        native_fork=True,
        concurrent_sessions=True,
        session_instructions=True,
        native_steer=True,
    )
    assert expected_native == Agent.CAPABILITIES
    assert ClaudeCodeAgent.CAPABILITIES.native_steer
    assert ClaudeCodeAgent.CAPABILITIES.background_events
    assert CodexAgent.CAPABILITIES.native_steer
    assert CodexAgent.CAPABILITIES.native_fork
    assert OpenCodeV2Agent.CAPABILITIES.native_steer
    assert OpenCodeV2Agent.CAPABILITIES.native_compaction
    assert OpenCodeV2Agent.CAPABILITIES.native_fork
    assert OpenCodeV2Agent.CAPABILITIES.background_events
    assert AntigravityAgent.CAPABILITIES.structured_output
    assert ACPAgent.CAPABILITIES.background_events

    async with AgentPool() as pool:
        agent = Agent(name="compactor", model=TestModel(custom_output_text="ok"))
        await pool.add_agent(agent)
        assert agent.capabilities is Agent.CAPABILITIES

        await agent.run("turn 1")
        await agent.run("turn 2")
        await agent.run("turn 3")
        assert len(agent.conversation.get_history()) == 6

        forked = await agent.fork_session()
        assert forked.parent_id == agent.session.session_id
        assert forked.session_id in pool.sessions
        assert len(forked.history.get_history()) == 6

        before, after = await agent.compact(
            CompactionPipeline([KeepLastMessages(count=2)]),
            session=forked,
        )
        assert (before, after) == (6, 4)
        assert len(forked.history.get_history()) == 2
        # Original session untouched
        assert len(agent.conversation.get_history()) == 6


async def test_codex_and_opencode_v2_native_compact_and_fork() -> None:
    """CodexAgent and OpenCodeV2Agent delegate compact() and fork_session() natively."""
    from types import SimpleNamespace

    from agentpool.agents import CodexAgent, OpenCodeV2Agent

    # Codex native compact & fork
    class _FakeCodexSession:
        def __init__(self, thread_id: str) -> None:
            self.thread_id = thread_id
            self.compacted = False

        async def compact_start(self) -> None:
            self.compacted = True

        async def fork(self, *, mcp_servers: Any = None) -> _FakeCodexSession:
            return _FakeCodexSession("thread_forked")

    codex = CodexAgent(name="codex-ops", enable_logging=False)
    sdk_sess = _FakeCodexSession("thread_1")
    codex._client = object()  # type: ignore[assignment]
    codex._sdk_session_id = "thread_1"
    codex._sessions["thread_1"] = sdk_sess  # type: ignore[assignment]

    await codex.compact()
    assert sdk_sess.compacted is True
    codex_fork = await codex.fork_session()
    assert codex_fork.sdk_session_id == "thread_forked"
    assert "thread_forked" in codex._sessions

    # OpenCode V2 native compact & fork
    class _FakeV2Client:
        def __init__(self) -> None:
            self.compacted: list[str] = []
            self.forked: list[tuple[str, str | None]] = []

        async def compact_session(self, session_id: str) -> None:
            self.compacted.append(session_id)

        async def fork_session(self, session_id: str, *, before: str | None = None) -> Any:
            self.forked.append((session_id, before))
            return SimpleNamespace(id="ses_v2_fork")

    v2_client = _FakeV2Client()
    oc = OpenCodeV2Agent(
        name="oc-ops",
        client=v2_client,  # type: ignore[arg-type]
        enable_logging=False,
    )
    oc._sdk_session_id = "ses_v2_1"
    await oc.compact()
    assert v2_client.compacted == ["ses_v2_1"]
    oc_fork = await oc.fork_session()
    assert v2_client.forked == [("ses_v2_1", None)]
    assert oc_fork.sdk_session_id == "ses_v2_fork"


async def test_active_session_context_and_per_session_model_override() -> None:
    """NativeAgent uses session.overrides.model without mutating self and tracks active_session."""
    from agentpool.agents.events import StepStartedEvent

    agent = Agent(name="multi-model", model=TestModel(custom_output_text="ok"))
    async with agent:
        default_model = agent.model_name
        s1 = Session.create("multi-model", storage=agent.storage)
        s1.overrides = SessionOverrides(model="test")

        events = [e async for e in agent.run_stream("hi", session=s1)]
        step_starts = [e for e in events if isinstance(e, StepStartedEvent)]
        assert step_starts
        assert step_starts[0].model_name == "test"
        # Shared agent's configured model and default session remain untouched
        assert agent.model_name == default_model
        assert agent.active_session is agent.session


def test_registry_drops_least_recently_used_and_children_go_with_parent() -> None:
    from agentpool.sessions import SessionRegistry

    registry = SessionRegistry(max_sessions=2)
    a, b = registry.create("x"), registry.create("x")
    assert registry.get(a.session_id) is a  # a is now used more recently than b
    c = registry.create("x")
    assert [s.session_id for s in registry.list_sessions()] == [a.session_id, c.session_id]
    child = registry.register(c.child("y"))  # drops a, the least recently used
    assert [s.session_id for s in registry.list_sessions()] == [c.session_id, child.session_id]
    assert b.session_id not in registry
    registry.remove(c.session_id)
    assert len(registry) == 0


async def test_agents_compact_and_fork_only_a_backend_session_that_exists() -> None:
    """Before a backend session exists there is nothing to compact or fork: they say so."""
    from agentpool.agents import ClaudeCodeAgent, CodexAgent, OpenCodeV2Agent
    from agentpool.agents.exceptions import UnsupportedOperationError

    # Native compaction and fork, but only of a backend session that exists, and whole
    for agent in (
        ClaudeCodeAgent(name="claude"),
        CodexAgent(name="codex"),
        OpenCodeV2Agent(name="opencode"),
    ):
        assert agent.capabilities.native_compaction
        assert agent.capabilities.native_fork
        with pytest.raises(UnsupportedOperationError):
            await agent.compact()  # Not connected: no backend session yet
        with pytest.raises(UnsupportedOperationError):
            await agent.fork_session(at_message_id="msg-1")


async def test_backend_session_id_is_kept_per_session() -> None:
    """Within a session, an agent's backend session (thread) is that session's."""
    from agentpool.agents.t3_agent import T3Agent

    agent = T3Agent(project="proj_1", url="http://t3.test", token="t", enable_logging=False)
    first, second = Session.create("t3"), Session.create("t3")
    with agent.use_session(first):
        agent._sdk_session_id = "thread-a"
    with agent.use_session(second):
        assert agent._sdk_session_id is None
        agent._sdk_session_id = "thread-b"
    assert (first.sdk_session_id, second.sdk_session_id) == ("thread-a", "thread-b")
    assert agent._sdk_session_id is None  # The agent's own session has none
    assert agent.capabilities.concurrent_sessions


async def test_registry_open_restores_a_stored_session_with_its_backend_session() -> None:
    from agentpool import AgentsManifest
    from agentpool_config.storage import SQLStorageConfig, StorageConfig

    storage = StorageConfig(providers=[SQLStorageConfig(url="sqlite+aiosqlite:///:memory:")])
    async with AgentPool(AgentsManifest(storage=storage)) as pool:
        agent = Agent(name="a", model=TestModel(custom_output_text="ok"))
        await pool.add_agent(agent)
        session = await pool.sessions.open("ses_x", "a")
        await agent.run("hi", session=session)
        await pool.storage.update_sdk_session_id("ses_x", "backend-1")
        pool.sessions.remove("ses_x")  # E.g. the server restarted
        restored = await pool.sessions.open("ses_x", "a")
        assert restored is not session
        assert restored.sdk_session_id == "backend-1"
        # Storage keeps the turn's answer, with the prompt inside it (design section 10.3)
        assert len(restored.history.get_history()) == 1
        assert await pool.sessions.open("ses_x", "a") is restored  # Now registered
