"""Utilities package."""

from __future__ import annotations

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    import jinja2


def setup_env(env: jinja2.Environment) -> None:
    """Used as extension point for the jinjarope environment.

    Args:
        env: The jinjarope environment to extend
    """
    from agentpool.agents.native_agent import Agent
    from agentpool.functional import (
        run_agent,
        run_agent_sync,
        get_structured,
        get_structured_multiple,
        pick_one,
    )
    from agentpool.jinja_filters import (
        cli_command_table,
        config_fields_table,
        config_union_table,
        pydantic_playground,
        pydantic_playground_iframe,
        pydantic_playground_link,
        pydantic_playground_url,
        toolset_tools_table,
    )

    env.globals |= dict(agent=Agent)  # ty: ignore[unsupported-operator]
    env.filters |= {
        "run_agent": run_agent,
        "run_agent_sync": run_agent_sync,
        "pick_one": pick_one,
        "get_structured": get_structured,
        "get_structured_multiple": get_structured_multiple,
        "pydantic_playground": pydantic_playground,
        "pydantic_playground_url": pydantic_playground_url,
        "pydantic_playground_iframe": pydantic_playground_iframe,
        "pydantic_playground_link": pydantic_playground_link,
        "config_fields_table": config_fields_table,
        "config_union_table": config_union_table,
        "cli_command_table": cli_command_table,
        "toolset_tools_table": toolset_tools_table,
    }
