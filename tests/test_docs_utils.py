"""Tests for the docs example runner `agentpool.docs.utils.run`."""

import asyncio

import pytest

from agentpool.docs.utils import run


async def _value() -> str:
    return "done"


async def _fail() -> None:
    raise RuntimeError("boom")


def test_run_without_loop_returns_result() -> None:
    assert run(_value()) == "done"


def test_run_does_not_retry_on_coroutine_runtime_error() -> None:
    """A RuntimeError from the coroutine itself propagates unchanged."""
    with pytest.raises(RuntimeError, match="boom"):
        run(_fail())


def test_run_inside_running_loop_raises_clear_error() -> None:
    async def main() -> None:
        coro = _value()
        with pytest.raises(RuntimeError, match="await the coroutine instead"):
            run(coro)
        assert coro.cr_frame is None  # closed, no "never awaited" warning

    asyncio.run(main())
