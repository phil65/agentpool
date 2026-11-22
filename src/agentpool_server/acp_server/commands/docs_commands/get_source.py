"""Get Python source code using dot notation."""

from __future__ import annotations

import uuid

from pydantic_ai import UserPromptPart
from slashed import CommandContext  # noqa: TC002

from agentpool.log import get_logger
from agentpool.messaging.context import NodeContext  # noqa: TC001
import agentpool.utils.importing
from agentpool_commands.base import NodeCommand
from agentpool_server.acp_server.session import ACPSession  # noqa: TC001


logger = get_logger(__name__)


class GetSourceCommand(NodeCommand):
    """Get Python source code using dot notation.

    Uses the AgentPool importing.py utility to fetch source code
    for any Python object accessible via dot notation.
    """

    name = "get-source"
    category = "docs"

    async def execute_command(
        self,
        ctx: CommandContext[NodeContext[ACPSession]],
        dot_path: str,
        *,
        cwd: str | None = None,
    ) -> None:
        """Get Python source code for an object.

        Args:
            ctx: Command context with ACP session
            dot_path: Dot notation path to Python object (e.g., 'requests.Session.get')
            cwd: Working directory to run in (defaults to session cwd)
        """
        session = ctx.context.data
        assert session

        # Generate tool call ID
        tool_call_id = f"get-source-{uuid.uuid4().hex[:8]}"

        try:
            # Check if client supports terminal
            if not (session.client_capabilities and session.client_capabilities.terminal):
                msg = "❌ **Terminal access not available for source code fetching**"
                await session.notifications.send_agent_text(msg)
                return

            importing_py_path = agentpool.utils.importing.__file__
            assert isinstance(importing_py_path, str)
            await session.notifications.tool_call_start(
                tool_call_id=tool_call_id,
                title=f"Getting source: {dot_path}",
                kind="read",
            )
            # Run importing.py as script
            output, exit_code = await session.run_command(
                "uv", ["run", importing_py_path, dot_path], cwd=cwd or session.cwd
            )
            # Check if command succeeded
            if exit_code != 0:
                error_msg = output.strip() or f"Exit code: {exit_code}"
                await session.notifications.tool_call_progress(
                    tool_call_id=tool_call_id,
                    status="failed",
                    title=f"Failed to get source for {dot_path}: {error_msg}",
                )
                return
            source_content = output.strip()
            if not source_content:
                await session.notifications.tool_call_progress(
                    tool_call_id=tool_call_id,
                    status="completed",
                    title="No source code found",
                )
                return
            # Stage the source content for use in agent context
            content = f"Python source code for {dot_path}:\n\n{source_content}"
            staged_part = UserPromptPart(content=content)
            ctx.context.agent.staged_content.add([staged_part])
            # Send successful result - wrap in code block for proper display
            staged_count = len(ctx.context.agent.staged_content)
            await session.notifications.tool_call_progress(
                tool_call_id=tool_call_id,
                status="completed",
                title=f"Source code fetched and staged ({staged_count} total parts)",
                content=[f"```python\n{source_content}\n```"],
            )

        except Exception as e:
            logger.exception("Unexpected error fetching source code", dot_path=dot_path)
            await session.notifications.tool_call_progress(
                tool_call_id=tool_call_id,
                status="failed",
                title=f"Error: {e}",
            )
