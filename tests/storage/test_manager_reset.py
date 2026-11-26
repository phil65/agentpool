"""StorageManager.reset: skip providers whose data agentpool doesn't own, bounded fan-out."""

import asyncio
from typing import TYPE_CHECKING

from agentpool.messaging import ChatMessage
from agentpool.storage.manager import RESET_CONCURRENCY, StorageManager
from agentpool_config.storage import ClaudeStorageConfig, MemoryStorageConfig
from agentpool_storage.base import StorageProvider
from agentpool_storage.claude_provider.provider import ClaudeStorageProvider
from agentpool_storage.memory_provider.provider import MemoryStorageProvider


if TYPE_CHECKING:
    from pathlib import Path


async def test_manager_reset_skips_foreign_providers(tmp_path: Path):
    claude = ClaudeStorageProvider(ClaudeStorageConfig(path=str(tmp_path / ".claude")))
    msg = ChatMessage[str](content="hi", role="user", session_id="/w/a:s1")
    await claude.log_message(message=msg)
    memory = MemoryStorageProvider(MemoryStorageConfig())
    calls: list[str] = []

    async def claude_reset(**kwargs: object) -> tuple[int, int]:
        calls.append("claude")
        return (0, 0)

    claude.reset = claude_reset  # type: ignore[method-assign]
    manager = StorageManager(providers=[memory, claude])
    await manager.reset(hard=True)
    assert calls == []
    assert len(list(tmp_path.rglob("*.jsonl"))) == 1


class _SlowProvider(StorageProvider):
    active = 0
    peak = 0

    def __init__(self) -> None:
        super().__init__(MemoryStorageConfig())

    async def reset(self, *, agent_name: str | None = None, hard: bool = False) -> tuple[int, int]:
        cls = type(self)
        cls.active += 1
        cls.peak = max(cls.peak, cls.active)
        await asyncio.sleep(0.01)
        cls.active -= 1
        return (1, 1)


async def test_manager_reset_bounded_concurrency():
    providers: list[StorageProvider] = [_SlowProvider() for _ in range(64)]
    manager = StorageManager(providers=providers)
    assert await manager.reset() == (1, 1)
    assert _SlowProvider.peak == RESET_CONCURRENCY
