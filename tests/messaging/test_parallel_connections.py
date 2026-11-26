"""``connect_to([...])`` sends in order by default; ``parallel=True`` sends at once."""

import asyncio

from agentpool import Agent


async def test_targets_run_in_order_by_default():
    order: list[str] = []

    async def first(prompt: str) -> str:
        await asyncio.sleep(0.05)
        order.append("first")
        return "1"

    async def second(prompt: str) -> str:
        order.append("second")
        return "2"

    async with Agent[None](model="test", name="source") as source:
        source.connect_to([first, second])
        await source.run("go")
    assert order == ["first", "second"]


async def test_parallel_targets_run_at_once():
    """Each target waits for the other to start: in order, the first would time out."""
    started = {"a": asyncio.Event(), "b": asyncio.Event()}
    done: list[str] = []

    def target(me: str, other: str):
        async def run(prompt: str) -> str:
            started[me].set()
            await asyncio.wait_for(started[other].wait(), timeout=5)
            done.append(me)
            return me

        run.__name__ = f"target_{me}"
        return run

    async with Agent[None](model="test", name="source") as source:
        source.connect_to([target("a", "b"), target("b", "a")], parallel=True)
        await asyncio.wait_for(source.run("go"), timeout=10)
    assert sorted(done) == ["a", "b"]
