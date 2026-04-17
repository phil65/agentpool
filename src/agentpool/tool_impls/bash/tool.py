"""Bash command execution tool."""

from __future__ import annotations

from dataclasses import dataclass
import re
import time
from typing import TYPE_CHECKING, Any
import uuid

from exxec.events import (
    OutputEvent,
    ProcessCompletedEvent,
    ProcessErrorEvent,
    ProcessStartedEvent,
)

from agentpool.agents.context import AgentContext  # noqa: TC001
from agentpool.log import get_logger
from agentpool.tool_impls.bash.helpers import format_output, truncate_output
from agentpool.tools.base import Tool, ToolResult


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from exxec import ExecutionEnvironment


logger = get_logger(__name__)


def apply_linewise_regex_filter(text: str, pattern_str: str) -> str:
    pattern = re.compile(pattern_str)
    lines = [i for i in text.splitlines(keepends=True) if pattern.search(i)]
    return "".join(lines)


@dataclass
class BashTool(Tool[ToolResult]):
    """Execute shell commands and return the output.

    A standalone, configurable tool for running bash commands. Can be configured
    with a specific execution environment and output limits.

    Use create_bash_tool() factory for convenient instantiation with defaults.
    """

    # Tool-specific configuration
    env: ExecutionEnvironment | None = None
    """Execution environment to use. Falls back to agent.env if not set."""

    output_limit: int | None = None
    """Default maximum bytes of output to return."""

    timeout: float | None = None
    """Default command timeout in seconds. None means no timeout."""

    def get_callable(self) -> Callable[..., Awaitable[ToolResult]]:
        """Return the execute method as the callable."""
        return self._execute

    def _get_env(self, ctx: AgentContext) -> ExecutionEnvironment:
        """Get execution environment, falling back to agent's env."""
        if self.env is not None:
            return self.env
        return ctx.agent.env

    async def _execute(  # noqa: PLR0915
        self,
        ctx: AgentContext,
        command: str,
        output_limit: int | None = None,
        timeout: float | None = None,
        filter_lines: str | None = None,
    ) -> ToolResult:
        """Execute a shell command and return the output.

        This is the primary tool for running shell commands. It executes the command,
        waits for completion, and returns the full output in a single call.

        Args:
            ctx: Agent context for event emission and environment access
            command: Shell command to execute
            output_limit: Maximum bytes of output to return (overrides default)
            timeout: Command timeout in seconds (overrides default)
            filter_lines: Optional regex pattern to filter output lines
                          (only matching lines returned)
        """
        from exxec.acp_provider import ACPExecutionEnvironment

        effective_limit = output_limit or self.output_limit
        effective_timeout = timeout if timeout is not None else self.timeout
        process_id: str | None = None
        stdout_parts: list[str] = []
        stderr_parts: list[str] = []
        exit_code: int | None = None
        error_msg: str | None = None
        start_time = time.monotonic()
        try:
            env = self._get_env(ctx)
            is_acp = isinstance(env, ACPExecutionEnvironment)
            async for event in env.stream_command(command, timeout=effective_timeout):
                match event:
                    case ProcessStartedEvent(process_id=pid, command=cmd):
                        process_id = pid
                        await ctx.events.process_started(pid, cmd, success=True)
                    case OutputEvent(process_id=pid, data=data, stream=stream):
                        if stream == "stderr":
                            stderr_parts.append(data)
                        else:
                            stdout_parts.append(data)
                        # Skip progress events for ACP - terminal streams client-side
                        if not is_acp:
                            await ctx.events.process_output(pid, data)
                    case ProcessCompletedEvent(process_id=pid, exit_code=code_):
                        exit_code = code_
                        # Skip exit event for ACP - completion handled by FunctionToolResultEvent
                        if not is_acp:
                            combined = "".join(stdout_parts) + "".join(stderr_parts)
                            await ctx.events.process_exit(pid, exit_code, final_output=combined)
                    case ProcessErrorEvent(error=err, exit_code=code_):
                        error_msg = err
                        exit_code = code_
        except Exception as e:  # noqa: BLE001
            error_id = process_id or f"cmd_{uuid.uuid4().hex[:8]}"
            await ctx.events.process_started(error_id, command, success=False, error=str(e))
            meta = {"output": "", "exit": None, "description": command}
            return ToolResult(content=f"Error executing command: {e}", metadata=meta)

        stdout = "".join(stdout_parts)
        stderr = "".join(stderr_parts)
        if filter_lines:  # Apply regex filter if specified
            try:
                stdout = apply_linewise_regex_filter(stdout, filter_lines)
                stderr = apply_linewise_regex_filter(stderr, filter_lines)
            except re.error as regex_err:
                error_msg = f"Invalid filter regex: {regex_err}"
                meta: dict[str, Any] = {"output": "", "exit": None, "description": command}
                return ToolResult(content=error_msg, metadata=meta)
            # Apply output limit if specified
        truncated = False
        if effective_limit:
            stdout, stdout_truncated = truncate_output(stdout, effective_limit)
            stderr, stderr_truncated = truncate_output(stderr, effective_limit)
            truncated = stdout_truncated or stderr_truncated
        # Format error response
        if error_msg:
            output = stdout + stderr if stdout or stderr else ""
            result_output = f"{output}\n\nError: {error_msg}\nExit code: {exit_code}"
            meta = {"output": output, "exit": exit_code, "description": command}
            return ToolResult(content=result_output, metadata=meta)

        # Format success response
        elapsed = time.monotonic() - start_time
        formatted_output = format_output(stdout, stderr, exit_code, truncated, elapsed)
        combined_output = stdout + stderr
        meta = {"output": combined_output, "exit": exit_code, "description": command}
        return ToolResult(content=formatted_output, metadata=meta)
