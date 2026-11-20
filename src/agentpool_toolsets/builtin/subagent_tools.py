"""Provider for subagent/task tools with streaming support."""

from __future__ import annotations

import asyncio
import datetime as dt
import re
from typing import TYPE_CHECKING, Any, Literal

from pydantic_ai import ModelRetry, TextPartDelta, ThinkingPartDelta
from upathtools.filesystems.base import WrapperFileSystem

from agentpool.agents.context import AgentContext  # noqa: TC001
from agentpool.agents.events import (
    PartDeltaEvent,
    StreamCompleteEvent,
    SubAgentEvent,
    TaskFinishedEvent,
    TaskStartedEvent,
)
from agentpool.agents.events.processors import batch_stream_deltas
from agentpool.log import get_logger
from agentpool.resource_providers import StaticResourceProvider
from agentpool.tools.exceptions import ToolError


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fsspec.asyn import AsyncFileSystem

    from agentpool.agents.events import RichAgentStreamEvent
    from agentpool.agents.events.events import SubAgentType


logger = get_logger(__name__)

# Set to hold references to background tasks, preventing GC while running
_background_tasks: set[asyncio.Task[Any]] = set()


def _generate_task_id(description: str) -> str:
    """Generate a unique, sortable task ID from timestamp and description.

    Args:
        description: Short task description to include in the ID

    Returns:
        Task ID in format: YYYYMMDD-HHMMSS-description
    """
    timestamp = dt.datetime.now(tz=dt.UTC).strftime("%Y%m%d-%H%M%S")
    # Sanitize description: lowercase, replace spaces/special chars with dashes
    slug = re.sub(r"[^a-z0-9]+", "-", description.lower()).strip("-")[:30]
    return f"{timestamp}-{slug}"


async def _stream_task(
    ctx: AgentContext,
    source_name: str,
    source_type: SubAgentType,
    stream: AsyncIterator[RichAgentStreamEvent[Any]],
    *,
    batch_deltas: bool = False,
    depth: int = 1,
    parent_tool_call_id: str | None = None,
) -> str:
    """Stream a task's execution, emitting SubAgentEvents into parent stream.

    Args:
        ctx: Agent context for emitting events
        source_name: Name of the agent/team executing the task
        source_type: Whether source is "agent" or "team"
        stream: Async iterator of stream events from agent.run_stream()
        batch_deltas: If True, batch consecutive text/thinking deltas for fewer UI updates
        depth: Nesting depth for nested task delegation
        parent_tool_call_id: Tool call ID of the parent task that spawned this subagent

    Returns:
        Final text content from the stream
    """
    if batch_deltas:
        stream = batch_stream_deltas(stream)

    final_content: str = ""
    async for event in stream:
        # Handle nested SubAgentEvents - increment depth
        if isinstance(event, SubAgentEvent):
            nested_event = SubAgentEvent(
                source_name=event.source_name,
                source_type=event.source_type,
                event=event.event,
                depth=event.depth + depth,
                parent_tool_call_id=event.parent_tool_call_id,
            )
            await ctx.events.emit_event(nested_event)
        else:
            # Wrap the event in SubAgentEvent
            subagent_event = SubAgentEvent(
                source_name=source_name,
                source_type=source_type,
                event=event,
                depth=depth,
                parent_tool_call_id=parent_tool_call_id,
            )
            await ctx.events.emit_event(subagent_event)

            # Extract final content from StreamCompleteEvent
            if isinstance(event, StreamCompleteEvent):
                content = event.message.content
                final_content = str(content) if content else ""

    return final_content


async def _stream_task_to_fs(
    ctx: AgentContext,
    fs: AsyncFileSystem,
    task_id: str,
    source_name: str,
    stream: AsyncIterator[RichAgentStreamEvent[Any]],
) -> None:
    """Stream a task's output to internal filesystem.

    Writes streaming output to /tasks/{task_id}/output.md as content arrives. The
    task's activity is not streamed; its end is reported with a ``TaskFinishedEvent``
    (which reaches the parent's stream in its current or a later run).

    Args:
        ctx: Context of the agent that started the task
        fs: The internal filesystem to write to
        task_id: Unique identifier for this task
        source_name: Name of the agent/team executing the task
        stream: Async iterator of stream events from agent.run_stream()
    """
    output_path = f"/tasks/{task_id}/output.md"
    content_parts: list[str] = []
    final_output: str | None = None

    try:
        async for event in stream:
            # Handle nested SubAgentEvents - unwrap inner event
            inner_event = event.event if isinstance(event, SubAgentEvent) else event

            match inner_event:
                case (
                    PartDeltaEvent(delta=TextPartDelta(content_delta=str(text)))
                    | PartDeltaEvent(delta=ThinkingPartDelta(content_delta=str(text)))
                ) if text:
                    content_parts.append(text)
                    # Write incrementally (overwrite with accumulated content)
                    await fs._pipe(output_path, "".join(content_parts).encode("utf-8"))
                case StreamCompleteEvent(message=msg) if msg.content:
                    final_output = str(msg.content)
                    await fs._pipe(output_path, final_output.encode("utf-8"))

        logger.info(
            "Async task completed",
            task_id=task_id,
            source_name=source_name,
            output_path=output_path,
        )
    except asyncio.CancelledError:
        await ctx.events.emit_event(
            TaskFinishedEvent(task_id=task_id, status="cancelled", output_ref=output_path)
        )
        raise
    except Exception as e:
        logger.exception("Async task failed", task_id=task_id, source_name=source_name)
        # Write error to output file
        error_content = f"# Task Failed\n\nTask {task_id} ({source_name}) failed with an error."
        await fs._pipe(output_path, error_content.encode("utf-8"))
        await ctx.events.emit_event(
            TaskFinishedEvent(
                task_id=task_id, status="failed", summary=str(e), output_ref=output_path
            )
        )
    else:
        summary = (final_output or "".join(content_parts))[:500] or None
        await ctx.events.emit_event(
            TaskFinishedEvent(
                task_id=task_id, status="completed", summary=summary, output_ref=output_path
            )
        )


class SubagentTools(StaticResourceProvider):
    """Provider for task delegation tools with streaming progress."""

    def __init__(
        self,
        name: str = "subagent_tools",
        *,
        batch_stream_deltas: bool = False,
    ) -> None:
        super().__init__(name=name)
        self._batch_stream_deltas = batch_stream_deltas
        for tool in [
            self.create_tool(
                self.list_available_nodes, category="search", read_only=True, idempotent=True
            ),
            self.create_tool(self.task, category="other"),
        ]:
            self.add_tool(tool)

    async def list_available_nodes(  # noqa: D417
        self,
        ctx: AgentContext,
        node_type: Literal["all", "agent", "team"] = "all",
        only_idle: bool = False,
    ) -> str:
        """List available agents and/or teams in the current pool.

        Args:
            node_type: Filter by node type - "all", "agent", or "team"
            only_idle: If True, only returns nodes that aren't currently busy

        Returns:
            List of node names that can be used with the task tool
        """
        if ctx.pool is None:
            msg = "No agent pool available"
            raise ToolError(msg)
        lines: list[str] = []
        if node_type in ("all", "agent"):
            agents = dict(ctx.pool.all_agents)
            if only_idle:
                agents = {n: a for n, a in agents.items() if not a.is_busy()}
            for name, agent in agents.items():
                lines.extend([
                    f"name: {name}",
                    "type: agent",
                    f"description: {agent.description or 'No description'}",
                    "---",
                ])

        if node_type in ("all", "team"):  # List teams
            teams = ctx.pool.teams
            if only_idle:
                teams = {name: team for name, team in teams.items() if not team.is_running}
            for name, team in teams.items():
                lines.extend([
                    f"name: {name}",
                    f"description: {team.description or 'No description'}",
                    "---",
                ])

        return "\n".join(lines) if lines else "No nodes available"

    async def task(  # noqa: D417
        self,
        ctx: AgentContext,
        agent_or_team: str,
        prompt: str,
        description: str,
        async_mode: bool = False,
    ) -> str:
        """Execute a task on an agent or team.

        Launch a task to be executed by a specialized agent or team.

        In synchronous mode (default), the task runs with streaming progress events
        and returns the result when complete.

        In async mode, the task starts in the background and returns immediately
        with a task ID. The output is streamed to /tasks/{task_id}/output.md in
        the internal filesystem, which can be read later.

        Args:
            agent_or_team: The agent or team to execute the task
            prompt: The task instructions for the agent or team
            description: A short (3-5 words) description of the task
            async_mode: If True, run in background and return task ID immediately

        Returns:
            In sync mode: The result of the task execution
            In async mode: Task ID and output file path
        """
        from agentpool import Team, TeamRun
        from agentpool.agents.base_agent import BaseAgent
        from agentpool.common_types import SupportsRunStream

        if ctx.pool is None:
            msg = "Agent needs to be in a pool to execute tasks"
            raise ToolError(msg)

        if agent_or_team not in ctx.pool.nodes:
            msg = (
                f"No agent or team found with name: {agent_or_team}. "
                f"Available nodes: {', '.join(ctx.pool.nodes.keys())}"
            )
            raise ModelRetry(msg)

        # Determine source type and get node
        node = ctx.pool.nodes[agent_or_team]
        match node:
            case Team():
                source_type: SubAgentType = "team_parallel"
            case TeamRun():
                source_type = "team_sequential"
            case BaseAgent():
                source_type = "agent"
            case _:
                raise ValueError(f"Unexpected node type: {type(node)}")

        if not isinstance(node, SupportsRunStream):
            msg = f"Node {agent_or_team} does not support streaming"
            raise ToolError(msg)

        logger.info(
            "Executing task",
            agent_or_team=agent_or_team,
            description=description,
            async_mode=async_mode,
        )

        if async_mode:
            # Generate task ID and start background task
            task_id = _generate_task_id(description)
            output_path = f"/tasks/{task_id}/output.md"

            # Create the task directory
            fs = ctx.internal_fs
            fs.mkdirs(f"/tasks/{task_id}", exist_ok=True)
            fs = WrapperFileSystem(fs)
            # Start streaming to filesystem in background
            # Store task reference to prevent garbage collection
            await ctx.events.emit_event(
                TaskStartedEvent(
                    task_id=task_id,
                    kind="agent",
                    description=description,
                    tool_call_id=ctx.tool_call_id,
                    background=True,
                    agent_type=agent_or_team,
                )
            )
            task = asyncio.create_task(
                _stream_task_to_fs(
                    ctx=ctx,
                    fs=fs,
                    task_id=task_id,
                    source_name=agent_or_team,
                    stream=node.run_stream(prompt),
                ),
                name=f"async_task_{task_id}",
            )
            # Add task to a set to prevent GC while running
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)

            return (
                f"Task started in background.\n"
                f"Task ID: {task_id}\n"
                f"Output will be written to: {output_path}\n"
                f"Use the read tool to check the output file for results."
            )

        # Synchronous mode - stream with SubAgentEvent wrapping
        task_id = ctx.tool_call_id or _generate_task_id(description)
        await ctx.events.emit_event(
            TaskStartedEvent(
                task_id=task_id,
                kind="agent",
                description=description,
                tool_call_id=ctx.tool_call_id,
                agent_type=agent_or_team,
            )
        )
        try:
            result = await _stream_task(
                ctx,
                source_name=agent_or_team,
                source_type=source_type,
                stream=node.run_stream(prompt),
                batch_deltas=self._batch_stream_deltas,
                parent_tool_call_id=ctx.tool_call_id,
            )
        except asyncio.CancelledError:
            await ctx.events.emit_event(TaskFinishedEvent(task_id=task_id, status="cancelled"))
            raise
        except Exception as e:
            finished = TaskFinishedEvent(task_id=task_id, status="failed", summary=str(e))
            await ctx.events.emit_event(finished)
            raise
        await ctx.events.emit_event(TaskFinishedEvent(task_id=task_id, status="completed"))
        return result
