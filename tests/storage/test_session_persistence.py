"""Session persistence: save/load round trips and model message (de)serialization."""

from datetime import timedelta
from typing import TYPE_CHECKING

from pydantic_ai import ModelRequest, ModelRequestPart, ModelResponse, ModelResponsePart
from pydantic_ai.messages import BinaryContent, TextPart, UserPromptPart
import pytest

from agentpool.sessions import SessionData
from agentpool.storage.serialization import (
    deserialize_messages,
    deserialize_parts,
    serialize_messages,
    serialize_parts,
)
from agentpool_config.storage import MemoryStorageConfig, SQLStorageConfig
from agentpool_storage.memory_provider import MemoryStorageProvider
from agentpool_storage.sql_provider import SQLModelProvider


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from pydantic_ai import ModelMessage

    from agentpool_storage.base import StorageProvider


@pytest.fixture(params=["sql", "memory"])
async def provider(request: pytest.FixtureRequest) -> AsyncIterator[StorageProvider]:
    if request.param == "sql":
        config = SQLStorageConfig(url="sqlite+aiosqlite:///:memory:")
        async with SQLModelProvider(config) as sql:
            yield sql
    else:
        yield MemoryStorageProvider(MemoryStorageConfig())


async def test_load_returns_stored_title(provider: StorageProvider) -> None:
    await provider.save_session(SessionData(session_id="s", agent_name="a"))
    await provider.update_session_title("s", "My title")
    loaded = await provider.load_session("s")
    assert loaded is not None
    assert loaded.title == "My title"


async def test_load_save_round_trip_is_lossless(provider: StorageProvider) -> None:
    original = SessionData(session_id="s", agent_name="a", cwd="/p", sdk_session_id="sdk-1")
    await provider.save_session(original)
    await provider.update_session_title("s", "My title")
    loaded = await provider.load_session("s")
    assert loaded is not None
    await provider.save_session(loaded.with_agent("b"))
    reloaded = await provider.load_session("s")
    assert reloaded is not None
    assert reloaded.title == "My title"
    assert reloaded.agent_name == "b"
    assert reloaded.sdk_session_id == "sdk-1"
    assert reloaded.created_at == original.created_at


async def test_saving_fresh_data_keeps_creation_time_and_title(provider: StorageProvider) -> None:
    original = SessionData(session_id="s", agent_name="a")
    await provider.save_session(original)
    await provider.update_session_title("s", "My title")
    later = SessionData(
        session_id="s",
        agent_name="a",
        cwd="/new",
        created_at=original.created_at + timedelta(hours=1),
    )
    await provider.save_session(later)  # No title: must not clear the stored one
    loaded = await provider.load_session("s")
    assert loaded is not None
    assert loaded.title == "My title"
    assert loaded.cwd == "/new"
    assert loaded.created_at == original.created_at


async def test_title_is_not_duplicated_into_stored_metadata(provider: StorageProvider) -> None:
    data = SessionData(session_id="s", agent_name="a", metadata={"title": "T", "k": 1})
    await provider.save_session(data)
    await provider.update_session_title("s", "Renamed")
    loaded = await provider.load_session("s")
    assert loaded is not None
    assert loaded.metadata == {"title": "Renamed", "k": 1}


async def test_sql_save_keeps_conversation_totals() -> None:
    from sqlmodel.ext.asyncio.session import AsyncSession

    from agentpool_storage.sql_provider.models import Conversation

    config = SQLStorageConfig(url="sqlite+aiosqlite:///:memory:")
    async with SQLModelProvider(config) as provider:
        await provider.save_session(SessionData(session_id="s", agent_name="a"))
        async with AsyncSession(provider.engine) as session:
            convo = await session.get(Conversation, "s")
            assert convo is not None
            convo.total_tokens = 1234
            convo.total_cost = 0.5
            convo.model = "m"
            session.add(convo)
            await session.commit()
        await provider.save_session(SessionData(session_id="s", agent_name="b"))
        async with AsyncSession(provider.engine) as session:
            convo = await session.get(Conversation, "s")
            assert convo is not None
            assert (convo.total_tokens, convo.total_cost, convo.model) == (1234, 0.5, "m")
            assert convo.agent_name == "b"


def test_model_messages_round_trip_as_objects() -> None:
    image = BinaryContent(data=b"\x00\x01", media_type="image/png")
    messages: list[ModelMessage] = [
        ModelRequest(parts=[UserPromptPart(content=["look", image])]),
        ModelResponse(parts=[TextPart(content="done")]),
    ]
    restored = deserialize_messages(serialize_messages(messages))
    assert isinstance(restored[0], ModelRequest)
    assert isinstance(restored[1], ModelResponse)
    prompt = restored[0].parts[0]
    assert isinstance(prompt, UserPromptPart)
    assert isinstance(prompt.content, list)
    text, restored_image = prompt.content
    assert text == "look"
    assert isinstance(restored_image, BinaryContent)
    assert (restored_image.data, restored_image.media_type) == (image.data, image.media_type)


def test_parts_round_trip_as_objects() -> None:
    parts: list[ModelRequestPart | ModelResponsePart] = [
        UserPromptPart(content="q"),
        TextPart(content="a"),
    ]
    restored = deserialize_parts(serialize_parts(parts))
    assert [type(p) for p in restored] == [UserPromptPart, TextPart]
