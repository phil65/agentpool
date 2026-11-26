"""StorageManager writes: every provider at once, a failing one logged, not raised."""

from __future__ import annotations

import asyncio
from typing import Any

from agentpool.messaging import ChatMessage
from agentpool.storage.manager import StorageManager
from agentpool_config.storage import MemoryStorageConfig
from agentpool_storage.base import StorageProvider


class _Recorder(StorageProvider):
    def __init__(self, *, fail: bool = False, gate: asyncio.Event | None = None) -> None:
        super().__init__(MemoryStorageConfig())
        self.fail = fail
        self.gate = gate
        self.logged: list[str] = []

    async def _write(self, what: str) -> None:
        if self.gate is not None:
            await self.gate.wait()
        if self.fail:
            raise OSError("disk full")
        self.logged.append(what)

    async def log_message(self, *, message: ChatMessage[Any]) -> None:
        await self._write(str(message.content))

    async def log_session(self, **kwargs: Any) -> None:
        await self._write(f"session:{kwargs['session_id']}")

    async def log_command(self, **kwargs: Any) -> None:
        await self._write(f"command:{kwargs['command']}")

    async def update_session_title(self, session_id: str, title: str) -> None:
        await self._write(f"title:{title}")


async def test_a_failing_provider_does_not_fail_the_write_or_the_others():
    broken, healthy = _Recorder(fail=True), _Recorder()
    manager = StorageManager(providers=[broken, healthy])
    await manager.log_message(ChatMessage[str](content="hi", role="user", session_id="s1"))
    await manager.log_session(session_id="s1", node_name="a")
    await manager.log_command(agent_name="a", session_id="s1", command="/help")
    await manager.update_session_title("s1", "Title")
    assert healthy.logged == ["hi", "session:s1", "command:/help", "title:Title"]


async def test_providers_are_written_at_once():
    """The first provider waits until the second wrote: sequential writes would hang."""
    second_wrote = asyncio.Event()

    class _Second(_Recorder):
        async def log_message(self, *, message: ChatMessage[Any]) -> None:
            await super().log_message(message=message)
            second_wrote.set()

    first, second = _Recorder(gate=second_wrote), _Second()
    manager = StorageManager(providers=[first, second])
    message = ChatMessage[str](content="hi", role="user", session_id="s1")
    await asyncio.wait_for(manager.log_message(message), timeout=5)
    assert first.logged == second.logged == ["hi"]
