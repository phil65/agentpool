"""Task execution command."""

import asyncio
from typing import TYPE_CHECKING, Annotated

import typer as t

from agentpool_cli import log, resolve_agent_config


if TYPE_CHECKING:
    from agentpool import AgentsManifest


TASK_HELP = """
Execute a defined task with the specified agent.

Example:
    agentpool task docs write_api_docs --prompt "Include code examples"
    agentpool task docs write_api_docs --log-level DEBUG
"""


async def execute_job(
    agent_name: str,
    task_name: str,
    config: AgentsManifest,
    *,
    prompt: str | None = None,
) -> str:
    """Execute task with agent."""
    from agentpool import AgentPool

    async with AgentPool(config) as pool:
        # Get both agent and task
        agent = pool.get_agent(agent_name)
        task = pool.get_job(task_name)

        # Create final prompt from task and additional input
        task_prompt = task.prompt
        if prompt:
            task_prompt = f"{task_prompt}\n\nAdditional instructions:\n{prompt}"

        result = await agent.run(task_prompt)
        return result.data


def task_command(
    agent_name: Annotated[str, t.Argument(help="Name of agent to run task with")],
    task_name: Annotated[str, t.Argument(help="Name of task to execute")],
    config: Annotated[
        str | None, t.Option("--config", "-c", help="Agent configuration file")
    ] = None,
    prompt: Annotated[str | None, t.Option("--prompt", "-p", help="Additional prompt")] = None,
) -> None:
    """Execute a task with the specified agent."""
    from agentpool import AgentsManifest

    logger = log.get_logger(__name__)
    try:
        logger.debug("Starting task execution", name=task_name)
        try:
            config_path = resolve_agent_config(config)
        except ValueError as e:
            msg = str(e)
            raise t.BadParameter(msg) from e

        manifest = AgentsManifest.from_file(config_path)
        result = asyncio.run(execute_job(agent_name, task_name, manifest, prompt=prompt))
        print(result)

    except Exception as e:
        t.echo(f"Error: {e}", err=True)
        logger.debug("Exception details", exc_info=True)
        raise t.Exit(1) from e


if __name__ == "__main__":
    t.run(task_command)
