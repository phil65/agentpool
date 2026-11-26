"""Automatic agent function execution."""

import asyncio
import inspect
from typing import TYPE_CHECKING, Any

from agentpool.running.executor import discover_functions, execute_functions


if TYPE_CHECKING:
    from upathtools import JoinablePathLike

    from agentpool.models.manifest import AgentsManifest


async def run_nodes_async(
    config: JoinablePathLike | AgentsManifest,
    *,
    module: str | None = None,
    functions: list[str] | None = None,
    inputs: dict[str, Any] | None = None,
    parallel: bool = False,
    max_concurrency: int | None = None,
) -> dict[str, Any]:
    """Execute node functions with dependency handling.

    Args:
        config: Node configuration (path or manifest)
        module: Optional module to discover functions from
        functions: Optional list of function names to run (auto-discovers if None)
        inputs: Optional input values for function parameters
        parallel: Whether to run independent functions in parallel
        max_concurrency: Most functions running at once in parallel mode (None: no limit)

    Returns:
        Dict mapping function names to their results

    Example:
        ```python
        @node_function
        async def analyze(analyzer: Agent) -> str:
            return await analyzer.run("...")

        results = await run_nodes_async("agents.yml")
        print(results["analyze"])
        ```
    """
    from agentpool import AgentPool

    # Find functions to run
    if module:
        discovered = discover_functions(module)
    else:
        frame = inspect.currentframe()
        while frame:
            if frame.f_globals.get("__name__") != __name__:
                break
            frame = frame.f_back
        if not frame:
            raise RuntimeError("Could not determine calling module")
        discovered = discover_functions(frame.f_globals["__file__"])

    if functions:
        discovered = [f for f in discovered if f.name in functions]

    # Run with pool
    async with AgentPool(config) as pool:
        return await execute_functions(
            discovered, pool, inputs=inputs, parallel=parallel, max_concurrency=max_concurrency
        )


def run_nodes(config: JoinablePathLike | AgentsManifest, **kwargs: Any) -> dict[str, Any]:
    """Run node functions synchronously.

    Convenience wrapper around run_nodes_async for sync contexts.
    See run_nodes_async for full documentation.

    Args:
        config: Agent configuration path
        **kwargs: Arguments to pass to run_nodes_async

    Returns:
        Dict mapping function names to their results
    """
    return asyncio.run(run_nodes_async(config, **kwargs))
