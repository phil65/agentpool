"""Function discovery and metadata handling."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, cast, overload

from agentpool.log import get_logger
from agentpool.utils.inspection import get_fn_name
from agentpool.utils.signatures import get_forwardref_signature


if TYPE_CHECKING:
    from collections.abc import Sequence

    from agentpool.common_types import AnyCallable

logger = get_logger(__name__)


@dataclass
class NodeFunction:
    """Metadata for a function that uses nodes."""

    func: AnyCallable
    """The actual function to execute."""

    depends_on: list[str] = field(default_factory=list)
    """Names of functions this one depends on."""

    deps: Any = None
    """Node dependencies."""

    default_inputs: dict[str, Any] = field(default_factory=dict)
    """Default parameter values."""

    name: str = field(init=False)
    """Function name (from function.__name__)."""

    def __post_init__(self) -> None:
        """Set name and validate dependencies."""
        self.name = get_fn_name(self.func)
        # Extract default inputs from function signature

        sig = get_forwardref_signature(self.func)
        self.default_inputs = {
            name: param.default
            for name, param in sig.parameters.items()
            if param.default is not param.empty
        }
        logger.debug("Registered node function", name=self.name, deps=self.depends_on)


class MarkedNodeFunction(Protocol):
    """A callable that has been marked with ``@node_function``."""

    _node_function: NodeFunction

    def __call__(self, *args: Any, **kwargs: Any) -> Any: ...


type DependsOn = str | Sequence[str | Callable[..., Any]] | Callable[..., Any] | None


@overload
def node_function(
    func: Callable[..., Any],
    *,
    deps: Any | None = None,
    depends_on: DependsOn = None,
) -> MarkedNodeFunction: ...


@overload
def node_function(
    func: None = None,
    *,
    deps: Any | None = None,
    depends_on: DependsOn = None,
) -> Callable[[Callable[..., Any]], MarkedNodeFunction]: ...


def node_function(
    func: Callable[..., Any] | None = None,
    *,
    deps: Any | None = None,
    depends_on: DependsOn = None,
) -> MarkedNodeFunction | Callable[[Callable[..., Any]], MarkedNodeFunction]:
    """Mark a function for automatic node execution.

    Can be used as simple decorator or with arguments:

    @node_function
    async def func(): ...

    @node_function(order=1, depends_on="other_func")
    async def func(): ...

    Args:
        func: Function to mark
        deps: Dependencies to inject into all Agent parameters
        depends_on: Names of functions this one depends on

    Returns:
        Decorated function
    """

    def decorator(func: Callable[..., Any]) -> MarkedNodeFunction:
        match depends_on:
            case None:
                depends_on_ = []
            case str():
                depends_on_ = [depends_on]
            case Callable():
                depends_on_ = [get_fn_name(depends_on)]
            case [*items]:
                depends_on_ = [get_fn_name(i) if callable(i) else str(i) for i in items]
            case _:
                raise ValueError(f"Invalid depends_on: {depends_on}")
        # TODO: we still need to inject the deps in execution part.
        metadata = NodeFunction(func=func, depends_on=depends_on_ or [], deps=deps)
        # Functions accept arbitrary attributes; the Protocol describes the marked result.
        marked = cast(MarkedNodeFunction, func)
        marked._node_function = metadata
        return marked

    return decorator(func) if func is not None else decorator
