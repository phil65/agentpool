"""Provider for worker agent tools."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentpool.agents.context import AgentContext  # noqa: TC001
from agentpool.log import get_logger
from agentpool.resource_providers import ResourceProvider
from agentpool.tools.exceptions import ToolError


if TYPE_CHECKING:
    from collections.abc import Sequence

    from agentpool.tools.base import Tool
    from agentpool_config.workers import WorkerConfig

logger = get_logger(__name__)


class WorkersTools(ResourceProvider):
    """Provider for worker agent tools.

    Creates tools for each configured worker that delegate to agents/teams in the pool.
    Tools are created lazily when get_tools() is called, using AgentContext to access
    the pool at call time.
    """

    def __init__(self, workers: list[WorkerConfig], name: str = "workers") -> None:
        """Initialize workers toolset.

        Args:
            workers: List of worker configurations
            name: Provider name
        """
        super().__init__(name=name)
        self.workers = workers

    async def get_tools(self) -> Sequence[Tool]:
        """Get tools for all configured workers."""
        return [self._create_worker_tool(i) for i in self.workers]

    def _create_worker_tool(self, worker_config: WorkerConfig) -> Tool:
        """Create a tool for a single worker configuration."""
        from agentpool_config.workers import AgentWorkerConfig

        worker_name = worker_config.name
        # Regular agents get history management
        if isinstance(worker_config, AgentWorkerConfig):
            return self._create_agent_tool(
                worker_name,
                reset_history_on_run=worker_config.reset_history_on_run,
                pass_message_history=worker_config.pass_message_history,
            )
        # Teams, ACP agents, AGUI agents - all handled uniformly
        return self._create_node_tool(worker_name)

    def _create_agent_tool(
        self,
        agent_name: str,
        *,
        reset_history_on_run: bool = True,
        pass_message_history: bool = False,
    ) -> Tool:
        """Create tool for a regular agent worker with history management."""

        async def run(ctx: AgentContext, prompt: str) -> Any:
            from agentpool.agents.native_agent import Agent

            if ctx.pool is None:
                msg = "No agent pool available"
                raise ToolError(msg)
            agents = ctx.pool.get_agents()
            if agent_name not in agents:
                msg = f"Agent {agent_name!r} not found in pool"
                raise ToolError(msg)

            worker = agents[agent_name]
            # Only native agents run on the history they are given; external ones keep
            # their native session (see SubagentTools._resolve_child_session)
            if isinstance(worker, Agent) and (pass_message_history or reset_history_on_run):
                child = ctx.agent.active_session.child(
                    worker.name,
                    fork_history=pass_message_history,
                    agent_type=worker.AGENT_TYPE,
                )
                # Not registered: nothing continues a worker's session
                result = await worker.run(prompt, session=child)
                return result.data
            result = await worker.run(prompt)
            return result.data

        normalized_name = agent_name.replace("_", " ").title()
        run.__name__ = f"ask_{agent_name}"
        run.__doc__ = f"Get expert answer from specialized agent: {normalized_name}"
        return self.create_tool(run)

    def _create_node_tool(self, node_name: str) -> Tool:
        """Create tool for non-agent nodes (teams, ACP agents, AGUI agents)."""
        from agentpool import BaseTeam

        async def run(ctx: AgentContext, prompt: str) -> str:
            if ctx.pool is None:
                msg = "No agent pool available"
                raise ToolError(msg)
            if node_name not in ctx.pool.nodes:
                msg = f"Worker {node_name!r} not found in pool"
                raise ToolError(msg)

            worker = ctx.pool.nodes[node_name]
            result = await worker.run(prompt)
            # Teams get formatted output
            if isinstance(worker, BaseTeam):
                return result.format(style="detailed", show_costs=True)
            # Other nodes (ACP, AGUI) just return data as string
            return str(result.data)

        normalized_name = node_name.replace("_", " ").title()
        run.__name__ = f"ask_{node_name}"
        run.__doc__ = f"Delegate task to worker: {normalized_name}"
        return self.create_tool(run)
