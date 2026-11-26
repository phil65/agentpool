"""Tests for NotificationsTools provider."""

from unittest.mock import MagicMock

import pytest

from agentpool_config.toolsets import NotificationsToolsetConfig
from agentpool_toolsets.notifications import NotificationsTools


@pytest.fixture
def sample_channels() -> dict[str, str | list[str]]:
    """Create sample notification channels for testing."""
    return {
        "team_slack": "slack://TokenA/TokenB/TokenC/",
        "personal_telegram": "tgram://bottoken/ChatID",
        "ops_alerts": ["slack://ops-channel/", "mailto://ops@company.com"],
    }


@pytest.fixture
def notifications_provider(sample_channels: dict[str, str | list[str]]) -> NotificationsTools:
    """Create NotificationsTools provider with sample channels."""
    return NotificationsTools(channels=sample_channels)


async def test_get_tools_returns_send_notification(notifications_provider: NotificationsTools):
    """Test that get_tools returns the send_notification tool."""
    tools = await notifications_provider.get_tools()
    assert len(tools) == 1
    assert tools[0].name == "send_notification"


async def test_tool_schema_includes_channel_enum(notifications_provider: NotificationsTools):
    """Test that tool schema includes enum for channels."""
    tools = await notifications_provider.get_tools()
    schema = tools[0].schema["function"]
    channel_prop = schema["parameters"]["properties"]["channel"]
    assert "enum" in channel_prop
    assert set(channel_prop["enum"]) == {"ops_alerts", "personal_telegram", "team_slack"}


async def test_tool_schema_message_is_required(notifications_provider: NotificationsTools):
    """Test that message is required in tool schema."""
    tools = await notifications_provider.get_tools()
    schema = tools[0].schema["function"]
    assert "message" in schema["parameters"]["required"]


async def test_tool_schema_no_tag_property(notifications_provider: NotificationsTools):
    """Test that simplified schema has no tag property."""
    tools = await notifications_provider.get_tools()
    schema = tools[0].schema["function"]
    assert "tag" not in schema["parameters"]["properties"]


async def test_send_notification_to_channel(notifications_provider: NotificationsTools):
    """Test sending notification to specific channel."""
    mock_apprise = MagicMock()
    mock_apprise.notify.return_value = True
    notifications_provider._apprise = mock_apprise
    call = {"message": "Test message", "title": "Test Title", "channel": "team_slack"}
    result = await notifications_provider.send_notification(**call)
    assert result["success"] is True
    assert "team_slack" in result["target"]
    mock_apprise.notify.assert_called_once_with(
        body="Test message", title="Test Title", tag="team_slack"
    )


async def test_send_notification_to_all(notifications_provider: NotificationsTools):
    """Test sending notification to all channels."""
    mock_apprise = MagicMock()
    mock_apprise.notify.return_value = True
    notifications_provider._apprise = mock_apprise
    result = await notifications_provider.send_notification(message="Broadcast message")
    assert result["success"] is True
    assert "all" in result["target"]
    mock_apprise.notify.assert_called_once_with(body="Broadcast message", title="", tag="all")


async def test_send_notification_unknown_channel(notifications_provider: NotificationsTools):
    """Test sending to unknown channel returns error."""
    result = await notifications_provider.send_notification(message="Test", channel="nonexistent")
    assert result["success"] is False
    assert "Unknown channel" in result["error"]
    assert "available_channels" in result


async def test_send_notification_failure(notifications_provider: NotificationsTools):
    """Test handling of notification failure."""
    mock_apprise = MagicMock()
    mock_apprise.notify.return_value = False
    notifications_provider._apprise = mock_apprise
    result = await notifications_provider.send_notification(message="This will fail")
    assert result["success"] is False


async def test_send_notification_exception(notifications_provider: NotificationsTools):
    """Test handling of exception during notification."""
    mock_apprise = MagicMock()
    mock_apprise.notify.side_effect = Exception("Network error")
    notifications_provider._apprise = mock_apprise
    result = await notifications_provider.send_notification(message="This will raise")
    assert result["success"] is False
    assert "error" in result
    assert "Network error" in result["error"]


def test_config_creates_provider(sample_channels: dict[str, str | list[str]]):
    """Test that config correctly creates provider."""
    config = NotificationsToolsetConfig(channels=sample_channels)
    provider = config.get_provider()
    assert isinstance(provider, NotificationsTools)
    assert provider.channels == sample_channels


async def test_empty_channels():
    """Test provider with no channels configured."""
    provider = NotificationsTools(channels={})
    tools = await provider.get_tools()
    assert len(tools) == 1
    schema = tools[0].schema["function"]
    # Should not have enum constraints when empty
    channel_prop = schema["parameters"]["properties"]["channel"]
    assert "enum" not in channel_prop
