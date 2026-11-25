"""Decorators for agent injection and execution."""

from __future__ import annotations

from functools import wraps
import inspect
from typing import TYPE_CHECKING, overload

from agentpool.running.injection import inject_nodes


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from agentpool import AgentPool


@overload
def with_nodes[T, **P](
    pool: AgentPool,
) -> Callable[[Callable[P, Awaitable[T]]], Callable[P, Awaitable[T]]]: ...


@overload
def with_nodes[T, **P](
    pool: AgentPool,
    func: Callable[P, Awaitable[T]],
) -> Callable[P, Awaitable[T]]: ...


def with_nodes[T, **P](
    pool: AgentPool,
    func: Callable[P, Awaitable[T]] | None = None,
) -> Callable[P, Awaitable[T]] | Callable[[Callable[P, Awaitable[T]]], Callable[P, Awaitable[T]]]:
    """Inject nodes into function parameters."""

    def decorator(func: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            # Convert args to kwargs for injection check
            sig = inspect.signature(func)
            bound_args = sig.bind_partial(*args)
            all_kwargs = {**bound_args.arguments, **kwargs}
            # Get needed nodes
            nodes = inject_nodes(func, pool, all_kwargs)
            # The given arguments (positional ones by name) and the injected nodes
            final_kwargs = {**bound_args.arguments, **nodes, **kwargs}
            # Convert back to args/kwargs using signature
            bound = sig.bind(**final_kwargs)
            bound.apply_defaults()
            # Call with proper args/kwargs
            return await func(*bound.args, **bound.kwargs)

        return wrapper

    return decorator(func) if func else decorator
