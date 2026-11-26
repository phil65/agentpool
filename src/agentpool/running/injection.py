"""Agent injection utilities."""

import inspect
from types import UnionType
from typing import TYPE_CHECKING, Any, Union, get_args, get_origin

from agentpool.log import get_logger
from agentpool.utils.inspection import get_fn_qualname
from agentpool.utils.signatures import get_forwardref_signature, resolve_type_hints


if TYPE_CHECKING:
    from collections.abc import Callable

    from agentpool import AgentPool, MessageNode


logger = get_logger(__name__)


def is_node_type(typ: Any) -> bool:
    """Check if a type is or inherits from MessageNode (including generics and unions)."""
    from agentpool import MessageNode

    if typ is MessageNode:
        return True

    # For "real" types
    if isinstance(typ, type):
        return issubclass(typ, MessageNode)

    # For generic types (Agent[T], etc.) and unions containing a node type
    origin = get_origin(typ)
    if origin in (Union, UnionType):
        return any(is_node_type(arg) for arg in get_args(typ))
    if isinstance(origin, type):
        return issubclass(origin, MessageNode)

    return False


class NodeInjectionError(Exception):
    """Raised when agent injection fails."""


def inject_nodes[T, **P](
    func: Callable[P, T],
    pool: AgentPool,
    provided_kwargs: dict[str, Any],
) -> dict[str, MessageNode[Any, Any]]:
    """Get nodes to inject based on function signature."""
    import agentpool

    # node types imported only under TYPE_CHECKING are looked up in agentpool's namespace
    hints = resolve_type_hints(func, vars(agentpool))
    params = get_forwardref_signature(func).parameters
    logger.debug(
        "Injecting nodes", module=func.__module__, name=get_fn_qualname(func), type_hint=hints
    )

    nodes: dict[str, MessageNode[Any, Any]] = {}
    for name, param in params.items():
        if param.kind not in {
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        }:
            logger.debug("Skippin: wrong parameter kind", name=name, kind=param.kind)
            continue

        hint = hints.get(name)
        if hint is None:
            logger.debug("Skipping: no type hint", name=name)
            continue

        if not is_node_type(hint):
            logger.debug("Skipping. Not a node type.", name=name, hint=hint)
            continue

        logger.debug("Found node parameter", name=name)

        # Check for duplicate parameters
        if name in provided_kwargs and provided_kwargs[name] is not None:
            msg = (
                f"Cannot inject node {name!r}: Parameter already provided.\n"
                f"Remove the explicit argument or rename the parameter."
            )
            logger.error(msg)
            raise NodeInjectionError(msg)

        # Get node from pool
        if name not in pool.nodes:
            available = ", ".join(sorted(pool.nodes))
            msg = (
                f"No node named {name!r} found in pool.\n"
                f"Available nodes: {available}\n"
                f"Check your YAML configuration or node name."
            )
            logger.error(msg)
            raise NodeInjectionError(msg)

        nodes[name] = pool.nodes[name]
        logger.debug("Injecting node", node=nodes[name], name=name)

    logger.debug("Injection complete.", nodes=sorted(nodes))
    return nodes
