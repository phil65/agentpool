"""Agent CLI tool for executing internal commands."""

from typing import Literal

from agentpool.tool_impls.agent_cli.tool import AgentCliTool
from agentpool_config.tools import ToolHints


__all__ = ["AgentCliTool", "create_agent_cli_tool"]

# Tool metadata defaults
NAME = "agent_cli"
DESCRIPTION = "Execute an internal agent management command."
CATEGORY: Literal["other"] = "other"
HINTS = ToolHints()


def create_agent_cli_tool(
    *,
    name: str = NAME,
    description: str = DESCRIPTION,
    requires_confirmation: bool = False,
) -> AgentCliTool:
    """Create a configured AgentCliTool instance.

    Args:
        name: Tool name override.
        description: Tool description override.
        requires_confirmation: Whether tool execution needs confirmation.

    Returns:
        Configured AgentCliTool instance.
    """
    return AgentCliTool(
        name=name,
        description=description,
        category=CATEGORY,
        hints=HINTS,
        requires_confirmation=requires_confirmation,
    )
