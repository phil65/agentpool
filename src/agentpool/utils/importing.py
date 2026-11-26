"""Utilities for importing callables and classes from dotted paths."""

import importlib
import inspect
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from collections.abc import Callable


def import_callable(path: str) -> Callable[..., Any]:
    """Import a callable from a dotted path.

    Supports both dot and colon notation:
    - Dot notation: module.submodule.Class.method
    - Colon notation: module.submodule:Class.method

    Examples:
        >>> import_callable("os.path.join")
        >>> import_callable("builtins.str.upper")
        >>> import_callable("sqlalchemy.orm:Session.query")

    Args:
        path: Import path using dots and/or colon

    Returns:
        Imported callable

    Raises:
        ValueError: If path cannot be imported or result isn't callable
    """
    if not path:
        raise ValueError("Import path cannot be empty")

    # Normalize path - replace colon with dot if present
    normalized_path = path.replace(":", ".")
    parts = normalized_path.split(".")

    # Try importing progressively smaller module paths
    for i in range(len(parts), 0, -1):
        try:
            # Try current module path
            module_path = ".".join(parts[:i])
            module = importlib.import_module(module_path)

            # Walk remaining parts as attributes
            obj = module
            for part in parts[i:]:
                obj = getattr(obj, part)

            # Check if we got a callable
            if callable(obj):
                return obj
            raise ValueError(f"Found object at {path} but it isn't callable")
        except ImportError:
            # Try next shorter path
            continue
        except AttributeError:
            # Attribute not found - try next shorter path
            continue
    # If we get here, no import combination worked
    raise ValueError(f"Could not import callable from path: {path}")


def import_class(path: str) -> type:
    """Import a class from a dotted path.

    Args:
        path: Dot-separated path to the class

    Returns:
        The imported class

    Raises:
        ValueError: If path is invalid or doesn't point to a class
    """
    try:
        obj = import_callable(path)
        if not isinstance(obj, type):
            raise TypeError(f"{path} is not a class")  # noqa: TRY301
    except Exception as exc:
        raise ValueError(f"Failed to import class from {path}") from exc
    else:
        return obj


if __name__ == "__main__":
    # ATTENTION: Dont modify this script.
    import sys

    if len(sys.argv) != 2:  # noqa: PLR2004
        print("Usage: python importing.py <dot.path.to.object>", file=sys.stderr)
        sys.exit(1)

    dot_path = sys.argv[1]

    try:
        obj = import_callable(dot_path)
        source = inspect.getsource(obj)
        print(source)
    except Exception as e:  # noqa: BLE001
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
