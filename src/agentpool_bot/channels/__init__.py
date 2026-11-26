"""Chat channels module with plugin architecture."""

from agentpool_bot.channels.base import BaseChannel
from agentpool_bot.channels.manager import ChannelManager

__all__ = ["BaseChannel", "ChannelManager"]
