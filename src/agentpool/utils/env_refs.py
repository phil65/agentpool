"""``${VAR}`` references to environment variables in configuration values."""

from __future__ import annotations

import os
import string
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections.abc import Mapping


def expand_env_refs(
    values: Mapping[str, str], env: Mapping[str, str] | None = None
) -> dict[str, str]:
    """Replace ``${VAR}`` / ``$VAR`` references in each value (``$$`` is a literal ``$``).

    Args:
        values: E.g. HTTP headers.
        env: Variables to use (default: the process environment).

    Raises:
        ValueError: A value references an unset variable.
    """
    source = os.environ if env is None else env
    resolved: dict[str, str] = {}
    for name, value in values.items():
        try:
            resolved[name] = string.Template(value).substitute(source)
        except KeyError as exc:
            msg = f"{name!r} references unset environment variable {exc.args[0]}"
            raise ValueError(msg) from exc
    return resolved
