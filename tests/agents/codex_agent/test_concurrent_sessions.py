"""Sessions of one Codex agent: a thread each, on their own bridge route."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agentpool.agents.codex_agent import CodexAgent
from agentpool.sessions import Session


@dataclass
class _FakeThread:
    thread_id: str


@dataclass
class _FakeClient:
    """Starts threads, recording the MCP servers each was given."""

    servers: dict[str, dict[str, Any]] = field(default_factory=dict)

    async def thread_start(self, *, mcp_servers: dict[str, Any], **kwargs: Any) -> _FakeThread:
        thread = _FakeThread(f"thread-{len(self.servers) + 1}")
        self.servers[thread.thread_id] = mcp_servers
        return thread


async def test_each_session_gets_a_thread_on_its_own_route() -> None:
    agent = CodexAgent(name="cx", enable_logging=False)
    client = _FakeClient()
    agent._client = client  # type: ignore[assignment]  # ty:ignore[invalid-assignment]
    first, second = Session.create("cx"), Session.create("cx")
    async with agent._tool_bridge:
        for session in (first, second):
            with agent.use_session(session):
                await agent._start_thread()
        name = agent._tool_bridge.resolved_server_name
        urls = {thread: servers[name].url for thread, servers in client.servers.items()}
        assert urls == {
            first.sdk_session_id: agent._tool_bridge.url_for(first.session_id),
            second.sdk_session_id: agent._tool_bridge.url_for(second.session_id),
        }
    assert agent._sdk_session_id is None  # The default session has no thread yet
    assert agent.capabilities.concurrent_sessions


async def test_callbacks_get_the_context_of_their_threads_turn() -> None:
    agent = CodexAgent(name="cx", enable_logging=False)
    first, second = Session.create("cx"), Session.create("cx")
    agent._thread_routes = {"t1": first.session_id, "t2": second.session_id}
    a, b = agent.get_context(data="A"), agent.get_context(data="B")
    async with (
        agent._tool_bridge.set_run_context(a, route=first.session_id),
        agent._tool_bridge.set_run_context(b, route=second.session_id),
    ):
        assert agent._run_context("t1") is a
        assert agent._run_context("t2") is b
        assert agent._run_context(None) is None  # Two turns run: which one is unknown
    async with agent._tool_bridge.set_run_context(a, route=first.session_id):
        assert agent._run_context(None) is a  # Only one runs


async def test_settings_set_within_a_session_are_its_own() -> None:
    agent = CodexAgent(name="cx", model="gpt-a", sandbox="read-only", enable_logging=False)
    session = Session.create("cx")
    with agent.use_session(session):
        await agent.set_mode("danger-full-access", category_id="sandbox")
        await agent.set_model("gpt-b")
        assert (agent._current_sandbox, agent.model_name) == ("danger-full-access", "gpt-b")
    assert (agent._current_sandbox, agent.model_name) == ("read-only", "gpt-a")
    assert session.overrides.model == "gpt-b"


async def test_the_bridges_tools_run_without_codex_asking() -> None:
    """Codex would ask for every tool not annotated read-only (and reject them under never)."""
    agent = CodexAgent(name="cx", approval_policy="never", enable_logging=False)
    client = _FakeClient()
    agent._client = client  # type: ignore[assignment]  # ty:ignore[invalid-assignment]
    async with agent._tool_bridge:
        await agent._start_thread()
        [servers] = client.servers.values()
        bridge = servers[agent._tool_bridge.resolved_server_name]
    assert bridge.default_tools_approval_mode == "approve"
