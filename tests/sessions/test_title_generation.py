"""Integration tests for conversation title generation."""

import os
from typing import TYPE_CHECKING
from unittest.mock import patch

import anyio
import pytest

from agentpool import AgentPool
from agentpool.messaging import ChatMessage
from agentpool.models.agents import NativeAgentConfig
from agentpool.models.manifest import AgentsManifest
from agentpool.storage.manager import StorageManager
from agentpool_config.storage import (
    FileStorageConfig,
    MemoryStorageConfig,
    SQLStorageConfig,
    StorageConfig,
)
from agentpool_storage.file_provider import FileProvider
from agentpool_storage.memory_provider import MemoryStorageProvider
from agentpool_storage.sql_provider import SQLModelProvider


if TYPE_CHECKING:
    from agentpool_storage.models import ConversationData


GENERATED_TITLE = "Test Conversation Title"


@pytest.fixture
def storage_config() -> StorageConfig:
    """Storage config with memory provider and title generation enabled."""
    return StorageConfig(
        providers=[MemoryStorageConfig()],
        title_generation_model="test",  # Will be overridden in tests
    )


@pytest.fixture
async def pool_with_storage(storage_config: StorageConfig):
    """Create agent pool with storage configured."""
    manifest = AgentsManifest(
        agents={
            "test_agent": NativeAgentConfig(
                name="test_agent",
                model="test",
                system_prompt="You are a test agent",
            ),
        },
        storage=storage_config,
    )
    pool = AgentPool(manifest=manifest)
    async with pool:
        yield pool


class TestStorageManagerTitleGeneration:
    """Tests for StorageManager title generation methods."""

    async def test_generate_title_from_prompt_stores_title(self) -> None:
        """Test that _generate_title_from_prompt generates and stores a title."""
        config = StorageConfig(providers=[MemoryStorageConfig()], title_generation_model="test")
        async with StorageManager(config) as manager:
            conv_id = "test_conv_123"
            # First create the conversation
            await manager.log_session(session_id=conv_id, node_name="test_agent")
            # Directly call the title generation method (bypasses PYTEST check)
            title = await manager._generate_title_from_prompt(
                conv_id, "What is the weather today?", None
            )

            # Title should be generated
            assert title is not None
            assert len(title) > 0
            # Title should be stored
            stored_title = await manager.get_session_title(conv_id)
            assert stored_title == title

    async def test_generate_title_disabled(self) -> None:
        """Test that title generation is skipped when model is None."""
        config = StorageConfig(providers=[MemoryStorageConfig()], title_generation_model=None)
        async with StorageManager(config) as manager:
            conv_id = "test_conv_456"
            # Create conversation
            await manager.log_session(session_id=conv_id, node_name="test_agent")
            # Direct call should return None when model is not configured
            title = await manager._generate_title_from_prompt(conv_id, "Hello", None)
            assert title is None

    async def test_generate_title_already_exists(self) -> None:
        """Test that existing title is returned without regenerating."""
        config = StorageConfig(providers=[MemoryStorageConfig()], title_generation_model="test")
        async with StorageManager(config) as manager:
            conv_id = "test_conv_789"
            # Create conversation
            await manager.log_session(session_id=conv_id, node_name="test_agent")
            # Set existing title
            existing_title = "Existing Title"
            await manager.update_session_title(conv_id, existing_title)
            # Direct call should return existing title without calling model
            title = await manager._generate_title_from_prompt(conv_id, "New message", None)
            assert title == existing_title

    async def test_update_and_get_title(self) -> None:
        """Test updating and retrieving conversation title."""
        config = StorageConfig(providers=[MemoryStorageConfig()])
        async with StorageManager(config) as manager:
            conv_id = "test_conv_update"
            await manager.log_session(session_id=conv_id, node_name="test_agent")
            # Initially no title
            title = await manager.get_session_title(conv_id)
            assert title is None
            # Update title
            await manager.update_session_title(conv_id, "My Title")
            # Verify title was stored
            title = await manager.get_session_title(conv_id)
            assert title == "My Title"

    async def test_generate_session_title_from_messages(self) -> None:
        """Test the generate_session_title method with messages."""
        config = StorageConfig(providers=[MemoryStorageConfig()], title_generation_model="test")
        async with StorageManager(config) as manager:
            conv_id = "msg_title_test"
            await manager.log_session(session_id=conv_id, node_name="test_agent")
            # Create test messages
            messages = [
                ChatMessage.user_prompt("What is Python?"),
                ChatMessage(content="Python is a programming language.", role="assistant"),
            ]
            # Generate title from messages
            title = await manager.generate_session_title(conv_id, messages)
            assert title is not None
            # Verify it was stored
            stored = await manager.get_session_title(conv_id)
            assert stored == title

    async def test_log_session_triggers_title_gen_without_pytest_env(self) -> None:
        """Test that log_session triggers title gen when not in pytest."""
        config = StorageConfig(providers=[MemoryStorageConfig()], title_generation_model="test")
        async with StorageManager(config) as manager:
            conv_id = "test_trigger_123"
            title_result: str | None = None

            def on_title(title: str) -> None:
                nonlocal title_result
                title_result = title

            # Temporarily remove PYTEST env var to test the trigger
            with patch.dict(os.environ, {}, clear=False):
                # Remove the pytest marker if it exists
                os.environ.pop("PYTEST_CURRENT_TEST", None)

                await manager.log_session(
                    session_id=conv_id,
                    node_name="test_agent",
                    initial_prompt="What is the weather?",
                    on_title_generated=on_title,
                )
                await anyio.sleep(0.3)

            # Title should have been generated
            stored_title = await manager.get_session_title(conv_id)
            assert stored_title is not None


class TestMemoryProviderTitleSupport:
    """Tests for MemoryStorageProvider title methods."""

    async def test_memory_provider_title_operations(self) -> None:
        """Test title update and get on memory provider."""
        config = MemoryStorageConfig()
        provider = MemoryStorageProvider(config)
        conv_id = "mem_conv_123"
        await provider.log_session(session_id=conv_id, node_name="test_agent")
        # Initially no title
        title = await provider.get_session_title(conv_id)
        assert title is None
        # Update title
        await provider.update_session_title(conv_id, "Memory Title")
        # Get title
        title = await provider.get_session_title(conv_id)
        assert title == "Memory Title"

    async def test_memory_provider_title_nonexistent_conv(self) -> None:
        """Test getting title for non-existent conversation."""
        config = MemoryStorageConfig()
        provider = MemoryStorageProvider(config)
        title = await provider.get_session_title("nonexistent")
        assert title is None


class TestSQLProviderTitleSupport:
    """Tests for SQLModelProvider title methods."""

    @pytest.fixture
    def sql_config(self, tmp_path) -> SQLStorageConfig:
        """Create SQL config with temp database."""
        db_path = tmp_path / "test_titles.db"
        return SQLStorageConfig(url=f"sqlite:///{db_path}")

    async def test_sql_provider_title_operations(self, sql_config) -> None:
        """Test title update and get on SQL provider."""
        async with SQLModelProvider(sql_config) as provider:
            conv_id = "sql_conv_123"
            await provider.log_session(session_id=conv_id, node_name="test_agent")
            # Initially no title
            title = await provider.get_session_title(conv_id)
            assert title is None
            # Update title
            await provider.update_session_title(conv_id, "SQL Title")
            # Get title
            title = await provider.get_session_title(conv_id)
            assert title == "SQL Title"

    async def test_sql_provider_title_nonexistent_conv(self, sql_config) -> None:
        """Test getting title for non-existent conversation."""
        async with SQLModelProvider(sql_config) as provider:
            title = await provider.get_session_title("nonexistent")
            assert title is None

    async def test_sql_provider_title_update_overwrites(self, sql_config) -> None:
        """Test that updating title overwrites previous value."""
        async with SQLModelProvider(sql_config) as provider:
            conv_id = "sql_conv_overwrite"
            await provider.log_session(session_id=conv_id, node_name="test_agent")
            await provider.update_session_title(conv_id, "First Title")
            await provider.update_session_title(conv_id, "Second Title")
            title = await provider.get_session_title(conv_id)
            assert title == "Second Title"


class TestFileProviderTitleSupport:
    """Tests for FileProvider title methods."""

    async def test_file_provider_title_operations(self, tmp_path) -> None:
        """Test title update and get on file provider."""
        storage_file = tmp_path / "storage.json"
        config = FileStorageConfig(path=str(storage_file))
        provider = FileProvider(config)
        conv_id = "file_conv_123"
        await provider.log_session(session_id=conv_id, node_name="test_agent")
        # Initially no title
        title = await provider.get_session_title(conv_id)
        assert title is None
        # Update title
        await provider.update_session_title(conv_id, "File Title")
        # Get title
        title = await provider.get_session_title(conv_id)
        assert title == "File Title"
        # Verify file was written
        assert storage_file.exists()


class TestConversationDataTitle:
    """Tests for ConversationData title field."""

    def test_conversation_data_has_title(self) -> None:
        """Test that ConversationData TypedDict includes title field."""
        # Create ConversationData with title
        data: ConversationData = {
            "id": "ses_test123",
            "agent": "test_agent",
            "title": "My Conversation",
            "start_time": "2024-01-01T00:00:00",
            "messages": [],
            "token_usage": None,
        }
        assert data["title"] == "My Conversation"

    def test_conversation_data_title_none(self) -> None:
        """Test that ConversationData can have None title."""
        data: ConversationData = {
            "id": "ses_test456",
            "agent": "test_agent",
            "title": None,
            "start_time": "2024-01-01T00:00:00",
            "messages": [],
            "token_usage": None,
        }
        assert data["title"] is None
