"""AgentCli tool implementation."""

from __future__ import annotations

from dataclasses import dataclass
from io import StringIO
from typing import TYPE_CHECKING

from agentpool.agents.context import AgentContext  # noqa: TC001
from agentpool.tools.base import Tool


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


class _StringOutputWriter:
    """Output writer that captures output to a string buffer."""

    def __init__(self) -> None:
        self._buffer = StringIO()

    async def print(self, message: str) -> None:
        """Write a message to the buffer."""
        self._buffer.write(message)
        self._buffer.write("\n")

    def getvalue(self) -> str:
        """Get the captured output."""
        return self._buffer.getvalue()


@dataclass
class AgentCliTool(Tool[str]):
    """Tool for executing internal agent management commands.

    Provides access to the agent's internal CLI for management operations
    like creating agents, managing tools, connecting nodes, etc.
    """

    def get_callable(self) -> Callable[..., Awaitable[str]]:
        """Get the tool callable."""
        return self._execute

    async def _execute(self, ctx: AgentContext, command: str) -> str:
        """Execute an internal agent management command.

        This provides access to the agent's internal CLI for management operations.

        IMPORTANT: Before using any command for the first time, call "help <command>"
        to learn the correct syntax and available options.

        Discovery commands:
        - "help" - list all available commands
        - "help <command>" - get detailed usage for a specific command

        Command categories:
        - Agent/team management: create-agent, create-team, list-agents
        - Tool management: list-tools, register-tool, enable-tool, disable-tool
        - MCP servers: add-mcp-server, add-remote-mcp-server, mcp
        - Connections: connect, disconnect, connections
        - Workers: add-worker, remove-worker, list-workers

        Args:
            ctx: Agent execution context.
            command: The command to execute. Leading slash is optional.

        Returns:
            Command output or error message.
        """
        from slashed import CommandContext

        if not ctx.agent.command_store:
            return "No command store available"

        # Remove leading slash if present
        cmd = command.lstrip("/")

        # Create output capture
        output = _StringOutputWriter()

        # Create CommandContext with output capture and AgentContext as data
        cmd_ctx = CommandContext(
            output=output,
            data=ctx,
            command_store=ctx.agent.command_store,
        )

        try:
            await ctx.agent.command_store.execute_command(cmd, cmd_ctx)
            result = output.getvalue()
        except Exception as e:  # noqa: BLE001
            return f"Command failed: {e}"
        else:
            return result if result else "Command executed successfully."
