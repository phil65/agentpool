"""Durable storage of OpenCode V2 session event logs.

Each session's durable events are appended to a JSONL file. The log is the source of truth
for the V2 view of a session (messages, inbox, agent/model switches). The agent's own
conversation history keeps living in AgentPool's regular storage.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import TYPE_CHECKING

import anyio
from pydantic import TypeAdapter, ValidationError

from agentpool.log import get_logger
from agentpool_config.storage import DATA_DIR
from agentpool_server.opencode_v2_server.events import DurableSessionEvent, dump_model


if TYPE_CHECKING:
    from collections.abc import Sequence


logger = get_logger(__name__)

DEFAULT_EVENT_DIR = DATA_DIR / "opencode-v2" / "sessions"
_EVENT_ADAPTER: TypeAdapter[DurableSessionEvent] = TypeAdapter(DurableSessionEvent)
_SAFE_ID = re.compile(r"^[A-Za-z0-9_\-]+$")


class EventStore:
    """Append-only JSONL event logs, one file per session."""

    def __init__(self, directory: Path | str | None = None) -> None:
        """Initialize the store.

        Args:
            directory: Directory holding the logs (defaults to the AgentPool data dir)
        """
        self.directory = Path(directory) if directory is not None else DEFAULT_EVENT_DIR
        self._locks: dict[str, anyio.Lock] = {}

    def _path(self, session_id: str) -> Path:
        if not _SAFE_ID.match(session_id):
            msg = f"Invalid session ID for event store: {session_id!r}"
            raise ValueError(msg)
        return self.directory / f"{session_id}.jsonl"

    def _lock(self, session_id: str) -> anyio.Lock:
        return self._locks.setdefault(session_id, anyio.Lock())

    async def exists(self, session_id: str) -> bool:
        """Whether a log exists for the session."""
        return await anyio.Path(self._path(session_id)).exists()

    async def append(self, session_id: str, events: Sequence[DurableSessionEvent]) -> None:
        """Append events to a session's log."""
        if not events:
            return
        path = anyio.Path(self._path(session_id))
        lines = "".join(json.dumps(dump_model(e), separators=(",", ":")) + "\n" for e in events)
        async with self._lock(session_id):
            await path.parent.mkdir(parents=True, exist_ok=True)
            async with await anyio.open_file(path, "a", encoding="utf-8") as file:
                await file.write(lines)

    async def write(self, session_id: str, events: Sequence[DurableSessionEvent]) -> None:
        """Replace a session's log with the given events."""
        path = anyio.Path(self._path(session_id))
        lines = "".join(json.dumps(dump_model(e), separators=(",", ":")) + "\n" for e in events)
        async with self._lock(session_id):
            await path.parent.mkdir(parents=True, exist_ok=True)
            # Atomic: readers and a crash see the old log or the new one, never a truncated one
            temp = path.with_name(f"{path.name}.tmp")
            await temp.write_text(lines, encoding="utf-8")
            await temp.replace(path)

    async def load(self, session_id: str) -> list[DurableSessionEvent] | None:
        """Load a session's log, or None if there is none.

        Lines that don't validate against the pinned schema (e.g. written by a newer
        version) are skipped with a warning.
        """
        path = anyio.Path(self._path(session_id))
        async with self._lock(session_id):
            if not await path.exists():
                return None
            text = await path.read_text(encoding="utf-8")
        events: list[DurableSessionEvent] = []
        for number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                events.append(_EVENT_ADAPTER.validate_json(line))
            except ValidationError:
                logger.warning(
                    "Skipping invalid event log line", session_id=session_id, line=number
                )
        return events

    async def delete(self, session_id: str) -> None:
        """Delete a session's log."""
        path = anyio.Path(self._path(session_id))
        async with self._lock(session_id):
            if await path.exists():
                await path.unlink()
        self._locks.pop(session_id, None)
