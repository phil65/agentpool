"""Python code execution tool."""

from typing import TYPE_CHECKING, Literal

from agentpool.tool_impls.execute_code.tool import ExecuteCodeTool
from agentpool_config.tools import ToolHints


if TYPE_CHECKING:
    from exxec import ExecutionEnvironment

__all__ = ["ExecuteCodeTool", "create_execute_code_tool"]

# Tool metadata defaults
NAME = "execute_code"
DESCRIPTION = "Execute Python code and return the result."
CATEGORY: Literal["execute"] = "execute"
HINTS = ToolHints()


def create_execute_code_tool(
    *,
    env: ExecutionEnvironment | None = None,
    name: str = NAME,
    description: str = DESCRIPTION,
    requires_confirmation: bool = False,
) -> ExecuteCodeTool:
    """Create a configured ExecuteCodeTool instance.

    Args:
        env: Execution environment to use. Falls back to agent.env if not set.
        name: Tool name override.
        description: Tool description override.
        requires_confirmation: Whether tool execution needs confirmation.

    Returns:
        Configured ExecuteCodeTool instance.

    Example:
        # Basic
        exec_code = create_execute_code_tool()

        # Require confirmation
        exec_code = create_execute_code_tool(requires_confirmation=True)
    """
    return ExecuteCodeTool(
        name=name,
        description=description,
        category=CATEGORY,
        hints=HINTS,
        env=env,
        requires_confirmation=requires_confirmation,
    )
