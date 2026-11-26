"""Storage-level ``list_sessions``: one query in the SQL provider; errors surface."""

from datetime import timedelta
from typing import Any

import pytest

from agentpool import Agent
from agentpool.sessions.models import SessionData
from agentpool.storage.manager import StorageManager
from agentpool.utils.time_utils import get_now
from agentpool_config.storage import MemoryStorageConfig, SQLStorageConfig
from agentpool_storage.memory_provider.provider import MemoryStorageProvider
from agentpool_storage.sql_provider.sql_provider import SQLModelProvider


def _sessions() -> list[SessionData]:
    now = get_now()
    return [
        SessionData(
            session_id=f"s{i}",
            agent_name=agent,
            cwd=cwd,
            last_active=now + timedelta(minutes=i),
            metadata={"title": f"title {i}"},
        )
        for i, (agent, cwd) in enumerate([
            ("a", "/w1"),
            ("a", "/w2"),
            ("b", "/w1"),
            ("a", "/w1"),
            ("a", "/w1"),
        ])
    ]


async def test_sql_list_sessions_filters_in_one_query(monkeypatch: pytest.MonkeyPatch):
    async with SQLModelProvider(SQLStorageConfig(url="sqlite+aiosqlite:///:memory:")) as sql:
        for data in _sessions():
            await sql.save_session(data)

        async def no_per_session_load(*args: Any) -> None:
            raise AssertionError("list_sessions loaded a session by ID")

        monkeypatch.setattr(sql, "load_session", no_per_session_load)
        monkeypatch.setattr(sql, "get_session_title", no_per_session_load)
        found = await sql.list_sessions(agent_name="a", cwd="/w1", limit=2)
    assert [s.session_id for s in found] == ["s4", "s3"]  # Most recently active first
    assert [s.title for s in found] == ["title 4", "title 3"]


async def test_default_list_sessions_filters_and_limits():
    memory = MemoryStorageProvider(MemoryStorageConfig())
    for data in _sessions():
        await memory.save_session(data)
    found = await memory.list_sessions(agent_name="a", cwd="/w1")
    assert {s.session_id for s in found} == {"s0", "s3", "s4"}
    assert len(await memory.list_sessions(agent_name="a", limit=2)) == 2


async def test_native_list_sessions_raises_storage_errors(monkeypatch: pytest.MonkeyPatch):
    async def broken(self: StorageManager, **kwargs: Any) -> list[SessionData]:
        raise RuntimeError("database is locked")

    monkeypatch.setattr(StorageManager, "list_sessions", broken)
    async with Agent[None](model="test", name="a") as agent:
        if agent.storage is None:
            pytest.skip("agent without storage")
        with pytest.raises(RuntimeError, match="database is locked"):
            await agent.list_sessions()
