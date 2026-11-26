"""Function execution management."""

import asyncio
import importlib.util
import inspect
from pathlib import Path
import sys
from types import UnionType
from typing import TYPE_CHECKING, Any, Literal, Union, get_args, get_origin

import anyio

import agentpool
from agentpool.log import get_logger
from agentpool.running import with_nodes
from agentpool.utils.signatures import resolve_type_hints


if TYPE_CHECKING:
    import os

    from agentpool import AgentPool
    from agentpool.running.discovery import NodeFunction


logger = get_logger(__name__)


class ExecutionError(Exception):
    """Raised when function execution fails."""


def _validate_path(path: str | os.PathLike[str]) -> Path:
    if str(path) == "__main__":
        # Get the actual file being run

        path = sys.modules["__main__"].__file__  # type: ignore
        if not path:
            raise ValueError("Could not determine main module file")
    path_obj = Path(path)
    if not path_obj.exists():
        raise ValueError(f"Module not found: {path}")
    return path_obj


def discover_functions(path: str | os.PathLike[str]) -> list[NodeFunction]:
    """Find all node functions in a module.

    Args:
        path: Path to Python module file

    Returns:
        List of discovered node functions

    Raises:
        ImportError: If module cannot be imported
        ValueError: If path is invalid
    """
    path_obj = _validate_path(path)
    # Import module
    spec = importlib.util.spec_from_file_location(path_obj.stem, path_obj)
    if not spec or not spec.loader:
        raise ImportError(f"Could not load module: {path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # Find decorated functions
    return [i._node_function for _, i in inspect.getmembers(module) if hasattr(i, "_node_function")]


def _sort_functions(functions: list[NodeFunction]) -> list[NodeFunction]:
    """Sort functions by order and dependencies.

    Args:
        functions: Functions to sort

    Returns:
        Sorted list of functions

    Raises:
        ValueError: If there are circular dependencies
    """
    # First by explicit order
    ordered = sorted(functions, key=lambda f: f.name)
    # Then resolve dependencies
    result = []
    seen = set()
    in_progress = set()

    def add_function(func: NodeFunction) -> None:
        if func.name in seen:
            return
        if func.name in in_progress:
            raise ValueError(f"Circular dependency detected: {func.name}")

        in_progress.add(func.name)
        # Add dependencies first
        for dep in func.depends_on:
            dep_fn = next((f for f in ordered if f.name == dep), None)
            if not dep_fn:
                raise ValueError(f"Missing dependency {dep} for {func.name}")
            add_function(dep_fn)

        result.append(func)
        in_progress.remove(func.name)
        seen.add(func.name)

    for func in ordered:
        add_function(func)

    return result


def _group_parallel(
    sorted_funcs: list[NodeFunction],
) -> list[list[NodeFunction]]:
    """Group functions that can run in parallel."""
    if not sorted_funcs:
        return []

    # Group by dependency signature
    by_deps: dict[tuple[str, ...], list[NodeFunction]] = {}

    for func in sorted_funcs:
        # Use tuple of sorted deps as key for consistent grouping
        key = tuple(sorted(func.depends_on))
        if key not in by_deps:
            by_deps[key] = []
        by_deps[key].append(func)

    # Convert to list of groups, maintaining order
    groups = []
    seen_funcs: set[str] = set()

    for func in sorted_funcs:
        key = tuple(sorted(func.depends_on))
        if func.name not in seen_funcs:
            group = by_deps[key]
            groups.append(group)
            seen_funcs.update(f.name for f in group)
    names = [[f.name for f in g] for g in groups]
    logger.debug("Grouped functions into groups", num_funcs=len(sorted_funcs), group_names=names)
    return groups


async def execute_single(
    func: NodeFunction,
    pool: AgentPool,
    available_results: dict[str, Any],
    inputs: dict[str, Any] | None = None,
) -> tuple[str, Any]:
    """Execute a single function.

    Args:
        func: Function to execute
        pool: Agent pool for injection
        available_results: Results from previous functions
        inputs: Optional input overrides

    Returns:
        Tuple of (function name, result)

    Raises:
        ExecutionError: If execution fails
    """
    logger.debug("Executing function", name=func.name)
    try:
        kwargs = func.default_inputs.copy()
        if inputs:
            kwargs.update(inputs)

        # Get type hints for the function
        hints = resolve_type_hints(func.func, vars(agentpool))

        # Add and validate dependency results
        for dep in func.depends_on:
            if dep not in available_results:
                raise ExecutionError(f"Missing result from {dep}")  # noqa: TRY301

            value = available_results[dep]
            if dep in hints:  # If parameter is type hinted
                _validate_value_type(value, hints[dep], func.name, dep)
            kwargs[dep] = value

        # Execute with node injection
        wrapped = with_nodes(pool)(func.func)
        result = await wrapped(**kwargs)

        # Validate return type if hinted
        if "return" in hints:
            _validate_value_type(result, hints["return"], func.name, "return")
    except Exception as e:
        raise ExecutionError(f"Error executing {func.name}: {e}") from e
    else:
        return func.name, result


def _validate_dependency_types(functions: list[NodeFunction]) -> None:
    """Validate that dependency types match return types."""
    # Get return types for all functions
    return_types = {}
    for func in functions:
        hints = resolve_type_hints(func.func, vars(agentpool))
        if "return" in hints:
            return_types[func.name] = hints["return"]

    # Check each function's dependencies
    for func in functions:
        hints = resolve_type_hints(func.func, vars(agentpool))
        for dep in func.depends_on:
            # Only validate if both dependency return type AND parameter are typed
            if dep in hints and dep in return_types:
                expected_type = hints[dep]
                provided_type = return_types[dep]
                if Any not in (expected_type, provided_type) and expected_type != provided_type:
                    msg = (
                        f"Type mismatch in {func.name}: "
                        f"dependency {dep!r} is typed as {expected_type}, "
                        f"but {dep} returns {provided_type}"
                    )
                    raise TypeError(msg)


def _matches_type(value: Any, expected: Any) -> bool:
    """Whether ``value`` is of type ``expected``, checking generics by their origin only."""
    if expected is Any:
        return True
    origin = get_origin(expected)
    if origin in (Union, UnionType):
        return any(_matches_type(value, arg) for arg in get_args(expected))
    if origin is Literal:
        return value in get_args(expected)
    check = origin or expected
    if expected is None or check is type(None):
        return value is None
    return not isinstance(check, type) or isinstance(value, check)  # Unknown: not checked


def _validate_value_type(value: Any, expected_type: Any, func_name: str, param_name: str) -> None:
    """Validate that a value matches its expected type."""
    if not _matches_type(value, expected_type):
        msg = (
            f"Type error in {func_name}: parameter {param_name!r} "
            f"expected {expected_type!r}, got {type(value).__name__}"
        )
        raise TypeError(msg)


async def execute_functions(
    functions: list[NodeFunction],
    pool: AgentPool,
    inputs: dict[str, Any] | None = None,
    parallel: bool = False,
    max_concurrency: int | None = None,
) -> dict[str, Any]:
    """Execute discovered functions in the right order.

    Args:
        functions: Functions to execute
        pool: Agent pool for injection
        inputs: Optional input overrides
        parallel: Run functions whose dependencies are met concurrently
        max_concurrency: Most functions running at once in parallel mode (None: no limit)
    """
    if max_concurrency is not None and max_concurrency < 1:
        msg = f"max_concurrency must be at least 1, got {max_concurrency}"
        raise ValueError(msg)
    msg = "Executing functions"
    logger.info(msg, num_functions=len(functions), parallel=parallel)
    results: dict[str, Any] = {}
    # Sort by order/dependencies
    sorted_funcs = _sort_functions(functions)
    _validate_dependency_types(sorted_funcs)

    if parallel:
        # Group functions that can run in parallel
        groups = _group_parallel(sorted_funcs)
        semaphore = asyncio.Semaphore(max_concurrency) if max_concurrency else None

        async def run(func: NodeFunction) -> tuple[str, Any]:
            if semaphore is None:
                return await execute_single(func, pool, results, inputs)
            async with semaphore:
                return await execute_single(func, pool, results, inputs)

        for i, group in enumerate(groups):
            logger.debug(
                "Executing parallel group",
                group=i + 1,
                num_groups=len(groups),
                names=[f.name for f in group],
            )

            # Ensure previous results are available
            logger.debug("Available results", results=sorted(results))
            # Run group in parallel
            group_results = await asyncio.gather(*(run(func) for func in group))
            # Update results after group completes
            results.update(dict(group_results))
            logger.debug("Group complete", num=i + 1)
            # Add small delay between groups to ensure timing separation
            if i < len(groups) - 1:
                await anyio.sleep(0.02)  # 20ms between groups
    else:
        # Execute sequentially
        for func in sorted_funcs:
            name, result = await execute_single(func, pool, results, inputs)
            results[name] = result

    return results
