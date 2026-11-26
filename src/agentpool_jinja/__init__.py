"""agentpool's jinjarope extension: filters and globals, imported on first use.

The ``jinjarope.environment`` entry point loads this module for every new jinjarope
``Environment``; importing ``agentpool`` itself there would cost seconds each time.
So nothing of agentpool is imported here: each filter imports its implementation
when a template first calls it.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

import jinja2


if TYPE_CHECKING:
    from collections.abc import Callable


_FILTERS: dict[str, tuple[str, bool]] = {
    # name: (module, takes the environment first: ``jinja2.pass_environment``)
    "run_agent": ("agentpool.functional", False),
    "run_agent_sync": ("agentpool.functional", False),
    "pick_one": ("agentpool.functional", False),
    "get_structured": ("agentpool.functional", False),
    "get_structured_multiple": ("agentpool.functional", False),
    "pydantic_playground": ("agentpool.jinja_filters", True),
    "pydantic_playground_url": ("agentpool.jinja_filters", True),
    "pydantic_playground_iframe": ("agentpool.jinja_filters", True),
    "pydantic_playground_link": ("agentpool.jinja_filters", True),
    "config_fields_table": ("agentpool.jinja_filters", False),
    "config_union_table": ("agentpool.jinja_filters", False),
    "cli_command_table": ("agentpool.jinja_filters", False),
    "toolset_tools_table": ("agentpool.jinja_filters", False),
}


def _target(module: str, name: str) -> Callable[..., Any]:
    return getattr(import_module(module), name)


def _lazy_filter(module: str, name: str, pass_env: bool) -> Callable[..., Any]:
    if pass_env:

        @jinja2.pass_environment
        def with_env(env: jinja2.Environment, *args: Any, **kwargs: Any) -> Any:
            return _target(module, name)(env, *args, **kwargs)

        fn = with_env
    else:

        def fn(*args: Any, **kwargs: Any) -> Any:
            return _target(module, name)(*args, **kwargs)

    fn.__name__ = fn.__qualname__ = name
    fn.__doc__ = f"``{module}.{name}`` (imported on first use)."
    return fn


class _LazyAgent:
    """The ``agent`` global: ``agentpool.Agent``, imported on first use."""

    def _agent(self) -> Any:
        return _target("agentpool.agents.native_agent", "Agent")

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self._agent()(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._agent(), name)

    def __repr__(self) -> str:
        return "<agentpool.Agent (lazy)>"


def setup_env(env: jinja2.Environment) -> None:
    """Register agentpool's filters and the ``agent`` global (jinjarope entry point)."""
    globals_: dict[str, Any] = env.globals  # Its declared value type is too narrow
    globals_["agent"] = _LazyAgent()
    env.filters.update({
        name: _lazy_filter(module, name, pass_env) for name, (module, pass_env) in _FILTERS.items()
    })
