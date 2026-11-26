"""Tests for session data models and storage provider session CRUD."""

from typing import TYPE_CHECKING

import pytest

from agentpool.sessions import SessionData
from agentpool_config.storage import MemoryStorageConfig, SQLStorageConfig
from agentpool_storage.memory_provider import MemoryStorageProvider
from agentpool_storage.sql_provider import SQLModelProvider


if TYPE_CHECKING:
    from pathlib import Path


class TestSessionData:
    """Tests for SessionData model."""

    def test_session_data_creation(self) -> None:
        """Test basic session data creation."""
        data = SessionData(
            session_id="test_session",
            agent_name="test_agent",
        )
        assert data.session_id == "test_session"
        assert data.agent_name == "test_agent"
        assert data.pool_id is None
        assert data.cwd is None
        assert data.metadata == {}

    def test_session_data_with_metadata(self) -> None:
        """Test session data with metadata."""
        data = SessionData(
            session_id="test_session",
            agent_name="test_agent",
            metadata={"protocol": "acp", "version": "1.0"},
        )
        assert data.metadata["protocol"] == "acp"
        assert data.metadata["version"] == "1.0"

    def test_with_agent(self) -> None:
        """Test creating a copy with different agent."""
        original = SessionData(
            session_id="test_session",
            agent_name="agent1",
        )
        updated = original.with_agent("agent2")

        assert updated.agent_name == "agent2"
        assert updated.session_id == original.session_id
        # Original should be unchanged
        assert original.agent_name == "agent1"

    def test_with_metadata(self) -> None:
        """Test creating a copy with updated metadata."""
        original = SessionData(
            session_id="test_session",
            agent_name="test_agent",
            metadata={"key1": "value1"},
        )
        updated = original.with_metadata(key2="value2")

        assert updated.metadata["key1"] == "value1"
        assert updated.metadata["key2"] == "value2"
        # Original should be unchanged
        assert "key2" not in original.metadata


class TestMemoryProviderSessions:
    """Tests for session CRUD on MemoryStorageProvider."""

    @pytest.fixture
    def provider(self) -> MemoryStorageProvider:
        """Create a memory storage provider."""
        return MemoryStorageProvider(MemoryStorageConfig())

    async def test_save_and_load(self, provider: MemoryStorageProvider) -> None:
        """Test saving and loading a session."""
        data = SessionData(
            session_id="test_session",
            agent_name="test_agent",
        )

        async with provider:
            await provider.save_session(data)
            loaded = await provider.load_session("test_session")

        assert loaded is not None
        assert loaded.session_id == data.session_id
        assert loaded.agent_name == data.agent_name

    async def test_load_nonexistent(self, provider: MemoryStorageProvider) -> None:
        """Test loading a nonexistent session returns None."""
        async with provider:
            loaded = await provider.load_session("nonexistent")

        assert loaded is None

    async def test_delete(self, provider: MemoryStorageProvider) -> None:
        """Test deleting a session."""
        data = SessionData(
            session_id="test_session",
            agent_name="test_agent",
        )

        async with provider:
            await provider.save_session(data)
            deleted = await provider.delete_session("test_session")
            assert deleted is True

            loaded = await provider.load_session("test_session")
            assert loaded is None

    async def test_delete_nonexistent(self, provider: MemoryStorageProvider) -> None:
        """Test deleting a nonexistent session returns False."""
        async with provider:
            deleted = await provider.delete_session("nonexistent")

        assert deleted is False

    async def test_list_session_ids(self, provider: MemoryStorageProvider) -> None:
        """Test listing sessions."""
        data1 = SessionData(
            session_id="session1",
            agent_name="agent1",
            pool_id="pool1",
        )
        data2 = SessionData(
            session_id="session2",
            agent_name="agent2",
            pool_id="pool1",
        )
        data3 = SessionData(
            session_id="session3",
            agent_name="agent1",
            pool_id="pool2",
        )

        async with provider:
            await provider.save_session(data1)
            await provider.save_session(data2)
            await provider.save_session(data3)

            # List all
            all_sessions = await provider.list_session_ids()
            expected_total = 3
            assert len(all_sessions) == expected_total

            # Filter by pool_id
            pool1_sessions = await provider.list_session_ids(pool_id="pool1")
            expected_pool1 = 2
            assert len(pool1_sessions) == expected_pool1
            assert "session1" in pool1_sessions
            assert "session2" in pool1_sessions

            # Filter by agent_name
            agent1_sessions = await provider.list_session_ids(agent_name="agent1")
            expected_agent1 = 2
            assert len(agent1_sessions) == expected_agent1
            assert "session1" in agent1_sessions
            assert "session3" in agent1_sessions

    async def test_update_existing(self, provider: MemoryStorageProvider) -> None:
        """Test updating an existing session."""
        original = SessionData(
            session_id="test_session",
            agent_name="agent1",
        )

        async with provider:
            await provider.save_session(original)

            updated = original.with_agent("agent2")
            await provider.save_session(updated)

            loaded = await provider.load_session("test_session")

        assert loaded is not None
        assert loaded.agent_name == "agent2"


class TestSQLProviderSessions:
    """Tests for session CRUD on SQLModelProvider."""

    @pytest.fixture
    def provider(self, tmp_path: Path) -> SQLModelProvider:
        """Create a SQL provider with temp database."""
        db_path = tmp_path / "test_sessions.db"
        config = SQLStorageConfig(url=f"sqlite:///{db_path}")
        return SQLModelProvider(config)

    async def test_save_and_load(self, provider: SQLModelProvider) -> None:
        """Test saving and loading a session."""
        data = SessionData(
            session_id="sql_test_session",
            agent_name="test_agent",
            cwd="/tmp/test",
            metadata={"protocol": "acp"},
        )

        async with provider:
            await provider.save_session(data)
            loaded = await provider.load_session("sql_test_session")

        assert loaded is not None
        assert loaded.session_id == data.session_id
        assert loaded.agent_name == data.agent_name
        assert loaded.cwd == data.cwd
        assert loaded.metadata["protocol"] == "acp"

    async def test_load_nonexistent(self, provider: SQLModelProvider) -> None:
        """Test loading a nonexistent session returns None."""
        async with provider:
            loaded = await provider.load_session("nonexistent")

        assert loaded is None

    async def test_delete(self, provider: SQLModelProvider) -> None:
        """Test deleting a session."""
        data = SessionData(
            session_id="delete_test",
            agent_name="test_agent",
        )

        async with provider:
            await provider.save_session(data)
            deleted = await provider.delete_session("delete_test")
            assert deleted is True

            loaded = await provider.load_session("delete_test")
            assert loaded is None

    async def test_update_existing(self, provider: SQLModelProvider) -> None:
        """Test updating an existing session (upsert)."""
        original = SessionData(
            session_id="update_test",
            agent_name="agent1",
        )

        async with provider:
            await provider.save_session(original)

            updated = original.with_agent("agent2")
            await provider.save_session(updated)

            loaded = await provider.load_session("update_test")

        assert loaded is not None
        assert loaded.agent_name == "agent2"

    async def test_list_session_ids(self, provider: SQLModelProvider) -> None:
        """Test listing sessions with filters."""
        data1 = SessionData(
            session_id="sql_session1",
            agent_name="agent1",
            pool_id="pool1",
        )
        data2 = SessionData(
            session_id="sql_session2",
            agent_name="agent2",
            pool_id="pool1",
        )

        async with provider:
            await provider.save_session(data1)
            await provider.save_session(data2)

            all_sessions = await provider.list_session_ids()
            expected_total = 2
            assert len(all_sessions) == expected_total

            pool1_sessions = await provider.list_session_ids(pool_id="pool1")
            assert len(pool1_sessions) == expected_total

            agent1_sessions = await provider.list_session_ids(agent_name="agent1")
            assert len(agent1_sessions) == 1
            assert "sql_session1" in agent1_sessions
