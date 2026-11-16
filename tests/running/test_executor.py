from __future__ import annotations

import time
from typing import Any

import anyio
import pytest

from agentpool import Agent  # noqa: TC001
from agentpool.running.discovery import node_function
from agentpool.running.executor import (
    ExecutionError,
    _group_parallel,
    _sort_functions,
    execute_functions,
)


async def test_function_sorting():
    """Test function sorting by dependencies and definition order."""

    @node_function
    async def first(): ...

    @node_function(depends_on="first")
    async def second(): ...

    @node_function(depends_on="second")
    async def third(): ...

    funcs = [second, third, first]
    sorted_funcs = _sort_functions([f._node_function for f in funcs])
    assert [f.name for f in sorted_funcs] == ["first", "second", "third"]


async def test_circular_dependency():
    """Test detection of circular dependencies."""

    @node_function(depends_on="b")
    async def a(): ...

    @node_function(depends_on="a")
    async def b(): ...

    with pytest.raises(ValueError, match="Circular dependency"):
        _sort_functions([a._node_function, b._node_function])


async def test_parallel_grouping():
    """Test grouping of parallel functions."""

    @node_function
    async def first(): ...

    @node_function(depends_on="first")
    async def second_a(): ...

    @node_function(depends_on="first")
    async def second_b(): ...

    @node_function(depends_on=["second_a", "second_b"])
    async def third(): ...

    sorted_funcs = _sort_functions([f._node_function for f in [first, second_a, second_b, third]])
    groups = _group_parallel(sorted_funcs)

    assert len(groups) == 3
    assert [f.name for f in groups[0]] == ["first"]
    assert {f.name for f in groups[1]} == {"second_a", "second_b"}
    assert [f.name for f in groups[2]] == ["third"]


async def test_execution_order(pool):
    """Test function execution order."""
    executed = []

    @node_function
    async def first(agent1: Agent[None]):
        executed.append("first")
        return "first"

    @node_function(depends_on="first")
    async def second(agent1: Agent[None], first: str):  # Gets result from first:
        assert first == "first"
        executed.append("second")
        return "second"

    funcs = [second._node_function, first._node_function]  # type: ignore
    results = await execute_functions(funcs, pool)

    assert executed == ["first", "second"]
    assert results == {"first": "first", "second": "second"}


async def test_parallel_execution(pool):
    """Test parallel function execution."""
    # Track execution times
    start_times: dict[str, float] = {}
    end_times: dict[str, float] = {}

    @node_function
    async def first(agent1: Agent[None]):
        start_times["first"] = time.perf_counter()
        await anyio.sleep(0.1)
        end_times["first"] = time.perf_counter()
        return "first"

    @node_function(depends_on="first")
    async def second_a(agent1: Agent[None], first: str):
        start_times["second_a"] = time.perf_counter()
        await anyio.sleep(0.1)
        end_times["second_a"] = time.perf_counter()
        return "second_a"

    @node_function(depends_on="first")
    async def second_b(agent1: Agent[None], first: str):
        start_times["second_b"] = time.perf_counter()
        await anyio.sleep(0.1)
        end_times["second_b"] = time.perf_counter()
        return "second_b"

    funcs = [f._node_function for f in [first, second_a, second_b]]

    await execute_functions(funcs, pool, parallel=True)

    # Add small epsilon for float comparison
    epsilon = 0.001  # 1ms buffer

    # First should complete before second_a/second_b start
    assert end_times["first"] + epsilon < start_times["second_a"]
    assert end_times["first"] + epsilon < start_times["second_b"]

    # second_a and second_b should overlap
    assert start_times["second_a"] < end_times["second_b"]
    assert start_times["second_b"] < end_times["second_a"]


async def test_input_handling(pool):
    """Test handling of inputs and defaults."""

    @node_function
    async def func(agent1: Agent[None], required: str, optional: int = 42):
        return f"{required}:{optional}"

    # With default
    inputs: dict[str, Any] = {"required": "test"}
    result = await execute_functions([func._node_function], pool, inputs=inputs)  # type: ignore
    assert result["func"] == "test:42"

    # With override
    inputs = {"required": "test", "optional": 100}
    result = await execute_functions([func._node_function], pool, inputs=inputs)  # type: ignore
    assert result["func"] == "test:100"

    # Missing required
    with pytest.raises(ExecutionError):
        await execute_functions([func._node_function], pool)  # type: ignore
