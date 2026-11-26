"""Message toolset — lets agents send messages to chat channels via the bus."""

from typing import TYPE_CHECKING

from agentpool.log import get_logger
from agentpool.resource_providers import StaticResourceProvider


if TYPE_CHECKING:
    from agentpool.tools.base import Tool
    from agentpool_bot.bus import MessageBus


logger = get_logger(__name__)


class MessageTools(StaticResourceProvider):
    """Provider that gives agents the ability to send messages to chat channels.

    Wraps a ``MessageBus`` and exposes a ``send_message`` tool.  When the
    agent is processing a message from a specific channel/chat, set
    ``default_channel`` and ``default_chat_id`` so the agent doesn't have to
    specify them on every call.

    Args:
        bus: The message bus to publish outbound messages to.
        default_channel: Default target channel (e.g. ``"telegram"``).
        default_chat_id: Default target chat/user id.
        name: Provider name.
    """

    def __init__(
        self,
        bus: MessageBus,
        default_channel: str = "",
        default_chat_id: str = "",
        name: str = "message",
    ) -> None:
        super().__init__(name=name)
        self.bus = bus
        self.default_channel = default_channel
        self.default_chat_id = default_chat_id
        self._tools: list[Tool] = [
            self.create_tool(self.send_message, category="other", open_world=True),
        ]

    def set_context(self, channel: str, chat_id: str) -> None:
        """Update the default routing for outbound messages.

        Called by the bus-to-agent adapter before each agent invocation so
        that the agent can omit channel/chat_id when replying to the current
        conversation.

        Args:
            channel: Channel name (e.g. ``"telegram"``, ``"discord"``).
            chat_id: Chat or user identifier within the channel.
        """
        self.default_channel = channel
        self.default_chat_id = chat_id

    async def send_message(
        self,
        content: str,
        channel: str | None = None,
        chat_id: str | None = None,
        media: list[str] | None = None,
    ) -> str:
        """Send a message to a chat channel.

        Use this when you need to proactively send a message to a user or
        chat.  For normal conversational replies you do **not** need this
        tool — just respond with text.

        Args:
            content: The message text to send.
            channel: Target channel (e.g. ``"telegram"``).  Falls back to the
                current conversation's channel if omitted.
            chat_id: Target chat / user id.  Falls back to the current
                conversation's chat if omitted.
            media: Optional list of local file paths to attach.

        Returns:
            Confirmation string, or an error message.
        """
        from agentpool_bot.bus import OutboundMessage

        resolved_channel = channel or self.default_channel
        resolved_chat_id = chat_id or self.default_chat_id

        if not resolved_channel or not resolved_chat_id:
            return "Error: no target channel/chat_id specified"

        msg = OutboundMessage(
            channel=resolved_channel,
            chat_id=resolved_chat_id,
            content=content,
            media=media or [],
        )

        try:
            await self.bus.publish_outbound(msg)
        except Exception:
            logger.exception(
                "Failed to send message",
                channel=resolved_channel,
                chat_id=resolved_chat_id,
            )
            return f"Error: failed to send message to {resolved_channel}:{resolved_chat_id}"

        media_info = f" with {len(media)} attachment(s)" if media else ""
        return f"Message sent to {resolved_channel}:{resolved_chat_id}{media_info}"
