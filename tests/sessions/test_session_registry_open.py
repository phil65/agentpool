"""Concurrent ``SessionRegistry.open`` of one id gives every caller the same session."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agentpool.sessions import SessionRegistry


class _SlowStorage:
    """Storage whose ``load_session`` takes a while (so opens overlap)."""

    def __init__(self, *, fail_first: bool = False) -> None:
        self.loads = 0
        self.fail_first = fail_first

    async def load_session(self, session_id: str) -> Any:
        self.loads += 1
        call = self.loads
        await asyncio.sleep(0.01)
        if self.fail_first and call == 1:
            raise OSError("storage hiccup")


async def test_concurrent_opens_share_one_session() -> None:
    storage = _SlowStorage()
    registry = SessionRegistry(storage)  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]
    a, b, c = await asyncio.gather(*(registry.open("s1", "agent") for _ in range(3)))
    assert a is b is c
    assert registry.get("s1") is a
    assert storage.loads == 1


async def test_a_failed_open_lets_the_waiting_ones_try_again() -> None:
    storage = _SlowStorage(fail_first=True)
    registry = SessionRegistry(storage)  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]
    first, second = await asyncio.gather(
        registry.open("s1", "agent"), registry.open("s1", "agent"), return_exceptions=True
    )
    assert isinstance(first, OSError)
    assert registry.get("s1") is second


async def test_a_cancelled_open_lets_the_waiting_ones_try_again() -> None:
    registry = SessionRegistry(_SlowStorage())  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]
    leader = asyncio.create_task(registry.open("s1", "agent"))
    await asyncio.sleep(0)
    follower = asyncio.create_task(registry.open("s1", "agent"))
    await asyncio.sleep(0)
    leader.cancel()
    with pytest.raises(asyncio.CancelledError):
        await leader
    opened = await follower
    assert registry.get("s1") is opened
