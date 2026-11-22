"""Loading a pool session resumes the Codex thread it runs on."""

from __future__ import annotations

from typing import Any

from agentpool import AgentPool
from agentpool.agents.codex_agent import CodexAgent
from agentpool.models.manifest import AgentsManifest
from agentpool_config.storage import MemoryStorageConfig, StorageConfig


class _RecordingClient:
    """Records the thread `thread_resume` is asked for, then fails like a missing thread."""

    def __init__(self) -> None:
        self.resumed: list[str] = []

    async def thread_resume(self, thread_id: str, *, mcp_servers: Any = None) -> Any:
        self.resumed.append(thread_id)
        msg = f"no rollout found for thread id {thread_id}"
        raise RuntimeError(msg)


def _pool() -> AgentPool[None]:
    return AgentPool(AgentsManifest(storage=StorageConfig(providers=[MemoryStorageConfig()])))


async def test_load_session_resumes_the_thread_of_a_pool_session() -> None:
    """ACP `session/load` passes the pool's session ID; Codex knows only its thread ID."""
    pool = _pool()
    await pool.storage.log_session(session_id="ses_pool", node_name="cx")
    await pool.storage.update_sdk_session_id("ses_pool", "019a-thread")
    agent = CodexAgent(name="cx", agent_pool=pool)
    client = _RecordingClient()
    agent._client = client  # type: ignore[assignment]  # ty:ignore[invalid-assignment]

    await agent.load_session("ses_pool")

    assert client.resumed == ["019a-thread"]


async def test_load_session_of_a_codex_thread_resumes_it_directly() -> None:
    """A thread listed from the Codex server has no pool record; its ID is the thread's."""
    agent = CodexAgent(name="cx", agent_pool=_pool())
    client = _RecordingClient()
    agent._client = client  # type: ignore[assignment]  # ty:ignore[invalid-assignment]

    await agent.load_session("019a-thread")

    assert client.resumed == ["019a-thread"]
