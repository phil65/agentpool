"""Provider for process management tools with event emission."""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING

from agentpool import log
from agentpool.agents.context import AgentContext  # noqa: TC001
from agentpool.resource_providers import ResourceProvider


logger = log.get_logger(__name__)


if TYPE_CHECKING:
    from collections.abc import Sequence

    from exxec import ExecutionEnvironment

    from agentpool.tools.base import Tool


def filter_lines_regex(pattern_str: str, text: str) -> str:
    try:
        pattern = re.compile(pattern_str)
        filtered_lines = [line for line in text.splitlines(keepends=True) if pattern.search(line)]
        return "".join(filtered_lines)
    except re.error as regex_err:
        return f"Invalid filter regex: {regex_err}"


class ProcessManagementTools(ResourceProvider):
    """Provider for background process management tools.

    Provides tools for starting, monitoring, and controlling background processes.
    Uses any ExecutionEnvironment backend and emits events via AgentContext.

    For code execution and bash commands, use the standalone tools:
    - agentpool.tool_impls.bash.BashTool
    - agentpool.tool_impls.execute_code.ExecuteCodeTool
    """

    def __init__(self, env: ExecutionEnvironment | None = None, name: str = "process") -> None:
        """Initialize process management toolset.

        Args:
            env: Execution environment to use (defaults to agent.env)
            name: The name of the toolset
        """
        super().__init__(name=name)
        self._env = env
        self._start_times: dict[str, float] = {}  # process_id -> monotonic timestamp

    def get_env(self, agent_ctx: AgentContext) -> ExecutionEnvironment:
        """Get execution environment, falling back to agent's env if not set.

        Args:
            agent_ctx: Agent context to get fallback env from
        """
        if self._env is not None:
            return self._env
        return agent_ctx.agent.env

    async def get_tools(self) -> Sequence[Tool]:
        return [
            self.create_tool(self.start_process, category="execute", open_world=True),
            self.create_tool(
                self.get_process_output, category="execute", read_only=True, idempotent=True
            ),
            self.create_tool(
                self.wait_for_process, category="execute", read_only=True, idempotent=True
            ),
            self.create_tool(self.kill_process, category="execute", destructive=True),
            self.create_tool(self.release_process, category="execute"),
            self.create_tool(
                self.list_processes, category="search", read_only=True, idempotent=True
            ),
        ]

    async def start_process(  # noqa: D417
        self,
        agent_ctx: AgentContext,
        command: str,
        args: list[str] | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        output_limit: int | None = None,
    ) -> str:
        """Start a command in the background and return process ID.

        This is the preferred tool  for long-running processes that should run in the background
        (e.g. servers, file watchers, build --watch).

        Args:
            command: Command to execute
            args: Command arguments
            cwd: Working directory
            env: Environment variables (added to current env)
            output_limit: Maximum bytes of output to retain
        """
        manager = self.get_env(agent_ctx).process_manager
        try:
            process_id = await manager.start_process(
                command=command,
                args=args,
                cwd=cwd,
                env=env,
                output_limit=output_limit,
            )
            self._start_times[process_id] = time.monotonic()
            await agent_ctx.events.process_started(process_id, command, success=True)

        except Exception as e:  # noqa: BLE001
            await agent_ctx.events.process_started("", command, success=False, error=str(e))
            return f"Failed to start process: {e}"
        else:
            full_cmd = f"{command} {' '.join(args)}" if args else command
            return f"Started background process {process_id}\nCommand: {full_cmd}"

    async def get_process_output(  # noqa: D417
        self,
        agent_ctx: AgentContext,
        process_id: str,
        filter_lines: str | None = None,
    ) -> str:
        """Get current output from a background process.

        Args:
            process_id: Process identifier from start_process
            filter_lines: Optional regex pattern to filter output lines
                          (only matching lines returned)
        """
        manager = self.get_env(agent_ctx).process_manager
        try:
            output = await manager.get_output(process_id)
            await agent_ctx.events.process_output(process_id, output.combined or "")
            combined = output.combined or ""
            # Apply regex filter if specified
            if filter_lines and combined:
                combined = filter_lines_regex(filter_lines, combined)
            status = "completed" if output.exit_code is not None else "running"
            # Format as plain text
            suffix_parts: list[str] = [f"Status: {status}"]
            if output.exit_code is not None:
                suffix_parts.append(f"Exit code: {output.exit_code}")
            if output.truncated:
                suffix_parts.append("[output truncated]")
            if start := self._start_times.get(process_id):
                elapsed = time.monotonic() - start
                suffix_parts.append(f"Elapsed: {elapsed:.1f}s")

            return (
                f"{combined}\n\n{' | '.join(suffix_parts)}"
                if combined
                else " | ".join(suffix_parts)
            )
        except ValueError as e:
            return f"Error: {e}"
        except Exception as e:  # noqa: BLE001
            return f"Error getting process output: {e}"

    async def wait_for_process(  # noqa: D417
        self,
        agent_ctx: AgentContext,
        process_id: str,
        filter_lines: str | None = None,
    ) -> str:
        """Wait for background process to complete and return final output.

        Args:
            process_id: Process identifier from start_process
            filter_lines: Optional regex pattern to filter output lines
                          (only matching lines returned)
        """
        manager = self.get_env(agent_ctx).process_manager
        try:
            exit_code = await manager.wait_for_exit(process_id)
            output = await manager.get_output(process_id)
            await agent_ctx.events.process_exit(process_id, exit_code, final_output=output.combined)
        except ValueError as e:
            return f"Error: {e}"
        except Exception as e:  # noqa: BLE001
            return f"Error waiting for process: {e}"
        else:
            combined = output.combined or ""
            if filter_lines and combined:  # Apply regex filter if specified
                combined = filter_lines_regex(filter_lines, combined)
            # Format as plain text
            suffix_parts = []
            if output.truncated:
                suffix_parts.append("[output truncated]")
            if exit_code != 0:
                suffix_parts.append(f"Exit code: {exit_code}")
            if start := self._start_times.pop(process_id, None):
                elapsed = time.monotonic() - start
                suffix_parts.append(f"Execution time: {elapsed:.1f}s")

            if suffix_parts:
                return f"{combined}\n\n{' | '.join(suffix_parts)}"
            return combined

    async def kill_process(self, agent_ctx: AgentContext, process_id: str) -> str:  # noqa: D417
        """Terminate a background process.

        Args:
            process_id: Process identifier from start_process
        """
        try:
            await self.get_env(agent_ctx).process_manager.kill_process(process_id)
            await agent_ctx.events.process_killed(process_id=process_id, success=True)
        except ValueError as e:
            await agent_ctx.events.process_killed(process_id, success=False, error=str(e))
            return f"Error: {e}"
        except Exception as e:  # noqa: BLE001
            await agent_ctx.events.process_killed(process_id, success=False, error=str(e))
            return f"Error killing process: {e}"
        else:
            return f"Process {process_id} has been terminated"

    async def release_process(self, agent_ctx: AgentContext, process_id: str) -> str:  # noqa: D417
        """Release resources for a background process.

        Args:
            process_id: Process identifier from start_process
        """
        try:
            await self.get_env(agent_ctx).process_manager.release_process(process_id)
            await agent_ctx.events.process_released(process_id=process_id, success=True)
        except ValueError as e:
            await agent_ctx.events.process_released(process_id, success=False, error=str(e))
            return f"Error: {e}"
        except Exception as e:  # noqa: BLE001
            await agent_ctx.events.process_released(process_id, success=False, error=str(e))
            return f"Error releasing process: {e}"
        else:
            return f"Process {process_id} resources have been released"

    async def list_processes(self, agent_ctx: AgentContext) -> str:
        """List all active background processes."""
        env = self.get_env(agent_ctx)
        try:
            process_ids = await env.process_manager.list_processes()
            if not process_ids:
                return "No active background processes"

            lines = [f"Active processes ({len(process_ids)}):"]
            for process_id in process_ids:
                try:
                    info = await env.process_manager.get_process_info(process_id)
                    command = info["command"]
                    args = info.get("args", [])
                    full_cmd = f"{command} {' '.join(args)}" if args else command
                    status = "running" if info.get("is_running", False) else "stopped"
                    exit_code = info.get("exit_code")
                    status_str = (
                        f"{status}" if exit_code is None else f"{status} (exit {exit_code})"
                    )
                    lines.append(f"  - {process_id}: {full_cmd} [{status_str}]")
                except Exception as e:  # noqa: BLE001
                    lines.append(f"  - {process_id}: [error getting info: {e}]")

            return "\n".join(lines)
        except Exception as e:  # noqa: BLE001
            return f"Error listing processes: {e}"
