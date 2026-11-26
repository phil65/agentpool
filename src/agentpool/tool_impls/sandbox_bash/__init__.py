"""Sandboxed bash execution via bashkit.

Provides a virtual bash interpreter that runs entirely in-process with no real
filesystem access. Built on bashkit (Rust) for safe, sandboxed command execution
in AI agent workloads.
"""

from typing import Literal

from agentpool.tool_impls.sandbox_bash.tool import SandboxBashTool
from agentpool.tool_impls.sandbox_bash.wrapper import SandboxBash, SandboxExecResult
from agentpool_config.tools import ToolHints


__all__ = [
    "SandboxBash",
    "SandboxBashTool",
    "SandboxExecResult",
    "create_sandbox_bash_tool",
]

NAME = "sandbox_bash"
DESCRIPTION = (
    "Execute bash commands in a sandboxed virtual environment. "
    "All file operations happen in a virtual filesystem — nothing touches the real host."
)
CATEGORY: Literal["execute"] = "execute"
HINTS = ToolHints(destructive=False, idempotent=False, open_world=False, read_only=False)


def create_sandbox_bash_tool(
    *,
    username: str | None = None,
    hostname: str | None = None,
    max_commands: int | None = None,
    max_loop_iterations: int | None = None,
    name: str = NAME,
    description: str = DESCRIPTION,
    requires_confirmation: bool = False,
) -> SandboxBashTool:
    """Create a configured SandboxBashTool instance.

    Args:
        username: Custom username for the virtual environment (whoami).
        hostname: Custom hostname for the virtual environment.
        max_commands: Maximum number of commands to execute.
        max_loop_iterations: Maximum loop iterations allowed.
        name: Tool name override.
        description: Tool description override.
        requires_confirmation: Whether tool execution needs confirmation.

    Returns:
        Configured SandboxBashTool instance.
    """
    return SandboxBashTool(
        name=name,
        description=description,
        category=CATEGORY,
        hints=HINTS,
        username=username,
        hostname=hostname,
        max_commands=max_commands,
        max_loop_iterations=max_loop_iterations,
        requires_confirmation=requires_confirmation,
    )
