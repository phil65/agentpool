import annotationlib
import asyncio
from collections.abc import Awaitable, Coroutine, Sequence
from dataclasses import fields
import functools
from importlib.util import find_spec
import inspect
from types import UnionType
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    ForwardRef,
    Literal,
    TypeAliasType,
    TypeGuard,
    Union,
    get_args,
    get_origin,
    get_type_hints,
    overload,
)

from agentpool.utils.signatures import (
    annotation_names,
    get_forwardref_signature,
    resolve_type_hints,
)


if TYPE_CHECKING:
    from collections.abc import Callable

    from agentpool.agents import AgentContext


PACKAGE_NAME = "agentpool"


def dataclasses_no_defaults_repr(self: Any) -> str:
    """Exclude fields with values equal to the field default."""
    kv_pairs = (
        f"{f.name}={getattr(self, f.name)!r}"
        for f in fields(self)
        if f.repr and getattr(self, f.name) != f.default
    )
    return f"{self.__class__.__qualname__}({', '.join(kv_pairs)})"


@overload
async def execute[T](
    func: Callable[..., Awaitable[T]],
    *args: Any,
    use_thread: bool = False,
    **kwargs: Any,
) -> T: ...


@overload
async def execute[T](
    func: Callable[..., T | Awaitable[T]],
    *args: Any,
    use_thread: bool = False,
    **kwargs: Any,
) -> T: ...


async def execute[T](
    func: Callable[..., T | Awaitable[T]],
    *args: Any,
    use_thread: bool = False,
    **kwargs: Any,
) -> T:
    """Execute callable, handling both sync and async cases."""
    if inspect.iscoroutinefunction(func):
        return await func(*args, **kwargs)  # type: ignore[no-any-return]

    if use_thread:
        result = await asyncio.to_thread(func, *args, **kwargs)
    else:
        result = func(*args, **kwargs)

    if inspect.iscoroutine(result) or inspect.isawaitable(result):
        return await result

    return result


def get_argument_key(
    func: Callable[..., Any],
    arg_type: type | str | UnionType | Sequence[type | str | UnionType],
    include_return: bool = False,
) -> Literal[False] | str:
    """Check if function has any argument of specified type(s) and return the key.

    Args:
        func: Function to check
        arg_type: Type(s) to look for. Can be:
            - Single type (int, str, etc)
            - Union type (int | str)
            - Type name as string
            - Sequence of the above
        include_return: Whether to also check return type annotation

    Examples:
        >>> def func(x: int | str, y: list[int]): ...
        >>> get_argument_key(func, int | str)  # Returns 'x'
        >>> get_argument_key(func, int)        # Returns 'x'
        >>> get_argument_key(func, list)       # Returns 'y'
        >>> get_argument_key(func, float)      # Returns False
        >>> get_argument_key(func, (int, str)) # Returns 'x'

    Returns:
        Parameter name (str) if a matching argument is found, False otherwise.
        Only checks the origin type for generics, not type arguments
        (e.g., list[int] matches 'list', but does not match 'int').
        Annotations naming something not available at runtime (imported under
        ``TYPE_CHECKING``) are matched by name.
    """
    # Convert target type(s) to set of normalized strings
    if isinstance(arg_type, Sequence) and not isinstance(arg_type, str | bytes):
        target_types = {_type_to_string(t) for t in arg_type}
    else:
        target_types = {_type_to_string(arg_type)}

    # Get type hints including return type if requested (unresolvable names as ForwardRef)
    hints = get_type_hints(func, include_extras=True, format=annotationlib.Format.FORWARDREF)
    if not include_return:
        hints.pop("return", None)

    # Check each parameter's type annotation
    for key, param_type_ in hints.items():
        # Handle type aliases
        param_type = (
            param_type_.__value__ if isinstance(param_type_, TypeAliasType) else param_type_
        )
        # Check for direct match
        if _type_to_string(param_type) in target_types:
            return key

        # Not available at runtime: compare the names (`X | None`, `X[T]` give "X")
        if isinstance(param_type, ForwardRef):
            if not annotation_names(param_type).isdisjoint(target_types):
                return key
            continue

        # Handle Union types (both | and Union[...])
        origin = get_origin(param_type)
        if origin is Union or origin is UnionType:
            union_members = get_args(param_type)
            # Check each union member
            if any(_type_to_string(t) in target_types for t in union_members):
                return key
            # Also check if the complete union type matches
            if _type_to_string(param_type) in target_types:
                return key

        # Handle generic types (list[str], dict[str, int], etc)
        # Only check the origin type (e.g., list), not the arguments
        # This avoids matching nested contexts like RunContext[AgentContext]
        if origin is not None and _type_to_string(origin) in target_types:
            return key

        # if origin is not None:
        #     # Check if the generic type (e.g., list) matches
        #     if _type_to_string(origin) in target_types:
        #         return key
        #     # Check type arguments (e.g., str in list[str])
        #     args = get_args(param_type)
        #     if any(_type_to_string(arg) in target_types for arg in args):
        #         return key

    return False


def is_async_callable(obj: Any) -> Any:
    """Correctly check if a callable is async.

    This function was copied from Starlette:
    https://github.com/encode/starlette/blob/78da9b9e218ab289117df7d62aee200ed4c59617/starlette/_utils.py#L36-L40
    """
    while isinstance(obj, functools.partial):
        obj = obj.func

    return inspect.iscoroutinefunction(obj) or (
        callable(obj) and inspect.iscoroutinefunction(obj.__call__)
    )


def _type_to_string(type_hint: Any) -> str:
    """Convert type to normalized string representation for comparison."""
    match type_hint:
        case str():
            return type_hint
        case type():
            return type_hint.__name__
        case TypeAliasType():
            return _type_to_string(type_hint.__value__)
        case UnionType():
            args = get_args(type_hint)
            args_str = ", ".join(_type_to_string(t) for t in args)
            return f"Union[{args_str}]"
        case _:
            return str(type_hint)


_COROUTINE_ARG_COUNT = 3


def _matches_return_type(return_type: Any, expected_type: type[Any]) -> bool:
    """Check whether a resolved return type annotation matches expected_type."""
    while isinstance(return_type, TypeAliasType):
        return_type = return_type.__value__

    origin = get_origin(return_type)
    if return_type is expected_type or (origin is not None and origin is expected_type):
        return True

    args = get_args(return_type)
    if origin is Annotated:
        return bool(args) and _matches_return_type(args[0], expected_type)
    if origin is Union or origin is UnionType:
        return any(_matches_return_type(arg, expected_type) for arg in args)
    if origin is Coroutine:
        return len(args) == _COROUTINE_ARG_COUNT and _matches_return_type(args[2], expected_type)
    if isinstance(origin, type) and issubclass(origin, Awaitable):
        return bool(args) and _matches_return_type(args[0], expected_type)
    return False


def has_return_type[T](
    func: Callable[..., Any],
    expected_type: type[T],
) -> TypeGuard[Callable[..., T | Awaitable[T]]]:
    """Check if a function has a specific return type annotation.

    Unannotated callables (including lambdas) return ``False``. Handles unions,
    generic origins, ``TypeAliasType``, ``Annotated``, ``Awaitable`` / ``Coroutine``
    return annotations, ``async def`` functions, and ``TYPE_CHECKING``-only return
    annotations.

    Args:
        func: Function to check
        expected_type: The type to check for

    Returns:
        True if function returns the expected type (or Awaitable of it)
    """
    target: Any = func
    while isinstance(target, functools.partial):
        target = target.func
    if not isinstance(target, type) and not inspect.isroutine(target) and callable(target):
        target = target.__call__

    extra_globals: dict[str, Any] = {
        "Awaitable": Awaitable,
        "Coroutine": Coroutine,
        expected_type.__name__: expected_type,
    }
    try:
        hints = resolve_type_hints(target, extra_globals)
    except TypeError:
        return False

    if "return" not in hints:
        return False

    return_type = hints["return"]
    if _matches_return_type(return_type, expected_type):
        return True

    # Fallback when resolve_type_hints degraded an unresolvable ForwardRef (e.g. dotted
    # `mod.Type` or a union mixing expected_type with another TYPE_CHECKING-only name) to Any.
    if return_type is Any:
        try:
            raw_return = get_forwardref_signature(target).return_annotation
        except ValueError, TypeError:
            return False
        if isinstance(raw_return, str | ForwardRef):
            return expected_type.__name__ in annotation_names(raw_return)

    return False


def call_with_context[T](
    func: Callable[..., T],
    context: AgentContext[Any],
    **kwargs: Any,
) -> T:
    """Call function with appropriate context injection.

    Handles:
    - Simple functions
    - Bound methods
    - Functions expecting AgentContext
    - Functions expecting context data
    """
    from agentpool.agents import AgentContext

    if inspect.ismethod(func):
        if get_argument_key(func, AgentContext):
            return func(context)  # type: ignore[no-any-return]
        return func()  # type: ignore[no-any-return]
    if get_argument_key(func, AgentContext):
        return func(context, **kwargs)
    return func(context.data)


def validate_import(module_path: str, extras_name: str) -> None:
    """Check existence of module, showing helpful error if not installed."""
    if not find_spec(module_path):
        msg = f"""
Optional dependency {module_path!r} not found.
Install with: pip install {PACKAGE_NAME}[{extras_name}]
"""
        raise ImportError(msg.strip())


def get_fn_name(func: Any) -> str:
    """Get the __name__ of a callable, handling edge cases.

    Works with regular functions, lambdas, partials, and other callables
    that may not have a __name__ attribute.

    Args:
        func: Any callable object

    Returns:
        The function name, or a fallback string if not available
    """
    # Unwrap functools.partial
    while isinstance(func, functools.partial):
        func = func.func

    # Try __name__ first (most common case)
    if hasattr(func, "__name__"):
        return func.__name__  # type: ignore[no-any-return]

    # Try __class__.__name__ for callable objects
    if hasattr(func, "__class__"):
        return func.__class__.__name__  # type: ignore[no-any-return]

    return "<unknown>"


def get_fn_qualname(func: Any) -> str:
    """Get the __qualname__ of a callable, handling edge cases.

    Works with regular functions, lambdas, partials, methods, and other
    callables that may not have a __qualname__ attribute.

    Args:
        func: Any callable object

    Returns:
        The qualified name, or a fallback string if not available
    """
    # Unwrap functools.partial
    while isinstance(func, functools.partial):
        func = func.func

    # Try __qualname__ first (most common case)
    if hasattr(func, "__qualname__"):
        return func.__qualname__  # type: ignore[no-any-return]

    # Fall back to __name__
    if hasattr(func, "__name__"):
        return func.__name__  # type: ignore[no-any-return]

    # Try __class__.__qualname__ for callable objects
    if hasattr(func, "__class__"):
        return func.__class__.__qualname__  # type: ignore[no-any-return]

    return "<unknown>"
