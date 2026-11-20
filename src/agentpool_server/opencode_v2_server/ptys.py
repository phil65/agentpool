"""PTY sessions for the V2 API (backed by the main agent's execution environment).

Mirrors OpenCode's PTY transport: output is buffered with an absolute byte cursor, clients
connect via websocket with a short-lived ticket (``POST .../connect-token``), receive the
replay from their cursor, then a ``0x00`` + JSON ``{"cursor": n}`` meta frame, then live
output. Inbound frames are terminal input.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
import secrets
import time
from typing import TYPE_CHECKING

from agentpool.log import get_logger
from agentpool_server.opencode_v2_server.events import build_event
from opencode_v2_sdk.models import (
    Pty,
    PtyCreated,
    PtyCreatedData,
    PtyDeleted,
    PtyDeletedData,
    PtyExited,
    PtyExitedData,
    PtyUpdated,
    PtyUpdatedData,
)


if TYPE_CHECKING:
    from collections.abc import Callable

    from exxec.pty_manager import PtyInfo, PtyManagerProtocol
    from pydantic import BaseModel


logger = get_logger(__name__)

MAX_BUFFER = 2_000_000
"""Output bytes kept per PTY for replay."""
TICKET_TTL = 60
"""Seconds a connect ticket stays valid."""
MAX_PTYS = 20


class PtyLimitError(Exception):
    """Too many PTYs are open."""


@dataclass
class PtySession:
    """A PTY with its replay buffer and websocket subscribers."""

    info: Pty
    raw_id: str
    """ID of the PTY in the execution environment's PTY manager."""
    directory: str
    buffer: bytearray = field(default_factory=bytearray)
    dropped: int = 0
    subscribers: set[asyncio.Queue[bytes | None]] = field(default_factory=set)
    reader: asyncio.Task[None] | None = None

    @property
    def cursor(self) -> int:
        """Absolute output position."""
        return self.dropped + len(self.buffer)

    def append(self, chunk: bytes) -> None:
        """Buffer output and fan it out to subscribers."""
        self.buffer.extend(chunk)
        overflow = len(self.buffer) - MAX_BUFFER
        if overflow > 0:
            del self.buffer[:overflow]
            self.dropped += overflow
        for queue in self.subscribers:
            queue.put_nowait(chunk)

    def replay(self, cursor: int | None) -> bytes:
        """Buffered output from an absolute cursor (``-1``/None: everything buffered)."""
        if cursor is None or cursor < 0:
            return bytes(self.buffer)
        return bytes(self.buffer[max(cursor - self.dropped, 0) :])


def meta_frame(cursor: int) -> bytes:
    """Control frame carrying the absolute output cursor."""
    return b"\x00" + json.dumps({"cursor": cursor}).encode()


@dataclass
class PtyRegistry:
    """Tracks PTYs created through the API."""

    manager: Callable[[], PtyManagerProtocol]
    publish: Callable[[BaseModel, str], None]
    sessions: dict[str, PtySession] = field(default_factory=dict)
    tickets: dict[str, tuple[str, float]] = field(default_factory=dict)

    def _info(self, raw: PtyInfo, title: str, directory: str) -> Pty:
        return Pty(
            id=raw.id if raw.id.startswith("pty") else f"pty_{raw.id}",
            title=title,
            command=raw.command,
            args=list(raw.args),
            cwd=raw.cwd or directory,
            status=raw.status,
            pid=max(raw.pid, 0),
            exit_code=raw.exit_code if raw.exit_code is not None and raw.exit_code >= 0 else None,
        )

    def _publish(
        self, event_cls: type[BaseModel], data: BaseModel, kind: str, directory: str
    ) -> None:
        self.publish(build_event(event_cls, data, directory=directory), kind)

    async def create(
        self,
        *,
        directory: str,
        command: str | None,
        args: list[str] | None,
        cwd: str | None,
        title: str | None,
        env: dict[str, str] | None,
    ) -> PtySession:
        """Start a PTY.

        Raises:
            PtyLimitError: If too many PTYs are open
        """
        if len(self.sessions) >= MAX_PTYS:
            msg = f"Too many PTY sessions ({len(self.sessions)})"
            raise PtyLimitError(msg)
        manager = self.manager()
        raw = await manager.create(command=command, args=args, cwd=cwd or directory, env=env)
        info = self._info(raw, title or f"Terminal {raw.id[-4:]}", directory)
        session = PtySession(info=info, raw_id=raw.id, directory=directory)
        self.sessions[info.id] = session
        session.reader = asyncio.create_task(self._read(raw.id, session), name=f"pty-{info.id}")
        self._publish(PtyCreated, PtyCreatedData(info=info), "pty.created", directory)
        return session

    async def _read(self, raw_id: str, session: PtySession) -> None:
        manager = self.manager()
        exit_code = 0
        try:
            async for chunk in manager.stream(raw_id):
                session.append(chunk)
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("PTY read failed", pty_id=session.info.id)
            exit_code = 1
        if (raw := await manager.get_info(raw_id)) is not None and raw.exit_code is not None:
            exit_code = max(raw.exit_code, 0)
        session.info = session.info.model_copy(update={"status": "exited", "exit_code": exit_code})
        for queue in session.subscribers:
            queue.put_nowait(None)
        data = PtyExitedData(id=session.info.id, exit_code=exit_code)
        self._publish(PtyExited, data, "pty.exited", session.directory)

    async def update(
        self, pty_id: str, *, title: str | None, rows: int | None, cols: int | None
    ) -> PtySession | None:
        """Rename or resize a PTY."""
        from exxec.pty_manager import PtySize

        session = self.sessions.get(pty_id)
        if session is None:
            return None
        if rows is not None and cols is not None:
            await self.manager().resize(session.raw_id, PtySize(rows=rows, cols=cols))
        if title is not None:
            session.info = session.info.model_copy(update={"title": title})
        self._publish(
            PtyUpdated, PtyUpdatedData(info=session.info), "pty.updated", session.directory
        )
        return session

    async def remove(self, pty_id: str) -> bool:
        """Kill and forget a PTY."""
        session = self.sessions.pop(pty_id, None)
        if session is None:
            return False
        await self.manager().kill(session.raw_id)
        if session.reader is not None and not session.reader.done():
            session.reader.cancel()
        for queue in session.subscribers:
            queue.put_nowait(None)
        self._publish(PtyDeleted, PtyDeletedData(id=pty_id), "pty.deleted", session.directory)
        return True

    async def write(self, session: PtySession, data: bytes) -> None:
        """Send terminal input."""
        await self.manager().write(session.raw_id, data)

    def issue_ticket(self, pty_id: str) -> str:
        """A single-use connect ticket for a PTY."""
        now = time.monotonic()
        self.tickets = {k: v for k, v in self.tickets.items() if v[1] > now}
        ticket = secrets.token_urlsafe(24)
        self.tickets[ticket] = (pty_id, now + TICKET_TTL)
        return ticket

    def consume_ticket(self, ticket: str, pty_id: str) -> bool:
        """Validate (and invalidate) a connect ticket."""
        entry = self.tickets.pop(ticket, None)
        return entry is not None and entry[0] == pty_id and entry[1] > time.monotonic()

    async def shutdown(self) -> None:
        """Kill all PTYs."""
        for pty_id in list(self.sessions):
            try:
                await self.remove(pty_id)
            except Exception:  # noqa: BLE001
                logger.warning("Failed to kill PTY", pty_id=pty_id)
