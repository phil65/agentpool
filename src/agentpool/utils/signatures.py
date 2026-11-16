"""Signature utils."""

from __future__ import annotations

from collections.abc import Awaitable
import inspect
from typing import TYPE_CHECKING, Any, cast, get_args, get_origin

from pydantic._internal import _typing_extra

from agentpool.log import get_logger


if TYPE_CHECKING:
    from collections.abc import Callable

    from schemez.functionschema import ToolParameters


logger = get_logger(__name__)


def get_return_type(
    fn: Callable[..., Any],
    *,
    unwrap_awaitable: bool = True,
) -> type | None:
    """Get the return type of a function, optionally unwrapping Awaitable.

    Args:
        fn: Function to inspect
        unwrap_awaitable: If True, unwrap Awaitable[T] -> T for async functions

    Returns:
        The return type annotation, or None if not annotated

    Example:
        async def foo() -> Awaitable[int]: ...
        get_return_type(foo)  # Returns: int
        get_return_type(foo, unwrap_awaitable=False)  # Returns: Awaitable[int]
    """
    hints = _typing_extra.get_function_type_hints(fn)
    return_type = hints.get("return")

    if not return_type:
        return None

    if unwrap_awaitable and get_origin(return_type) is Awaitable:
        args = get_args(return_type)
        return cast(type, args[0]) if args else None

    return cast(type, return_type)


_CONTEXT_TYPE_NAMES = frozenset({
    "NodeContext",
    "AgentContext",
    "RunContext",
})


def is_context_type(annotation: Any) -> bool:
    """Check if an annotation is a context type (for auto-injection/hiding from agent).

    This detects context types that should be hidden from the agent's view of tool
    parameters. Both our own context types (AgentContext/NodeContext) and pydantic-ai's
    RunContext are detected.

    Uses name-based detection to avoid importing context classes, which would trigger
    Pydantic schema generation for their fields.

    Handles:
    - Direct AgentContext/NodeContext/RunContext references
    - String annotations (forward references from `from __future__ import annotations`)
    - Generic forms like AgentContext[SomeDeps], RunContext[SomeDeps]
    - Union types containing context types
    """
    if annotation is None or annotation is inspect.Parameter.empty:
        return False

    # Handle string annotations (forward references from `from __future__ import annotations`)
    # Check if the string matches or starts with a known context type name
    if isinstance(annotation, str):
        # Handle plain names like "AgentContext" and generics like "AgentContext[Deps]"
        base_name = annotation.split("[")[0].strip()
        return base_name in _CONTEXT_TYPE_NAMES

    # Check direct class match by name
    if isinstance(annotation, type) and annotation.__name__ in _CONTEXT_TYPE_NAMES:
        return True

    # Check generic origin (e.g., AgentContext[SomeDeps], RunContext[SomeDeps])
    origin = get_origin(annotation)
    if origin is not None:
        if isinstance(origin, type) and origin.__name__ in _CONTEXT_TYPE_NAMES:
            return True
        # Handle Union types
        if origin is type(None) or str(origin) in ("typing.Union", "types.UnionType"):
            args = get_args(annotation)
            return any(is_context_type(arg) for arg in args)

    return False


def get_params_matching_predicate(
    fn: Callable[..., Any],
    predicate: Callable[[inspect.Parameter], bool],
) -> set[str]:
    """Get names of function parameters matching a predicate.

    Args:
        fn: Function to inspect
        predicate: Function that takes a Parameter and returns True if it matches

    Returns:
        Set of parameter names that match the predicate
    """
    sig = inspect.signature(fn)
    return {name for name, param in sig.parameters.items() if predicate(param)}


def filter_schema_params(schema: ToolParameters, params_to_remove: set[str]) -> ToolParameters:
    """Filter parameters from a JSON schema.

    Creates a copy of the schema with specified parameters removed from
    'properties' and 'required' fields.

    Args:
        schema: JSON schema with 'properties' dict
        params_to_remove: Set of parameter names to remove

    Returns:
        New schema dict with parameters filtered out
    """
    if not params_to_remove:
        return schema

    result: ToolParameters = schema.copy()
    if "properties" in result:
        result["properties"] = {
            k: v for k, v in result["properties"].items() if k not in params_to_remove
        }
    if "required" in result:
        result["required"] = [r for r in result["required"] if r not in params_to_remove]
    return result


def create_modified_signature(
    fn_or_sig: Callable[..., Any] | inspect.Signature,
    *,
    remove: str | list[str] | None = None,
    inject: dict[str, type] | None = None,
) -> inspect.Signature:
    """Create a modified signature by removing specified parameters / injecting new ones.

    Args:
        fn_or_sig: The function or signature to modify.
        remove: The parameter(s) to remove.
        inject: The parameter(s) to inject.

    Returns:
        The modified signature.
    """
    sig = fn_or_sig if isinstance(fn_or_sig, inspect.Signature) else inspect.signature(fn_or_sig)
    rem_keys = [remove] if isinstance(remove, str) else remove or []
    new_params = [p for p in sig.parameters.values() if p.name not in rem_keys]
    if inject:
        injected_params = []
        for k, v in inject.items():
            injected_params.append(
                inspect.Parameter(k, inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=v)
            )
        new_params = injected_params + new_params
    return sig.replace(parameters=new_params)


def modify_signature(
    fn: Callable[..., Any],
    *,
    remove: str | list[str] | None = None,
    inject: dict[str, type] | None = None,
) -> None:
    new_sig = create_modified_signature(fn, remove=remove, inject=inject)
    update_signature(fn, new_sig)


def update_signature(fn: Callable[..., Any], signature: inspect.Signature) -> None:
    """Update function signature and annotations.

    Note: Setting __annotations__ destroys __annotate__ in Python 3.14+ (PEP 649).
    Callers using functools.wraps should restore __annotations__ from the original
    function after calling this function.
    """
    fn.__signature__ = signature  # type: ignore
    fn.__annotations__ = {
        name: param.annotation for name, param in signature.parameters.items()
    } | {"return": signature.return_annotation}


def create_bound_callable(  # noqa: PLR0915
    original_callable: Callable[..., Any],
    by_name: dict[str, Any] | None = None,
    by_type: dict[type, Any] | None = None,
    bind_kwargs: bool = False,
) -> Callable[..., Awaitable[Any]]:
    """Create a wrapper that pre-binds parameters by name or type.

    Parameters are bound by their position in the function signature. Only
    positional and positional-or-keyword parameters can be bound by default.
    If bind_kwargs=True, keyword-only parameters can also be bound using the
    same by_name/by_type logic. Binding by name takes priority over binding by type.

    Args:
        original_callable: The original callable that may need parameter binding
        by_name: Parameters to bind by exact parameter name
        by_type: Parameters to bind by parameter type annotation
        bind_kwargs: Whether to also bind keyword-only parameters

    Returns:
        New callable with parameters pre-bound and proper introspection

    Raises:
        ValueError: If the callable's signature cannot be inspected
    """
    try:
        sig = inspect.signature(original_callable)
    except (ValueError, TypeError) as e:
        msg = f"Cannot inspect signature of {original_callable}. Ensure callable is inspectable."
        raise ValueError(msg) from e

    # Build position-to-value mapping for positional binding
    context_values = {}
    # Build name-to-value mapping for keyword-only binding
    kwarg_bindings = {}

    for i, param in enumerate(sig.parameters.values()):
        # Bind positional and positional-or-keyword parameters
        if param.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ):
            # Bind by name first (higher priority)
            if by_name and param.name in by_name:
                context_values[i] = by_name[param.name]
            # Then bind by type if not already bound
            elif by_type and _find_matching_type(param.annotation, by_type) is not None:
                context_values[i] = _find_matching_type(param.annotation, by_type)
        # Bind keyword-only parameters if enabled
        elif bind_kwargs and param.kind == inspect.Parameter.KEYWORD_ONLY:
            # Bind by name first (higher priority)
            if by_name and param.name in by_name:
                kwarg_bindings[param.name] = by_name[param.name]
            # Then bind by type if not already bound
            elif by_type and _find_matching_type(param.annotation, by_type) is not None:
                kwarg_bindings[param.name] = _find_matching_type(param.annotation, by_type)

    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        # Filter out kwargs that would conflict with bound parameters
        param_names = list(sig.parameters.keys())
        bound_param_names = {param_names[i] for i in context_values}
        bound_kwarg_names = set(kwarg_bindings.keys())
        filtered_kwargs = {
            k: v
            for k, v in kwargs.items()
            if k not in bound_param_names and k not in bound_kwarg_names
        }

        # Add bound keyword-only parameters
        filtered_kwargs.update(kwarg_bindings)

        # Build new_args with context values at correct positions
        new_args = []
        arg_index = 0
        for param_index in range(len(sig.parameters)):
            if param_index in context_values:
                new_args.append(context_values[param_index])
            elif arg_index < len(args):
                new_args.append(args[arg_index])
                arg_index += 1

        # Add any remaining positional args
        if arg_index < len(args):
            new_args.extend(args[arg_index:])

        if inspect.iscoroutinefunction(original_callable):
            return await original_callable(*new_args, **filtered_kwargs)
        return original_callable(*new_args, **filtered_kwargs)

    # Preserve introspection attributes
    wrapper.__name__ = getattr(original_callable, "__name__", "wrapper")
    wrapper.__doc__ = getattr(original_callable, "__doc__", None)
    wrapper.__module__ = getattr(original_callable, "__module__", None)  # type: ignore[assignment]  # ty:ignore[invalid-assignment]
    wrapper.__wrapped__ = original_callable  # type: ignore[attr-defined]  # ty:ignore[missing-attribute]
    wrapper.__agentpool_wrapped__ = original_callable  # type: ignore[attr-defined]  # ty:ignore[missing-attribute]

    # Create modified signature without context parameters
    try:
        params = list(sig.parameters.values())
        # Remove parameters at context positions and bound kwargs
        context_positions = set(context_values.keys())
        bound_kwarg_names = set(kwarg_bindings.keys())
        new_params = [
            param
            for i, param in enumerate(params)
            if i not in context_positions and param.name not in bound_kwarg_names
        ]
        new_sig = sig.replace(parameters=new_params)
        wrapper.__signature__ = new_sig  # type: ignore[attr-defined]  # ty:ignore[missing-attribute]
        wrapper.__annotations__ = {
            name: param.annotation for name, param in new_sig.parameters.items()
        }
        if sig.return_annotation != inspect.Signature.empty:
            wrapper.__annotations__["return"] = sig.return_annotation

    except (ValueError, TypeError):
        logger.debug("Failed to update wrapper signature", original=original_callable)

    return wrapper


def _find_matching_type(param_annotation: Any, by_type: dict[type, Any]) -> Any | None:
    """Find a matching type binding for the given parameter annotation.

    Supports exact matching and generic origin matching.

    Args:
        param_annotation: The parameter's type annotation
        by_type: Dictionary of type bindings

    Returns:
        The bound value if a match is found, None otherwise
    """
    # First try exact match
    if param_annotation in by_type:
        return by_type[param_annotation]

    # Then try origin type matching for generics
    param_origin = get_origin(param_annotation)
    if param_origin is not None and param_origin in by_type:
        return by_type[param_origin]

    return None
