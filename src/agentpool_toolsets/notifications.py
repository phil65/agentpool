"""Apprise-based notifications toolset implementation."""

from typing import TYPE_CHECKING, Any

from schemez.functionschema.typedefs import OpenAIFunctionDefinition

from agentpool.log import get_logger
from agentpool.resource_providers import ResourceProvider


if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from agentpool.tools.base import Tool


logger = get_logger(__name__)


def get_schema(channel_names: list[str] | None) -> OpenAIFunctionDefinition:
    properties: dict[str, Any] = {
        "message": {"type": "string", "description": "The notification message body"},
        "title": {"type": "string", "description": "Optional notification title"},
    }

    if channel_names:
        properties["channel"] = {
            "type": "string",
            "enum": channel_names,
            "description": "Send to a specific channel. If not specified, sends to all channels.",
        }
    else:
        properties["channel"] = {
            "type": "string",
            "description": "Send to a specific channel. If not specified, sends to all channels.",
        }

    return OpenAIFunctionDefinition(
        name="send_notification",
        description=(
            "Send a notification via configured channels. "
            "Specify a channel name to send to that channel only, "
            "or omit to broadcast to all channels."
        ),
        parameters={"type": "object", "properties": properties, "required": ["message"]},
    )


class NotificationsTools(ResourceProvider):
    """Provider for Apprise-based notification tools.

    Provides a send_notification tool that can send messages via
    various notification services (Slack, Telegram, Email, etc.)
    configured through Apprise URLs.
    """

    def __init__(
        self,
        channels: Mapping[str, str | list[str]],
        name: str = "notifications",
    ) -> None:
        """Initialize notifications provider.

        Args:
            channels: Named notification channels mapping to Apprise URL(s).
                      Values can be a single URL string or list of URLs.
            name: Provider name
        """
        import apprise

        super().__init__(name=name)
        self.channels = channels
        self._tools: list[Tool] | None = None
        self._apprise = apprise.Apprise()
        for channel_name, urls in self.channels.items():
            # Normalize to list
            url_list = [urls] if isinstance(urls, str) else urls
            for url in url_list:
                # Tag each URL with the channel name for filtering
                self._apprise.add(url, tag=channel_name)
                logger.debug("Added notification URL", channel=channel_name, url=url[:30] + "...")

    async def get_tools(self) -> Sequence[Tool]:
        """Get notification tools with dynamic schema based on configured channels."""
        if self._tools is not None:
            return self._tools

        channel_names = sorted(self.channels.keys())
        schema = get_schema(channel_names)
        tool = self.create_tool(self.send_notification, schema_override=schema, open_world=True)
        self._tools = [tool]
        return self._tools

    async def send_notification(
        self,
        message: str,
        title: str | None = None,
        channel: str | None = None,
    ) -> dict[str, Any]:
        """Send a notification via configured channels.

        Args:
            message: The notification message body
            title: Optional notification title
            channel: Send to specific channel, or all if not specified

        Returns:
            Result dict with success status and details
        """
        if channel:
            if channel not in self.channels:
                return {
                    "success": False,
                    "error": f"Unknown channel: {channel}",
                    "available_channels": list(self.channels.keys()),
                }
            notify_tag = channel
            target_desc = f"channel '{channel}'"
        else:
            notify_tag = "all"
            target_desc = "all channels"

        logger.info("Sending notification", target=target_desc, title=title, length=len(message))

        try:
            success = self._apprise.notify(body=message, title=title or "", tag=notify_tag)
        except Exception as e:
            logger.exception("Failed to send notification")
            return {"success": False, "error": str(e), "target": target_desc}

        if success:
            logger.info("Notification sent successfully", target=target_desc)
            return {
                "success": True,
                "target": target_desc,
                "message": "Notification sent successfully",
            }
        logger.warning("Notification may have partially failed", target=target_desc)
        return {
            "success": False,
            "target": target_desc,
            "message": "Notification delivery may have failed for some channels",
        }
