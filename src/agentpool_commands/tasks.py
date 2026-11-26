"""Tasks of the agent's turns: list them, stop one, read its output."""

from typing import TYPE_CHECKING, Any

from slashed import CommandContext, CommandError

from agentpool.agents.context import AgentContext  # noqa: TC001
from agentpool_commands.base import NodeCommand


if TYPE_CHECKING:
    from agentpool.agents.agent_tasks import RunningTask
    from agentpool.messaging import MessageNode


MAX_OUTPUT_CHARS = 8_000
"""Output shown by ``/tasks output`` (its end: the latest)."""


class TasksCommand(NodeCommand):
    """Show the tasks the agent's turns started, or stop one, or show its output.

    Tasks are subagent runs and background commands that still run, also those whose
    turn has ended.

    Examples:
      /tasks                   The running tasks
      /tasks output a1b2c3     What the task has written so far
      /tasks stop a1b2c3       Stop it
    """

    name = "tasks"
    category = "agents"

    @classmethod
    def supports_node(cls, node: MessageNode[Any, Any]) -> bool:
        from agentpool.agents.base_agent import BaseAgent

        return isinstance(node, BaseAgent)

    async def execute_command(
        self,
        ctx: CommandContext[AgentContext],
        action: str | None = None,
        task_id: str | None = None,
    ) -> None:
        """Show the running tasks, or act on one.

        Args:
            ctx: Command context
            action: stop or output (none: list the tasks)
            task_id: The task to act on, as the list shows it
        """
        from agentpool.agents.exceptions import UnsupportedOperationError

        agent = ctx.context.agent
        if action is None:
            await ctx.print(_task_table(agent.running_tasks()))
            return
        if action not in ("stop", "output"):
            raise CommandError(f"Unknown action {action!r}: use stop or output, or none to list")
        if task_id is None:
            raise CommandError(f"Which task? /tasks {action} <task id>")
        try:
            if action == "stop":
                await agent.stop_task(task_id)
                await ctx.print(f"Stopping task **{task_id}**")
                return
            result = await agent.task_output(task_id)
        except KeyError as e:
            raise CommandError(str(e.args[0])) from e
        except UnsupportedOperationError as e:
            raise CommandError(str(e)) from e
        text = result.output[-MAX_OUTPUT_CHARS:]
        cut = result.truncated or len(result.output) > MAX_OUTPUT_CHARS
        note = f"\n\n(last {len(text)} of {result.total_bytes} bytes)" if cut else ""
        await ctx.print(f"## Output of {task_id}\n\n```\n{text}\n```{note}")


def _task_table(tasks: list[RunningTask]) -> str:
    from agentpool_commands.markdown_utils import format_table

    if not tasks:
        return "No tasks running."
    rows = [
        {
            "ID": task.task_id,
            "Kind": task.kind,
            "Description": task.description,
            "Background": "yes" if task.background else "no",
            "Since": task.started_at.strftime("%H:%M:%S"),
            "Latest": task.message or "",
        }
        for task in sorted(tasks, key=lambda t: t.started_at)
    ]
    headers = ["ID", "Kind", "Description", "Background", "Since", "Latest"]
    return f"## Running tasks\n\n{format_table(headers, rows)}"
