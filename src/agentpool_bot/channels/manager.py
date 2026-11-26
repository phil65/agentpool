"""Channel manager for coordinating chat channels."""

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any

from agentpool.log import get_logger


if TYPE_CHECKING:
    from agentpool_bot.bus import MessageBus
    from agentpool_bot.channels.base import BaseChannel
    from agentpool_bot.config import ChannelsConfig


logger = get_logger(__name__)


class ChannelManager:
    """Manages chat channels and coordinates message routing.

    Responsibilities:
    - Initialize enabled channels (Telegram, WhatsApp, etc.)
    - Start/stop channels
    - Route outbound messages
    """

    def __init__(self, config: ChannelsConfig, bus: MessageBus) -> None:
        self.config = config
        self.bus = bus
        self.channels: dict[str, BaseChannel] = {}
        self._dispatch_task: asyncio.Task[None] | None = None
        if self.config.telegram.enabled:
            self.channels["telegram"] = self.config.telegram.get_provider(self.bus)
        if self.config.whatsapp.enabled:
            self.channels["whatsapp"] = self.config.whatsapp.get_provider(self.bus)
        if self.config.discord.enabled:
            self.channels["discord"] = self.config.discord.get_provider(self.bus)
        if self.config.email.enabled:
            self.channels["email"] = self.config.email.get_provider(self.bus)
        if self.config.slack.enabled:
            self.channels["slack"] = self.config.slack.get_provider(self.bus)

    async def _start_channel(self, name: str, channel: BaseChannel) -> None:
        """Start a channel and log any exceptions."""
        try:
            await channel.start()
        except Exception:
            logger.exception("Failed to start channel", channel=name)

    async def start_all(self) -> None:
        """Start all channels and the outbound dispatcher."""
        if not self.channels:
            logger.warning("No channels enabled")
            return

        # Start outbound dispatcher
        self._dispatch_task = asyncio.create_task(self._dispatch_outbound())
        # Start channels
        tasks = []
        for name, channel in self.channels.items():
            logger.info("Starting channel", channel=name)
            tasks.append(asyncio.create_task(self._start_channel(name, channel)))

        await asyncio.gather(*tasks, return_exceptions=True)

    async def stop_all(self) -> None:
        """Stop all channels and the dispatcher."""
        logger.info("Stopping all channels...")
        if self._dispatch_task:
            self._dispatch_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._dispatch_task

        for name, channel in self.channels.items():
            try:
                await channel.stop()
                logger.info("Stopped channel", channel=name)
            except Exception:
                logger.exception("Error stopping channel", channel=name)

    async def _dispatch_outbound(self) -> None:
        """Dispatch outbound messages to the appropriate channel."""
        logger.info("Outbound dispatcher started")

        while True:
            try:
                msg = await asyncio.wait_for(self.bus.consume_outbound(), timeout=1.0)
                channel = self.channels.get(msg.channel)
                if channel:
                    try:
                        await channel.send(msg)
                    except Exception:
                        logger.exception("Error sending to channel", channel=msg.channel)
                else:
                    logger.warning("Unknown channel", channel=msg.channel)

            except TimeoutError:
                continue
            except asyncio.CancelledError:
                break

    def get_channel(self, name: str) -> BaseChannel | None:
        """Get a channel by name."""
        return self.channels.get(name)

    def get_status(self) -> dict[str, Any]:
        """Get status of all channels."""
        return {
            name: {"enabled": True, "running": channel.is_running}
            for name, channel in self.channels.items()
        }

    @property
    def enabled_channels(self) -> list[str]:
        """Get list of enabled channel names."""
        return list(self.channels.keys())
