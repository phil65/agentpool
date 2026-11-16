"""Discord channel implementation using Discord Gateway websocket."""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import TYPE_CHECKING, Any

import anyenv
import httpx
import websockets

from agentpool.log import get_logger
from agentpool_bot.channels.base import BaseChannel


if TYPE_CHECKING:
    from agentpool_bot.bus import MessageBus, OutboundMessage
    from agentpool_bot.config import DiscordConfig


logger = get_logger(__name__)


DISCORD_API_BASE = "https://discord.com/api/v10"
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024  # 20MB

# Discord Gateway opcodes
_OP_DISPATCH = 0
_OP_HEARTBEAT = 1
_OP_IDENTIFY = 2
_OP_RECONNECT = 7
_OP_INVALID_SESSION = 9
_OP_HELLO = 10

_HTTP_TOO_MANY_REQUESTS = 429
_MAX_SEND_RETRIES = 3


class DiscordChannel(BaseChannel):
    """Discord channel using Gateway websocket."""

    name = "discord"

    def __init__(self, config: DiscordConfig, bus: MessageBus) -> None:
        super().__init__(config, bus)
        self.config: DiscordConfig = config
        self._ws: Any = None
        self._seq: int | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._typing_tasks: dict[str, asyncio.Task[None]] = {}
        self._http: httpx.AsyncClient | None = None

    async def start(self) -> None:
        """Start the Discord gateway connection."""
        if not self.config.token:
            logger.error("Discord bot token not configured")
            return

        self._running = True
        self._http = httpx.AsyncClient(timeout=30.0)

        while self._running:
            try:
                logger.info("Connecting to Discord gateway...")
                async with websockets.connect(self.config.gateway_url) as ws:
                    self._ws = ws
                    await self._gateway_loop()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.warning("Discord gateway error", exc_info=True)
                if self._running:
                    logger.info("Reconnecting to Discord gateway in 5 seconds...")
                    await asyncio.sleep(5)

    async def stop(self) -> None:
        """Stop the Discord channel."""
        self._running = False
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            self._heartbeat_task = None
        for task in self._typing_tasks.values():
            task.cancel()
        self._typing_tasks.clear()
        if self._ws:
            await self._ws.close()
            self._ws = None
        if self._http:
            await self._http.aclose()
            self._http = None

    async def send(self, msg: OutboundMessage) -> None:
        """Send a message through Discord REST API."""
        if not self._http:
            logger.warning("Discord HTTP client not initialized")
            return

        url = f"{DISCORD_API_BASE}/channels/{msg.chat_id}/messages"
        payload: dict[str, Any] = {"content": msg.content}

        if msg.reply_to:
            payload["message_reference"] = {"message_id": msg.reply_to}
            payload["allowed_mentions"] = {"replied_user": False}

        headers = {"Authorization": f"Bot {self.config.token}"}

        try:
            for attempt in range(_MAX_SEND_RETRIES):
                try:
                    response = await self._http.post(url, headers=headers, json=payload)
                    if response.status_code == _HTTP_TOO_MANY_REQUESTS:
                        data = response.json()
                        retry_after = float(data.get("retry_after", 1.0))
                        logger.warning("Discord rate limited", retry_after=retry_after)
                        await asyncio.sleep(retry_after)
                        continue
                    response.raise_for_status()
                except Exception:
                    if attempt == _MAX_SEND_RETRIES - 1:
                        logger.exception("Error sending Discord message")
                    else:
                        await asyncio.sleep(1)
                else:
                    return
        finally:
            await self._stop_typing(msg.chat_id)

    async def _gateway_loop(self) -> None:
        """Main gateway loop: identify, heartbeat, dispatch events."""
        if not self._ws:
            return

        async for raw in self._ws:
            try:
                data = anyenv.load_json(raw)
            except anyenv.JsonLoadError:
                logger.warning("Invalid JSON from Discord gateway")
                continue

            op = data.get("op")
            event_type = data.get("t")
            seq = data.get("s")
            payload = data.get("d")

            if seq is not None:
                self._seq = seq

            if op == _OP_HELLO:
                interval_ms = payload.get("heartbeat_interval", 45000)
                await self._start_heartbeat(interval_ms / 1000)
                await self._identify()
            elif op == _OP_DISPATCH and event_type == "READY":
                logger.info("Discord gateway READY")
            elif op == _OP_DISPATCH and event_type == "MESSAGE_CREATE":
                await self._handle_message_create(payload)
            elif op == _OP_RECONNECT:
                logger.info("Discord gateway requested reconnect")
                break
            elif op == _OP_INVALID_SESSION:
                logger.warning("Discord gateway invalid session")
                break

    async def _identify(self) -> None:
        """Send IDENTIFY payload."""
        if not self._ws:
            return

        identify = {
            "op": _OP_IDENTIFY,
            "d": {
                "token": self.config.token,
                "intents": self.config.intents,
                "properties": {
                    "os": "agentpool",
                    "browser": "agentpool",
                    "device": "agentpool",
                },
            },
        }
        await self._ws.send(anyenv.dump_json(identify))

    async def _start_heartbeat(self, interval_s: float) -> None:
        """Start or restart the heartbeat loop."""
        if self._heartbeat_task:
            self._heartbeat_task.cancel()

        async def heartbeat_loop() -> None:
            while self._running and self._ws:
                payload = {"op": _OP_HEARTBEAT, "d": self._seq}
                try:
                    await self._ws.send(anyenv.dump_json(payload))
                except Exception:
                    logger.warning("Discord heartbeat failed", exc_info=True)
                    break
                await asyncio.sleep(interval_s)

        self._heartbeat_task = asyncio.create_task(heartbeat_loop())

    async def _handle_message_create(self, payload: dict[str, Any]) -> None:
        """Handle incoming Discord messages."""
        author = payload.get("author") or {}
        if author.get("bot"):
            return

        sender_id = str(author.get("id", ""))
        channel_id = str(payload.get("channel_id", ""))
        content = payload.get("content") or ""

        if not sender_id or not channel_id:
            return

        if not self.is_allowed(sender_id):
            return

        content_parts = [content] if content else []
        media_paths: list[str] = []
        media_dir = Path.home() / ".agentpool" / "media"
        media_dir.mkdir(parents=True, exist_ok=True)
        for attachment in payload.get("attachments") or []:
            url = attachment.get("url")
            filename = attachment.get("filename") or "attachment"
            size = attachment.get("size") or 0
            if not url or not self._http:
                continue
            if size and size > MAX_ATTACHMENT_BYTES:
                content_parts.append(f"[attachment: {filename} - too large]")
                continue
            try:
                file_path = media_dir / (
                    f"{attachment.get('id', 'file')}_{filename.replace('/', '_')}"
                )
                resp = await self._http.get(url)
                resp.raise_for_status()
                file_path.write_bytes(resp.content)
                media_paths.append(str(file_path))
                content_parts.append(f"[attachment: {file_path}]")
            except Exception:
                logger.warning("Failed to download Discord attachment", exc_info=True)
                content_parts.append(f"[attachment: {filename} - download failed]")

        reply_to = (payload.get("referenced_message") or {}).get("id")
        await self._start_typing(channel_id)
        await self._handle_message(
            sender_id=sender_id,
            chat_id=channel_id,
            content="\n".join(p for p in content_parts if p) or "[empty message]",
            media=media_paths,
            metadata={
                "message_id": str(payload.get("id", "")),
                "guild_id": payload.get("guild_id"),
                "reply_to": reply_to,
            },
        )

    async def _start_typing(self, channel_id: str) -> None:
        """Start periodic typing indicator for a channel."""
        await self._stop_typing(channel_id)

        async def typing_loop() -> None:
            if not self._http:
                return
            url = f"{DISCORD_API_BASE}/channels/{channel_id}/typing"
            headers = {"Authorization": f"Bot {self.config.token}"}
            while self._running:
                with contextlib.suppress(Exception):
                    await self._http.post(url, headers=headers)
                await asyncio.sleep(8)

        self._typing_tasks[channel_id] = asyncio.create_task(typing_loop())

    async def _stop_typing(self, channel_id: str) -> None:
        """Stop typing indicator for a channel."""
        if task := self._typing_tasks.pop(channel_id, None):
            task.cancel()
