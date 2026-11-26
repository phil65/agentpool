"""Python code execution tool."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING
import uuid

import anyenv
from exxec.events import (
    OutputEvent,
    ProcessCompletedEvent,
    ProcessErrorEvent,
    ProcessStartedEvent,
)

from agentpool.agents.context import AgentContext  # noqa: TC001
from agentpool.log import get_logger
from agentpool.tools.base import Tool
from agentpool.utils.identifiers import script_slug
from agentpool.utils.streams import closing


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from exxec import ExecutionEnvironment


logger = get_logger(__name__)


@dataclass
class ExecuteCodeTool(Tool[str]):
    """Execute Python code and return the result.

    A standalone tool for executing Python code in a sandboxed environment.

    Use create_execute_code_tool() factory for convenient instantiation with defaults.
    """

    # Tool-specific configuration
    env: ExecutionEnvironment | None = None
    """Execution environment to use. Falls back to agent.env if not set."""

    def get_callable(self) -> Callable[..., Awaitable[str]]:
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
        code: str,
        title: str,
    ) -> str:
        """Execute Python code and return the result.

        Args:
            ctx: Agent context for event emission and environment access
            code: Python code to execute
            title: Short descriptive title for this script (3-4 words)
        """
        process_id: str | None = None
        output_parts: list[str] = []
        exit_code: int | None = None
        error_msg: str | None = None
        result_str = ""

        # Check if we're running in ACP - terminal streams client-side
        from exxec.acp_provider import ACPExecutionEnvironment

        env = self._get_env(ctx)
        is_acp = isinstance(env, ACPExecutionEnvironment)

        try:
            # Closed here also when the call is cancelled (kills the process)
            events = env.stream_code(code)
            async with closing(events):
                async for event in events:
                    match event:
                        case ProcessStartedEvent(process_id=pid, command=cmd):
                            process_id = pid
                            await ctx.events.process_started(pid, cmd, success=True)

                        case OutputEvent(data=data):
                            output_parts.append(data)
                            # Skip progress events for ACP - terminal streams client-side
                            if process_id and not is_acp:
                                await ctx.events.process_output(process_id, data)

                        case ProcessCompletedEvent(exit_code=code_):
                            exit_code = code_
                            # Skip exit event for ACP - completion handled by ToolCallCompleteEvent
                            if process_id and not is_acp:
                                out = "".join(output_parts)
                                await ctx.events.process_exit(
                                    process_id, exit_code, final_output=out
                                )

                        case ProcessErrorEvent(error=err, exit_code=code_):
                            error_msg = err
                            exit_code = code_
                            # Skip exit event for ACP - completion handled by ToolCallCompleteEvent
                            if process_id and not is_acp:
                                await ctx.events.process_exit(
                                    process_id, exit_code or 1, final_output=err
                                )

            combined_output = "".join(output_parts)

            # Format error response
            if error_msg:
                result_str = f"{combined_output}\n\nError: {error_msg}\nExit code: {exit_code}"
            elif exit_code and exit_code != 0:
                result_str = f"{combined_output}\n\nExit code: {exit_code}"
            else:
                result_str = combined_output

        except Exception as e:  # noqa: BLE001
            error_id = process_id or f"code_{uuid.uuid4().hex[:8]}"
            await ctx.events.process_started(error_id, "execute_code", success=False, error=str(e))
            exit_code = 1
            error_msg = str(e)
            result_str = f"Error executing code: {e}"
        except asyncio.CancelledError:
            error_msg = "Cancelled"
            raise
        finally:
            # Save to agent's internal filesystem
            end_time = datetime.now(UTC)
            timestamp = end_time.strftime("%Y%m%d_%H%M%S")
            slug = script_slug(title)

            # Write script file
            script_path = f"execute_code/scripts/{timestamp}_{slug}.py"
            ctx.internal_fs.pipe(script_path, code.encode("utf-8"))

            # Write metadata file
            metadata = {
                "title": title,
                "timestamp": end_time.isoformat(),
                "exit_code": exit_code or 0,
                "result": result_str,
                "error": error_msg,
            }
            metadata_path = f"execute_code/scripts/{timestamp}_{slug}.json"
            ctx.internal_fs.pipe(metadata_path, anyenv.dump_json(metadata, indent=True).encode())

        return result_str
