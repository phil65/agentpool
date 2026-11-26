"""Bash command execution tool."""

from typing import TYPE_CHECKING, Literal

from agentpool.tool_impls.bash.tool import BashTool
from agentpool_config.tools import ToolHints


if TYPE_CHECKING:
    from exxec import ExecutionEnvironment

__all__ = ["BashTool", "create_bash_tool"]

# Tool metadata defaults
NAME = "bash"
DESCRIPTION = "Execute a shell command and return the output."
CATEGORY: Literal["execute"] = "execute"
HINTS = ToolHints(open_world=True)


def create_bash_tool(
    *,
    env: ExecutionEnvironment | None = None,
    output_limit: int | None = None,
    timeout: float | None = None,
    name: str = NAME,
    description: str = DESCRIPTION,
    requires_confirmation: bool = False,
) -> BashTool:
    """Create a configured BashTool instance.

    Args:
        env: Execution environment to use. Falls back to agent.env if not set.
        output_limit: Default maximum bytes of output to return.
        timeout: Default command timeout in seconds. None means no timeout.
        name: Tool name override.
        description: Tool description override.
        requires_confirmation: Whether tool execution needs confirmation.

    Returns:
        Configured BashTool instance.

    Example:
        # Basic
        bash = create_bash_tool()

        # With limits
        bash = create_bash_tool(output_limit=10000, timeout=30.0)

        # Require confirmation for dangerous commands
        bash = create_bash_tool(requires_confirmation=True)
    """
    return BashTool(
        name=name,
        description=description,
        category=CATEGORY,
        hints=HINTS,
        env=env,
        output_limit=output_limit,
        timeout=timeout,
        requires_confirmation=requires_confirmation,
    )
