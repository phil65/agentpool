"""WhatsApp channel implementation using Node.js bridge."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import anyenv

from agentpool.log import get_logger
from agentpool_bot.channels.base import BaseChannel


if TYPE_CHECKING:
    from websockets import ClientConnection

    from agentpool_bot.bus import MessageBus, OutboundMessage
    from agentpool_bot.config import WhatsAppConfig


logger = get_logger(__name__)


class WhatsAppChannel(BaseChannel):
    """WhatsApp channel that connects to a Node.js bridge.

    The bridge uses @whiskeysockets/baileys to handle the WhatsApp Web protocol.
    Communication between Python and Node.js is via WebSocket.
    """

    name = "whatsapp"

    def __init__(self, config: WhatsAppConfig, bus: MessageBus) -> None:
        super().__init__(config, bus)
        self.config: WhatsAppConfig = config
        self._ws: ClientConnection | None = None
        self._connected = False

    async def start(self) -> None:
        """Start the WhatsApp channel by connecting to the bridge."""
        import websockets

        bridge_url = self.config.bridge_url
        logger.info("Connecting to WhatsApp bridge", url=bridge_url)
        self._running = True
        while self._running:
            try:
                async with websockets.connect(bridge_url) as ws:
                    self._ws = ws
                    # Send auth token if configured
                    if self.config.bridge_token:
                        await ws.send(
                            anyenv.dump_json({"type": "auth", "token": self.config.bridge_token})
                        )
                    self._connected = True
                    logger.info("Connected to WhatsApp bridge")

                    # Listen for messages
                    async for message in ws:
                        try:
                            await self._handle_bridge_message(message)
                        except Exception:
                            logger.exception("Error handling bridge message")

            except asyncio.CancelledError:
                break
            except Exception:
                self._connected = False
                self._ws = None
                logger.warning("WhatsApp bridge connection error", exc_info=True)

                if self._running:
                    logger.info("Reconnecting in 5 seconds...")
                    await asyncio.sleep(5)

    async def stop(self) -> None:
        """Stop the WhatsApp channel."""
        self._running = False
        self._connected = False

        if self._ws:
            await self._ws.close()
            self._ws = None

    async def send(self, msg: OutboundMessage) -> None:
        """Send a message through WhatsApp."""
        if not self._ws or not self._connected:
            logger.warning("WhatsApp bridge not connected")
            return

        try:
            payload = {"type": "send", "to": msg.chat_id, "text": msg.content}
            await self._ws.send(anyenv.dump_json(payload))
        except Exception:
            logger.exception("Error sending WhatsApp message")

    async def _handle_bridge_message(self, raw: str | bytes) -> None:
        """Handle a message from the bridge."""
        try:
            data = anyenv.load_json(raw, return_type=dict)
        except anyenv.JsonLoadError:
            logger.warning("Invalid JSON from bridge")
            return

        msg_type = data.get("type")

        if msg_type == "message":
            pn = data.get("pn", "")
            sender = data.get("sender", "")
            content = data.get("content", "")

            user_id = pn if pn else sender
            sender_id = user_id.split("@")[0] if "@" in user_id else user_id

            # Handle voice transcription if it's a voice message
            if content == "[Voice Message]":
                logger.info("Voice message received, transcription not available for WhatsApp")
                content = "[Voice Message: Transcription not available for WhatsApp yet]"
            meta = {
                "message_id": data.get("id"),
                "timestamp": data.get("timestamp"),
                "is_group": data.get("isGroup", False),
            }
            await self._handle_message(
                sender_id=sender_id,
                chat_id=sender,
                content=content,
                metadata=meta,
            )

        elif msg_type == "status":
            status = data.get("status")
            logger.info("WhatsApp status update", status=status)

            if status == "connected":
                self._connected = True
            elif status == "disconnected":
                self._connected = False

        elif msg_type == "qr":
            logger.info("Scan QR code in the bridge terminal to connect WhatsApp")

        elif msg_type == "error":
            logger.error("WhatsApp bridge error", error=data.get("error"))
