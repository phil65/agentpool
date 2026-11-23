"""Agent pool management for collaboration."""

from __future__ import annotations

import asyncio
from asyncio import Lock
from contextlib import AsyncExitStack, asynccontextmanager, suppress
import os
from typing import TYPE_CHECKING, Any, Self, assert_never, overload

from anyenv import ProcessManager
import anyio
from upathtools import UPath

from agentpool.agents.definition import AgentDefinition
from agentpool.common_types import NodeName, SupportsStructuredOutput
from agentpool.delegation.message_flow_tracker import MessageFlowTracker
from agentpool.log import get_logger
from agentpool.messaging import MessageNode
from agentpool.talk import TeamTalk
from agentpool.talk.router import Router
from agentpool.tasks import TaskRegistry
from agentpool.utils.baseregistry import BaseRegistry
from agentpool.utils.inspection import get_fn_name


if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Sequence
    from contextlib import AbstractAsyncContextManager
    from types import TracebackType

    from upathtools import JoinablePathLike

    from agentpool.agents.base_agent import BaseAgent
    from agentpool.common_types import AgentName, AnyEventHandlerType
    from agentpool.delegation.base_team import BaseTeam
    from agentpool.delegation.team import Team
    from agentpool.delegation.teamrun import TeamRun
    from agentpool.messaging.compaction import CompactionPipeline
    from agentpool.models.manifest import AgentsManifest
    from agentpool.ui.base import InputProvider
    from agentpool_config.task import Job


logger = get_logger(__name__)


class AgentPool[TPoolDeps = None](BaseRegistry[NodeName, MessageNode[Any, Any]]):
    """Pool managing message processing nodes (agents and teams).

    Acts as a unified registry for all nodes, providing:
    - Centralized node management and lookup
    - Shared dependency injection
    - Connection management
    - Resource coordination

    Nodes can be accessed through:
    - nodes: All registered nodes (agents and teams)
    - agents: Only Agent instances
    - teams: Only Team instances
    """

    def __init__(  # noqa: PLR0915
        self,
        manifest: JoinablePathLike | AgentsManifest | None = None,
        *,
        shared_deps_type: type[TPoolDeps] | None = None,
        connect_nodes: bool = True,
        input_provider: InputProvider | None = None,
        parallel_load: bool = True,
        event_handlers: list[AnyEventHandlerType] | None = None,
        main_agent_name: str | None = None,
    ) -> None:
        """Initialize agent pool with immediate agent creation.

        Args:
            manifest: Agent configuration manifest
            shared_deps_type: Dependencies to share across all nodes
            connect_nodes: Whether to set up forwarding connections
            input_provider: Input provider for tool / step confirmations / HumanAgents
            parallel_load: Whether to load nodes in parallel (async)
            event_handlers: Event handlers to pass through to all agents
            main_agent_name: Name of the main agent (overrides manifest.default_agent)

        Raises:
            ValueError: If manifest contains invalid node configurations
            RuntimeError: If node initialization fails
        """
        from agentpool.mcp_server.manager import MCPManager
        from agentpool.models.manifest import AgentsManifest
        from agentpool.observability import registry
        from agentpool.prompts.manager import PromptManager
        from agentpool.sessions import SessionRegistry
        from agentpool.skills.manager import SkillsManager
        from agentpool.storage import StorageManager
        from agentpool.utils.file_ops_tracker import FileOpsTracker
        from agentpool.utils.todos import TodoTracker
        from agentpool.vfs_registry import VFSRegistry
        from agentpool_toolsets.builtin.debug import install_memory_handler

        super().__init__()
        self.manifest: AgentsManifest
        match manifest:
            case None:
                self.manifest = AgentsManifest()
            case AgentsManifest():
                self.manifest = manifest
            case str() | os.PathLike() | UPath():
                self.manifest = AgentsManifest.from_file(manifest)
            case _:
                raise ValueError(f"Invalid config type: {type(manifest)}")
        registry.configure_observability(self.manifest.observability)
        self._memory_log_handler = install_memory_handler()
        self.shared_deps_type = shared_deps_type
        self.connect_nodes = connect_nodes
        self._input_provider = input_provider
        self.exit_stack = AsyncExitStack()
        self.parallel_load = parallel_load
        self.storage = StorageManager(self.manifest.storage)
        self.sessions = SessionRegistry(storage=self.storage)
        self.vfs_registry = VFSRegistry()
        for name, resource_config in self.manifest.resources.items():
            self.vfs_registry.register_from_config(name, resource_config)
        self.event_handlers = event_handlers or []
        self.router = Router()
        """Owns the connections between the pool's nodes and routes along them."""
        self.connection_registry = self.router.registry
        servers = self.manifest.get_mcp_servers()
        self.mcp = MCPManager(name="pool_mcp", servers=servers, owner="pool")
        self.skills = SkillsManager(name="pool_skills", owner="pool")
        self._tasks = TaskRegistry()
        self.prompt_manager = PromptManager(self.manifest.prompts)
        # Main agent name: explicit param > manifest.default_agent > None (will use first)
        self._main_agent_name = main_agent_name or self.manifest.default_agent
        # Register tasks from manifest
        for name, task in self.manifest.jobs.items():
            self._tasks.register(name, task)
        self.process_manager = ProcessManager()
        self.file_ops = FileOpsTracker()
        self.todos = TodoTracker()
        # Create all agents from unified manifest.agents dict
        for name, config in self.manifest.agents.items():
            # Ensure name is set on config
            cfg = config.model_copy(update={"name": name}) if config.name is None else config
            agent: BaseAgent[TPoolDeps] = cfg.get_agent(
                event_handlers=self.event_handlers,
                input_provider=self._input_provider,
                pool=self,
                deps_type=shared_deps_type,
            )
            agent.definition = AgentDefinition.from_config(cfg)  # Sessions record its version
            self.register(name, agent)

        self._create_teams()
        if connect_nodes:
            self._connect_nodes()
        self.pool_talk = TeamTalk[Any].from_nodes(list(self.nodes.values()))
        self._enter_lock = Lock()  # Initialize async safety fields
        self._running_count = 0

    async def __aenter__(self) -> Self:
        """Enter async context and initialize all agents."""
        if self._running_count > 0:
            self._running_count += 1
            return self
        async with self._enter_lock:
            try:
                # Initialize MCP manager first, then add aggregating provider
                await self.exit_stack.enter_async_context(self.mcp)
                await self.exit_stack.enter_async_context(self.skills)
                aggregating_provider = self.mcp.get_aggregating_provider()
                agents = list(self.all_agents.values())
                teams = list(self.teams.values())
                for agent in agents:
                    agent.tools.add_provider(aggregating_provider)
                # Initialize storage
                await self.exit_stack.enter_async_context(self.storage)
                # Initialize agents and teams (can be parallel)
                comps: list[AbstractAsyncContextManager[Any]] = [*agents, *teams]
                node_inits = [self.exit_stack.enter_async_context(c) for c in comps]
                if self.parallel_load:
                    await asyncio.gather(*node_inits)
                else:
                    for init in node_inits:
                        await init

            except Exception as e:
                await self.cleanup()
                msg = "Failed to initialize agent pool"
                logger.exception(msg, exc_info=e)
                raise RuntimeError(msg) from e
        self._running_count += 1
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Exit async context."""
        if self._running_count == 0:
            raise ValueError("AgentPool.__aexit__ called more times than __aenter__")
        async with self._enter_lock:
            self._running_count -= 1
            if self._running_count == 0:
                # Remove MCP aggregating provider from all agents
                aggregating_provider = self.mcp.get_aggregating_provider()
                for agent in self.get_agents().values():
                    agent.tools.remove_provider(aggregating_provider.name)
                await self.cleanup()

    @property
    def is_running(self) -> bool:
        """Check if the agent pool is running."""
        return bool(self._running_count)

    async def cleanup(self) -> None:
        """Clean up all agents."""
        # Clean up background processes
        await self.process_manager.cleanup()
        await self.exit_stack.aclose()
        self.clear()

    @overload
    def create_team_run[TDeps, TResult](
        self,
        agents: Sequence[MessageNode[TDeps, Any]],
        validator: MessageNode[Any, TResult] | None = None,
        *,
        name: str | None = None,
        description: str | None = None,
        shared_prompt: str | None = None,
    ) -> TeamRun[TDeps, TResult]: ...

    @overload
    def create_team_run[TResult](
        self,
        agents: Sequence[MessageNode[Any, Any]],
        validator: MessageNode[Any, TResult] | None = None,
        *,
        name: str | None = None,
        description: str | None = None,
        shared_prompt: str | None = None,
    ) -> TeamRun[Any, TResult]: ...

    def create_team_run[TResult](
        self,
        agents: Sequence[MessageNode[Any, Any]],
        validator: MessageNode[Any, TResult] | None = None,
        *,
        name: str | None = None,
        description: str | None = None,
        shared_prompt: str | None = None,
    ) -> TeamRun[Any, TResult]:
        """Create a a sequential TeamRun from a list of Agents.

        Args:
            agents: List of agent names or team/agent instances (all if None)
            validator: Node to validate the results of the TeamRun
            name: Optional name for the team
            description: Optional description for the team
            shared_prompt: Optional prompt for all agents
        """
        from agentpool.delegation.teamrun import TeamRun

        team = TeamRun(
            agents,
            name=name,
            description=description,
            validator=validator,
            shared_prompt=shared_prompt,
        )
        if name:
            self[name] = team
        return team

    @overload
    def create_team[TDeps](
        self,
        agents: Sequence[MessageNode[TDeps, Any]],
        *,
        name: str | None = None,
        description: str | None = None,
        shared_prompt: str | None = None,
    ) -> Team[TDeps]: ...

    @overload
    def create_team(
        self,
        agents: Sequence[MessageNode[Any, Any]],
        *,
        name: str | None = None,
        description: str | None = None,
        shared_prompt: str | None = None,
    ) -> Team[Any]: ...

    def create_team(
        self,
        agents: Sequence[MessageNode[Any, Any]],
        *,
        name: str | None = None,
        description: str | None = None,
        shared_prompt: str | None = None,
    ) -> Team[Any]:
        """Create a group from agent names or instances.

        Args:
            agents: List of agent names or instances (all if None)
            name: Optional name for the team
            description: Optional description for the team
            shared_prompt: Optional prompt for all agents
        """
        from agentpool.delegation.team import Team

        team = Team(agents, name=name, description=description, shared_prompt=shared_prompt)
        if name:
            self[name] = team
        return team

    @asynccontextmanager
    async def track_message_flow(self) -> AsyncGenerator[MessageFlowTracker]:
        """Track message flow during a context."""
        tracker = MessageFlowTracker()
        self.connection_registry.message_flow.connect(tracker.track)
        try:
            yield tracker
        finally:
            self.connection_registry.message_flow.disconnect(tracker.track)

    async def run_event_loop(self) -> None:
        """Run pool in event-watching mode until interrupted."""
        print("Starting event watch mode...")
        print("Active nodes: ", ", ".join(list(self.nodes.keys())))
        print("Press Ctrl+C to stop")

        shutdown_event = anyio.Event()
        with suppress(KeyboardInterrupt):
            await shutdown_event.wait()

    @overload
    def get_agents(self) -> dict[str, BaseAgent[Any, Any]]: ...

    @overload
    def get_agents[TAgent: BaseAgent[Any, Any]](
        self, agent_type: type[TAgent]
    ) -> dict[str, TAgent]: ...

    def get_agents[TAgent: BaseAgent[Any, Any]](
        self,
        agent_type: type[TAgent] | None = None,
    ) -> dict[str, TAgent] | dict[str, BaseAgent[Any, Any]]:
        """Get agents filtered by type.

        Args:
            agent_type: Optional agent type to filter by. If None, returns all agents.

        Returns:
            Dictionary mapping agent names to agent instances.
        """
        from agentpool.agents.base_agent import BaseAgent

        filter_type = agent_type or BaseAgent
        return {i.name: i for i in self._items.values() if isinstance(i, filter_type)}

    @property
    def all_agents(self) -> dict[str, BaseAgent[Any, Any]]:
        """Get all agents (regular, ACP, and AG-UI)."""
        return self.get_agents()

    @property
    def main_agent(self) -> BaseAgent[Any, Any]:
        """Get the main agent.

        Returns the agent specified by main_agent_name constructor param,
        manifest.default_agent, or falls back to the first agent.

        Raises:
            RuntimeError: If no agents are available.
            ValueError: If the specified main agent doesn't exist.
        """
        agents = self.all_agents
        if not agents:
            raise RuntimeError("No agents available in pool")

        if self._main_agent_name:
            if self._main_agent_name not in agents:
                available = list(agents.keys())
                msg = f"Main agent {self._main_agent_name!r} not found. Available: {available}"
                raise ValueError(msg)
            return agents[self._main_agent_name]

        # Fallback to first agent
        return next(iter(agents.values()))

    @property
    def teams(self) -> dict[str, BaseTeam[Any, Any]]:
        """Get agents dict (backward compatibility)."""
        from agentpool.delegation.base_team import BaseTeam

        return {i.name: i for i in self._items.values() if isinstance(i, BaseTeam)}

    @property
    def nodes(self) -> dict[str, MessageNode[Any, Any]]:
        """Get agents dict (backward compatibility)."""
        return {i.name: i for i in self._items.values()}

    @property
    def compaction_pipeline(self) -> CompactionPipeline | None:
        """Get the configured compaction pipeline or None if not configured."""
        return self.manifest.get_compaction_pipeline()

    def _validate_item(self, item: MessageNode[Any, Any] | Any) -> MessageNode[Any, Any]:
        """Validate and convert items before registration.

        Raises:
            AgentPoolError: If item is not a valid node
        """
        if not isinstance(item, MessageNode):
            raise self._error_class(f"Item must be Agent or Team, got {type(item)}")
        item.agent_pool = self
        _ = item.router  # Its own router's connections move to the pool's now
        return item

    def _create_teams(self) -> None:
        """Create all teams in two phases to allow nesting."""
        # Phase 1: Create empty teams
        empty_teams: dict[str, BaseTeam[Any, Any]] = {}
        for name, config in self.manifest.teams.items():
            empty_teams[name] = config.get_team([], name=name)
        # Phase 2: Resolve members
        for name, config in self.manifest.teams.items():
            team = empty_teams[name]
            members: list[MessageNode[Any, Any]] = []
            agents = self.all_agents
            for member in config.members:
                if member in agents:
                    members.append(agents[member])
                elif member in empty_teams:
                    members.append(empty_teams[member])
                else:
                    raise ValueError(f"Unknown team member: {member}")
            team.nodes.extend(members)
            self[name] = team

    def _connect_nodes(self) -> None:
        """Set up connections defined in manifest."""
        # Merge agent and team configs into one dict of nodes with connections
        for name, config in self.manifest.nodes.items():
            source = self[name]
            for target in config.connections or []:
                target.connect_nodes(source, list(self.all_agents.values()), name)

    @overload
    def get_agent[TResult = str](
        self,
        agent: AgentName | BaseAgent[Any, Any],
        *,
        output_type: type[TResult] = str,  # type: ignore[assignment]  # ty:ignore[invalid-parameter-default]
    ) -> BaseAgent[TPoolDeps, TResult]: ...

    @overload
    def get_agent[TCustomDeps, TResult = str](
        self,
        agent: AgentName | BaseAgent[Any, Any],
        *,
        deps_type: type[TCustomDeps],
        output_type: type[TResult] = str,  # type: ignore[assignment]  # ty:ignore[invalid-parameter-default]
    ) -> BaseAgent[TCustomDeps, TResult]: ...

    def get_agent(
        self,
        agent: AgentName | BaseAgent[Any, Any],
        *,
        deps_type: Any | None = None,
        output_type: Any = str,
    ) -> BaseAgent[Any, Any]:
        """Get or configure an agent from the pool.

        This method provides flexible agent configuration with dependency injection:
        - Without deps: Agent uses pool's shared dependencies
        - With deps: Agent uses provided custom dependencies

        Args:
            agent: Either agent name or instance
            deps_type: Optional custom dependencies type (overrides shared deps)
            output_type: Optional type for structured responses

        Returns:
            Either:
            - Agent[TPoolDeps] when using pool's shared deps
            - Agent[TCustomDeps] when custom deps provided

        Raises:
            KeyError: If agent name not found
            ValueError: If configuration is invalid
        """
        from agentpool.agents.base_agent import BaseAgent

        match agent:
            case BaseAgent():
                base = agent
            case str():
                base = self.get_agents()[agent]
            case _ as unreachable:
                assert_never(unreachable)
        # Use custom deps if provided, otherwise use shared deps
        # base.context.data = deps if deps is not None else self.shared_deps
        base.deps_type = deps_type
        base.agent_pool = self
        if isinstance(base, SupportsStructuredOutput):
            base.to_structured(output_type)
        return base

    def get_job(self, name: str) -> Job[Any, Any]:
        return self._tasks[name]

    def register_task(self, name: str, task: Job[Any, Any]) -> None:
        self._tasks.register(name, task)

    async def add_agent(self, agent: BaseAgent[Any, Any]) -> None:
        """Add a new permanent agent to the pool."""
        from agentpool.agents.events import resolve_event_handlers

        if agent.agent_pool is not None:
            raise ValueError("Agent is already part of a pool")
        for handler in resolve_event_handlers(self.event_handlers):
            agent.event_handler.add_handler(handler)
        # Add MCP aggregating provider from manager
        agent.tools.add_provider(self.mcp.get_aggregating_provider())
        agent = await self.exit_stack.enter_async_context(agent)
        self.register(agent.name, agent)

    def get_mermaid_diagram(self, include_details: bool = True) -> str:
        """Generate mermaid flowchart of all agents and their connections.

        Args:
            include_details: Whether to show connection details (types, queues, etc)
        """
        declare_lines = []
        connect_lines = []
        # Add all connections as edges
        for agent in self.all_agents.values():
            declare_lines.append(f"    {agent.name}[{agent.display_name}]")
            for talk in agent.connections.get_connections():
                source = talk.source.name
                for target in talk.targets:
                    if include_details:
                        details: list[str] = []
                        details.append(talk.connection_type)
                        if talk.queued:
                            details.append(f"queued({talk.queue_strategy})")
                        if fn := talk.filter_condition:
                            details.append(f"filter:{get_fn_name(fn)}")
                        if fn := talk.stop_condition:
                            details.append(f"stop:{get_fn_name(fn)}")
                        if fn := talk.exit_condition:
                            details.append(f"exit:{get_fn_name(fn)}")

                        label = f"|{' '.join(details)}|" if details else ""
                        connect_lines.append(f"    {source}--{label}-->{target.name}")
                    else:
                        connect_lines.append(f"    {source}-->{target.name}")
        all_lines = ["flowchart LR", *declare_lines, *connect_lines]
        return "\n".join(all_lines)


if __name__ == "__main__":

    async def main() -> None:
        path = "src/agentpool/config_resources/agents.yml"
        async with AgentPool(path) as pool:
            agent = pool.get_agent("overseer")
            print(agent)

    anyio.run(main)
