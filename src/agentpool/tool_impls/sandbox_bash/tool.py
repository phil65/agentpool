"""Sandboxed bash tool for agentpool's tool framework."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from agentpool.log import get_logger
from agentpool.tool_impls.sandbox_bash.wrapper import SandboxBash
from agentpool.tools.base import Tool, ToolResult


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from agentpool.agents.context import AgentContext

logger = get_logger(__name__)


@dataclass
class SandboxBashTool(Tool[ToolResult]):
    """Execute bash commands in a sandboxed virtual environment.

    All commands run in-process against a virtual filesystem — no real filesystem
    access, no containers, no subprocesses. State (files, variables) persists
    between calls within the same tool instance.

    Uses bashkit (Rust) under the hood for fast, safe execution.
    """

    username: str | None = None
    """Custom username for the virtual environment."""

    hostname: str | None = None
    """Custom hostname for the virtual environment."""

    max_commands: int | None = None
    """Maximum number of commands to execute."""

    max_loop_iterations: int | None = None
    """Maximum loop iterations allowed."""

    _sandbox: SandboxBash | None = field(default=None, init=False, repr=False)

    def _get_sandbox(self) -> SandboxBash:
        """Get or create the sandbox instance (lazy initialization)."""
        if self._sandbox is None:
            self._sandbox = SandboxBash(
                username=self.username,
                hostname=self.hostname,
                max_commands=self.max_commands,
                max_loop_iterations=self.max_loop_iterations,
            )
        return self._sandbox

    def get_callable(self) -> Callable[..., Awaitable[ToolResult]]:
        """Return the execute method as the callable."""
        return self._execute

    async def _execute(
        self,
        ctx: AgentContext,
        commands: str,
    ) -> ToolResult:
        """Execute bash commands in a sandboxed virtual environment.

        Runs commands in an isolated bash interpreter with a virtual filesystem.
        No access to the real filesystem or network (unless explicitly allowed).
        State persists between calls — files created in one call are available
        in subsequent calls.

        Args:
            ctx: Agent context for event emission.
            commands: Bash commands to execute (like ``bash -c "commands"``).
        """
        sandbox = self._get_sandbox()
        result = await sandbox.execute(commands)

        logger.debug(
            "Sandbox bash executed",
            commands=commands[:100],
            exit_code=result.exit_code,
            stdout_len=len(result.stdout),
        )

        return ToolResult(
            content=result.output,
        )

    def reset(self) -> None:
        """Reset the sandbox, clearing all virtual filesystem state."""
        if self._sandbox is not None:
            self._sandbox.reset()
