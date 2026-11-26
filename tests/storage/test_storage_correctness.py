"""Storage provider correctness: counts, resets, parent ids, ancestry, lookups (#56, #62)."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import sqlite3
from typing import TYPE_CHECKING, Any, Self

import anyenv
import pytest

from agentpool.messaging import ChatMessage
from agentpool.storage.manager import StorageManager
from agentpool.utils.time_utils import get_now
from agentpool_config.session import SessionQuery
from agentpool_config.storage import (
    ClaudeStorageConfig,
    FileStorageConfig,
    MemoryStorageConfig,
    SQLStorageConfig,
    StorageConfig,
    T3StorageConfig,
    ZedStorageConfig,
)
from agentpool_storage.claude_provider.provider import ClaudeStorageProvider
from agentpool_storage.file_provider.provider import FileProvider
from agentpool_storage.memory_provider.provider import MemoryStorageProvider
from agentpool_storage.sql_provider import SQLModelProvider
from agentpool_storage.sql_provider.models import Message
from agentpool_storage.zed_provider import provider as zed_module
from agentpool_storage.zed_provider.provider import ZedStorageProvider


if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from agentpool_storage.base import StorageProvider


def _msg(
    content: str, session_id: str, msg_id: str, parent: str | None, offset: int
) -> ChatMessage:
    return ChatMessage[str](
        content=content,
        role="user",
        name="agent",
        session_id=session_id,
        message_id=msg_id,
        parent_id=parent,
        timestamp=get_now() + timedelta(seconds=offset),
    )


async def _fill(provider: StorageProvider) -> None:
    """Agent a: s1 (m1 -> m2 -> m3); agent b: s2 (m4). Plus a fork s3 of s1 at m2 (m5)."""
    await provider.log_session(session_id="s1", node_name="a")
    await provider.log_session(session_id="s2", node_name="b")
    await provider.log_message(message=_msg("one", "s1", "m1", None, 0))
    await provider.log_message(message=_msg("two", "s1", "m2", "m1", 1))
    await provider.log_message(message=_msg("three", "s1", "m3", "m2", 2))
    await provider.log_message(message=_msg("other", "s2", "m4", None, 3))
    await provider.log_session(session_id="s3", node_name="a")
    await provider.log_message(message=_msg("fork", "s3", "m5", "m2", 4))


@pytest.fixture
async def sql(tmp_path: Path) -> AsyncIterator[SQLModelProvider]:
    config = SQLStorageConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'history.db'}")
    async with SQLModelProvider(config) as provider:
        await _fill(provider)
        yield provider


@pytest.fixture
async def file_provider(tmp_path: Path) -> AsyncIterator[FileProvider]:
    async with FileProvider(FileStorageConfig(path=str(tmp_path / "store.json"))) as provider:
        await _fill(provider)
        yield provider


# --- SQL ------------------------------------------------------------------------------


async def test_sql_session_counts(sql: SQLModelProvider) -> None:
    assert await sql.get_session_counts() == (3, 5)
    assert await sql.get_session_counts(agent_name="a") == (2, 4)
    assert await sql.get_session_counts(agent_name="b") == (1, 1)


async def test_sql_soft_reset_by_agent(sql: SQLModelProvider) -> None:
    assert await sql.reset(agent_name="a") == (2, 4)
    assert await sql.get_session_counts() == (1, 1)
    assert await sql.reset() == (1, 1)
    assert await sql.get_session_counts() == (0, 0)


async def test_sql_messages_keep_parent_id_and_ancestors(sql: SQLModelProvider) -> None:
    messages = await sql.get_session_messages("s1")
    assert [m.parent_id for m in messages] == [None, "m1", "m2"]
    forked = await sql.get_session_messages("s3", include_ancestors=True)
    assert [m.content for m in forked] == ["one", "two", "fork"]


async def test_sql_ancestry_stops_at_a_cycle(sql: SQLModelProvider) -> None:
    from sqlalchemy import update
    from sqlalchemy.ext.asyncio import AsyncSession

    async with AsyncSession(sql.engine) as session:
        await session.execute(update(Message).where(Message.id == "m1").values(parent_id="m3"))  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]
        await session.commit()
    chain = await asyncio.wait_for(sql.get_message_ancestry("m3"), timeout=5)
    assert {m.message_id for m in chain} == {"m1", "m2", "m3"}


async def test_sql_compact_conversations(sql: SQLModelProvider) -> None:
    conversations = await sql.get_filtered_conversations(compact=True, include_tokens=True)
    by_id = {c["id"]: c for c in conversations}
    assert [m.content for m in by_id["s1"]["messages"]] == ["one", "three"]
    assert len(by_id["s2"]["messages"]) == 1


# --- File provider --------------------------------------------------------------------


async def test_file_reset_by_agent_removes_its_messages(file_provider: FileProvider) -> None:
    assert await file_provider.reset(agent_name="a") == (2, 4)
    assert await file_provider.get_session_counts() == (1, 1)
    assert [m["session_id"] for m in file_provider._data["messages"]] == ["s2"]


async def test_file_filter_messages_keeps_ids(file_provider: FileProvider) -> None:
    messages = await file_provider.filter_messages(SessionQuery(name="s1"))
    assert [m.message_id for m in messages] == ["m1", "m2", "m3"]
    assert [m.parent_id for m in messages] == [None, "m1", "m2"]


async def test_file_and_memory_ancestry_stop_at_a_cycle(file_provider: FileProvider) -> None:
    file_provider._data["messages"][0]["parent_id"] = "m3"
    chain = await asyncio.wait_for(file_provider.get_message_ancestry("m3"), timeout=5)
    assert len(chain) == 3
    memory = MemoryStorageProvider(MemoryStorageConfig())
    await memory.log_message(message=_msg("x", "s", "x1", "x2", 0))
    await memory.log_message(message=_msg("y", "s", "x2", "x1", 1))
    chain = await asyncio.wait_for(memory.get_message_ancestry("x2"), timeout=5)
    assert len(chain) == 2


# --- Claude ---------------------------------------------------------------------------


def _claude_entry(uuid: str, parent: str | None, session_id: str, text: str) -> dict[str, Any]:
    return {
        "type": "user",
        "uuid": uuid,
        "parentUuid": parent,
        "sessionId": session_id,
        "timestamp": "2026-01-01T00:00:00.000Z",
        "cwd": "/tmp/project",
        "version": "2.0.0",
        "isSidechain": False,
        "userType": "external",
        "message": {"role": "user", "content": text},
    }


async def test_claude_ancestors_of_a_fork_cross_sessions(tmp_path: Path) -> None:
    project = tmp_path / "projects" / "-tmp-project"
    project.mkdir(parents=True)
    sessions = {
        "source": [
            _claude_entry("u1", None, "source", "one"),
            _claude_entry("u2", "u1", "source", "two"),
        ],
        "fork": [_claude_entry("u3", "u2", "fork", "three")],
    }
    for sid, entries in sessions.items():
        lines = [anyenv.dump_json(e) for e in entries]
        (project / f"{sid}.jsonl").write_text("\n".join(lines) + "\n")
    provider = ClaudeStorageProvider(ClaudeStorageConfig(path=str(tmp_path)))
    messages = await provider.get_session_messages("fork", include_ancestors=True)
    assert [m.message_id for m in messages] == ["u1", "u2", "u3"]
    # Within one session the fast path still applies
    chain = await provider.get_message_ancestry("u2", session_id="source")
    assert [m.message_id for m in chain] == ["u1", "u2"]


# --- StorageManager -------------------------------------------------------------------


def test_default_provider_matches_the_config_type() -> None:
    memory = MemoryStorageProvider(MemoryStorageConfig())
    sql = SQLModelProvider(SQLStorageConfig(url="sqlite+aiosqlite:///:memory:"))
    manager = StorageManager(StorageConfig(default_provider="sql"), providers=[memory, sql])
    assert manager.get_history_provider() is sql
    assert manager.get_history_provider(preferred="memory") is memory
    assert manager.get_history_provider(preferred="SQLModelProvider") is sql


def test_project_provider_implements_projects(tmp_path: Path) -> None:
    claude = ClaudeStorageProvider(ClaudeStorageConfig(path=str(tmp_path)))
    memory = MemoryStorageProvider(MemoryStorageConfig())
    assert StorageManager(providers=[claude, memory]).get_project_provider() is memory


# --- Zed / T3 -------------------------------------------------------------------------


async def test_zed_connection_is_read_only_and_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "threads.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE threads (id TEXT, data_type TEXT, data BLOB)")
        conn.execute("INSERT INTO threads VALUES ('t1', 'json', x'00')")
    conn.close()
    opened: list[sqlite3.Connection] = []
    real_connect = sqlite3.connect

    def connect(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        connection = real_connect(*args, **kwargs)
        opened.append(connection)
        return connection

    def broken(*args: Any) -> bytes:
        raise ValueError("corrupt")

    monkeypatch.setattr(zed_module.sqlite3, "connect", connect)
    monkeypatch.setattr(zed_module.helpers, "decompress", broken)
    provider = ZedStorageProvider(ZedStorageConfig(path=str(db)))
    with pytest.raises(ValueError, match="corrupt"):
        await provider.get_session_counts()
    [connection] = opened
    with pytest.raises(sqlite3.ProgrammingError):  # closed
        connection.execute("SELECT 1")
    with provider._get_connection() as ro, pytest.raises(sqlite3.OperationalError):
        ro.execute("DELETE FROM threads")


async def test_t3_failed_enter_closes_the_client(monkeypatch: pytest.MonkeyPatch) -> None:
    from agentpool_storage.t3_provider import T3StorageProvider, provider as t3_module

    class FakeClient:
        closed = False

        async def __aenter__(self) -> Self:
            return self

        async def aclose(self) -> None:
            self.closed = True

    client = FakeClient()

    async def failing(*args: Any, **kwargs: Any) -> dict[str, str]:
        raise ConnectionError("down")

    monkeypatch.setattr(t3_module.threads, "connect", lambda **kwargs: client)
    monkeypatch.setattr(t3_module.threads, "project_folders", failing)
    provider = T3StorageProvider(T3StorageConfig(url="http://localhost:1"))
    with pytest.raises(ConnectionError):
        await provider.__aenter__()
    assert client.closed
    assert provider._client is None
