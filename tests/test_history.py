from datetime import datetime, timedelta
from decimal import Decimal

from pydantic_ai import RunUsage
import pytest

from agentpool.messaging import ChatMessage, TokenCost
from agentpool.utils.time_utils import get_now, parse_time_period
from agentpool_config.storage import SQLStorageConfig
from agentpool_storage.models import QueryFilters, StatsFilters
from agentpool_storage.sql_provider import SQLModelProvider


# Reference time for all tests
BASE_TIME = datetime(2024, 1, 1, 12, 0)  # noon on Jan 1, 2024

# Test configuration
test_config = SQLStorageConfig(url="sqlite+aiosqlite:///:memory:")


@pytest.fixture
async def provider():
    """Create SQLModelProvider instance."""
    async with SQLModelProvider(test_config) as p:
        yield p


@pytest.fixture(autouse=True)
async def cleanup_database(provider: SQLModelProvider):
    """Clean up the database before each test."""
    # Clean database using provider's reset method
    await provider.reset(hard=True)


@pytest.fixture
async def sample_data(provider: SQLModelProvider):
    """Create sample conversation data."""
    # Create two conversations using provider methods
    start = BASE_TIME - timedelta(hours=1)  # 11:00
    await provider.log_session(session_id="conv1", node_name="test_agent", start_time=start)
    start = BASE_TIME - timedelta(hours=2)  # 10:00
    await provider.log_session(session_id="conv2", node_name="other_agent", start_time=start)

    # Add messages as ChatMessage objects
    messages = [
        ChatMessage(
            content="Hello",
            role="user",
            name="user",
            model_name="gpt-5",
            session_id="conv1",
            usage=RunUsage(input_tokens=5, output_tokens=5),
            cost_info=TokenCost(total_cost=Decimal("0.001")),
        ),
        ChatMessage(
            content="Hi there!",
            role="assistant",
            name="test_agent",
            model_name="gpt-5",
            session_id="conv1",
            usage=RunUsage(input_tokens=10, output_tokens=10),
            cost_info=TokenCost(total_cost=Decimal("0.002")),
        ),
        ChatMessage(
            content="Testing",
            role="user",
            name="user",
            model_name="gpt-3.5-turbo",
            session_id="conv2",
            usage=RunUsage(input_tokens=7, output_tokens=8),
            cost_info=TokenCost(total_cost=Decimal("0.0015")),
        ),
    ]

    for msg in messages:
        await provider.log_message(message=msg)


def test_parse_time_period():
    """Test time period parsing."""
    assert parse_time_period("1h") == timedelta(hours=1)
    assert parse_time_period("2d") == timedelta(days=2)
    assert parse_time_period("1w") == timedelta(weeks=1)


async def test_get_sessions(provider: SQLModelProvider, sample_data: None):
    """Test conversation retrieval with filters."""
    # Get all conversations
    filters = QueryFilters()
    results = await provider.get_sessions(filters)
    assert len(results) == 2

    # Filter by agent
    filters = QueryFilters(agent_name="test_agent")
    results = await provider.get_sessions(filters)
    assert len(results) == 1
    conv = results[0]
    assert conv["agent"] == "test_agent"
    assert len(conv["messages"]) == 2

    # Filter by time
    filters = QueryFilters(since=BASE_TIME - timedelta(hours=1.5))
    results = await provider.get_sessions(filters)
    assert len(results) == 1


async def test_get_session_stats(provider: SQLModelProvider, sample_data: None):
    """Test statistics retrieval and aggregation."""
    cutoff = BASE_TIME - timedelta(hours=3)
    filters = StatsFilters(cutoff=cutoff, group_by="model")
    stats = await provider.get_session_stats(filters)

    # Check model grouping
    assert "gpt-5" in stats
    assert stats["gpt-5"]["messages"] == 2
    assert stats["gpt-5"]["usage"].total_tokens == 30
    assert "gpt-3.5-turbo" in stats
    assert stats["gpt-3.5-turbo"]["messages"] == 1


async def test_complex_filtering(provider: SQLModelProvider, sample_data: None):
    """Test combined filtering capabilities."""
    since = BASE_TIME - timedelta(hours=1.5)
    filters = QueryFilters(agent_name="test_agent", model="gpt-5", since=since, query="Hello")
    results = await provider.get_sessions(filters)
    assert len(results) == 1
    conv = results[0]
    assert conv["agent"] == "test_agent"
    assert any(msg.content == "Hello" for msg in conv["messages"])
    assert all(msg.model_name == "gpt-5" for msg in conv["messages"])


async def test_basic_filters(provider: SQLModelProvider, sample_data: None):
    """Test basic filtering by agent and model."""
    # Get all conversations
    filters = QueryFilters()
    results = await provider.get_sessions(filters)
    assert len(results) == 2

    # Filter by agent
    filters = QueryFilters(agent_name="test_agent")
    results = await provider.get_sessions(filters)
    assert len(results) == 1
    conv = results[0]
    assert conv["agent"] == "test_agent"
    assert len(conv["messages"]) == 2

    # Filter by model
    filters = QueryFilters(model="gpt-5")
    results = await provider.get_sessions(filters)
    assert len(results) == 1
    conv = results[0]
    assert all(msg.model_name == "gpt-5" for msg in conv["messages"])


async def test_time_filters(provider: SQLModelProvider, sample_data: None):
    """Test time-based filtering."""
    # First find conversation start times using provider method
    filters = QueryFilters()
    conversations = await provider.get_sessions(filters)
    latest_conv_time = max(
        datetime.fromisoformat(conv_data["start_time"]) for conv_data in conversations
    )

    # Get all conversations (no time filter)
    filters = QueryFilters()
    results = await provider.get_sessions(filters)
    assert len(results) == 2  # All conversations

    # Filter with cutoff after latest conversation - should get nothing
    filters = QueryFilters(since=latest_conv_time + timedelta(seconds=1))
    results = await provider.get_sessions(filters)
    assert len(results) == 0  # No conversations after cutoff

    # Filter with cutoff before latest conversation - should get conversations
    filters = QueryFilters(since=latest_conv_time - timedelta(hours=1))
    results = await provider.get_sessions(filters)
    assert len(results) > 0


async def test_filtered_conversations(provider: SQLModelProvider, sample_data: None):
    """Test high-level filtered conversation helper."""
    # Filter by agent (should get all messages for test_agent)
    results = await provider.get_filtered_conversations(
        agent_name="test_agent", include_tokens=True
    )
    assert len(results) == 1
    conv = results[0]
    assert conv["agent"] == "test_agent"
    assert len(conv["messages"]) == 2
    assert conv["token_usage"] is not None
    assert conv["token_usage"].total_tokens == 30  # 10 + 20 tokens


async def test_period_filtering(provider: SQLModelProvider, sample_data: None):
    """Test just the period filtering to isolate the issue."""
    # Test direct period parsing
    period = "2h"
    since = get_now() - parse_time_period(period)
    print(f"\nPeriod '2h' parsed to: {since}")

    # Test with QueryFilters
    filters = QueryFilters(since=since)
    results = await provider.get_sessions(filters)
    print(f"\nGot {len(results)} conversations with since={since}")

    # Print all conversations and their times using provider method
    filters = QueryFilters()
    conversations = await provider.get_sessions(filters)
    for conv_data in conversations:
        start_time = datetime.fromisoformat(conv_data["start_time"])
        print(f"Conversation {conv_data['id']}: {conv_data['start_time']}")
        print(f"Conv {conv_data['id']}: start_time={start_time}, since={since}")
        print(f"Comparison: {start_time} >= {since} = {start_time >= since}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
