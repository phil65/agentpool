"""Lifecycle of pools, servers, storage and background runs (issue #48)."""

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest

from agentpool import Agent, AgentPool
from agentpool.models.manifest import AgentsManifest
from agentpool.storage.manager import StorageManager
from agentpool_config.storage import MemoryStorageConfig, StorageConfig
from agentpool_server.aggregating_server import AggregatingServer
from agentpool_server.base import BaseServer


pytestmark = pytest.mark.timeout(30)

MANIFEST: dict[str, Any] = {
    "agents": {"helper": {"type": "native", "model": "test"}},
    "storage": {"providers": [{"type": "memory"}]},
}


async def test_concurrent_pool_entries_initialize_once(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = AgentPool(AgentsManifest.model_validate(MANIFEST))
    calls = 0
    cls = type(pool.storage)
    orig = cls.__aenter__

    async def counting(self: Any) -> Any:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return await orig(self)

    monkeypatch.setattr(cls, "__aenter__", counting)
    await asyncio.gather(pool.__aenter__(), pool.__aenter__())
    assert calls == 1
    assert pool._running_count == 2
    await pool.__aexit__(None, None, None)
    await pool.__aexit__(None, None, None)
    assert not pool.is_running


async def test_cancelled_pool_init_cleans_up(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = AgentPool(AgentsManifest.model_validate(MANIFEST))
    cleaned = asyncio.Event()
    started = asyncio.Event()
    orig_cleanup = pool.cleanup

    async def cleanup() -> None:
        cleaned.set()
        await orig_cleanup()

    async def hang(self: Any) -> Any:
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(pool, "cleanup", cleanup)
    monkeypatch.setattr(type(pool.storage), "__aenter__", hang)
    task = asyncio.create_task(pool.__aenter__())
    await asyncio.wait_for(started.wait(), 10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cleaned.is_set()


class _QuickServer(BaseServer):
    async def _start_async(self) -> None:
        await asyncio.sleep(0.01)  # e.g. uvicorn exiting on its own


class _ForeverServer(BaseServer):
    async def _start_async(self) -> None:
        await asyncio.Event().wait()


async def test_background_server_ending_by_itself_stops() -> None:
    server = _QuickServer(MagicMock())
    server.start_background()
    await asyncio.wait_for(server.wait_until_stopped(), 5)
    assert not server.is_running


async def test_background_server_stops_on_stop() -> None:
    server = _ForeverServer(MagicMock())
    server.start_background()
    await asyncio.sleep(0.05)
    server.stop()
    await asyncio.wait_for(server.wait_until_stopped(), 5)
    assert not server.is_running


async def test_run_context_stops_the_server_and_survives_repeated_cancels() -> None:
    server = _ForeverServer(MagicMock())
    async with server.run_context():
        await asyncio.sleep(0.05)
        task = server._server_task
        assert task is not None
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()  # A second cancel during shutdown (N6: RecursionError)
    assert task.done()
    assert not server.is_running


async def test_aggregating_server_handles_a_cancelled_server_task() -> None:
    aggregating = AggregatingServer(MagicMock(), servers=[_QuickServer(MagicMock())])
    task: asyncio.Task[None] = asyncio.create_task(asyncio.Event().wait())  # type: ignore[arg-type]
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await asyncio.wait_for(aggregating._wait_for_tasks([task], []), 5)


async def test_storage_waits_for_its_tasks_before_closing_providers() -> None:
    manager = StorageManager(StorageConfig(providers=[MemoryStorageConfig()]))
    order: list[str] = []
    async with manager:
        provider = manager.providers[0]
        orig_exit = provider.__aexit__

        async def provider_exit(*args: Any) -> None:
            order.append("provider closed")
            await orig_exit(*args)

        provider.__aexit__ = provider_exit  # type: ignore[method-assign]

        async def background() -> None:
            await asyncio.sleep(0.05)
            order.append("task done")

        manager.task_manager.create_task(background())
    assert order == ["task done", "provider closed"]


async def test_agent_is_not_busy_after_its_background_run() -> None:
    agent = Agent(name="a", model="test")
    async with agent:
        task = await agent.run_in_background("hi", max_count=1, interval=0)
        await asyncio.wait_for(task, 10)
        assert not agent.is_busy()
        await agent.stop()
        assert agent._background_task is None
