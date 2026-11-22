"""Base class for teams."""

from __future__ import annotations

from abc import abstractmethod
import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, overload

import anyio

from agentpool.log import get_logger
from agentpool.messaging import MessageNode
from agentpool.messaging.context import NodeContext
from agentpool.talk.stats import AggregatedMessageStats


if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator, Sequence

    from evented_config import EventConfig

    from agentpool import Agent, AgentPool, Team
    from agentpool.agents.base_agent import BaseAgent
    from agentpool.agents.events import RichAgentStreamEvent
    from agentpool.common_types import ProcessorCallback, PromptCompatible
    from agentpool.delegation.teamrun import ExtendedTeamTalk, TeamRun
    from agentpool.messaging import ChatMessage, TeamResponse
    from agentpool.sessions import Session
    from agentpool.talk.stats import AggregatedTalkStats
    from agentpool.ui.base import InputProvider
    from agentpool_config.mcp_server import MCPServerConfig

logger = get_logger(__name__)


@dataclass(kw_only=True)
class TeamContext[TDeps = object](NodeContext[TDeps]):
    """Context for team nodes."""

    pool: AgentPool | None = None
    """Pool the team is part of."""


class BaseTeam[TDeps, TResult](MessageNode[TDeps, TResult]):
    """Base class for Team and TeamRun."""

    def __init__(
        self,
        agents: Sequence[MessageNode[TDeps, TResult]],
        *,
        name: str | None = None,
        description: str | None = None,
        display_name: str | None = None,
        shared_prompt: str | None = None,
        mcp_servers: Sequence[str | MCPServerConfig] | None = None,
        event_configs: Sequence[EventConfig] | None = None,
        agent_pool: AgentPool | None = None,
    ) -> None:
        """Common variables only for typing."""
        from agentpool.delegation.teamrun import ExtendedTeamTalk

        self._name = name or " & ".join([i.name for i in agents])
        self._nodes: list[MessageNode[Any, Any]] = []
        super().__init__(
            name=self._name,
            display_name=display_name,
            mcp_servers=mcp_servers,
            description=description,
            event_configs=event_configs,
            agent_pool=agent_pool,
        )
        for agent in agents:
            self.add_node(agent)
        self._team_talk = ExtendedTeamTalk()
        self.shared_prompt = shared_prompt
        self._main_task: asyncio.Task[ChatMessage[Any] | None] | None = None
        self._infinite = False

    @property
    def nodes(self) -> list[MessageNode[Any, Any]]:
        return self._nodes

    async def member_session(
        self, member: MessageNode[Any, Any], session: Session | None
    ) -> Session | None:
        """The session ``member`` runs a turn of ``session`` in: a child session of it.

        One per member and team session (``<session>/<member>``), kept in the pool's
        registry, so it persists and resumes with the team's session. None (the member's
        own session) without a team session, or for agents with one native session per
        instance (``capabilities.concurrent_sessions``), whose turns are serialized.
        """
        from agentpool.agents.base_agent import BaseAgent as AgentBase

        if session is None:
            return None
        if isinstance(member, AgentBase) and not member.capabilities.concurrent_sessions:
            return None
        pool = member.agent_pool or self.agent_pool
        if pool is None:
            return None
        agent_type = member.AGENT_TYPE if isinstance(member, AgentBase) else None
        return await pool.sessions.open(
            member_session_id(session.session_id, member),
            member.name,
            parent_id=session.session_id,
            cwd=session.data.cwd,
            agent_type=agent_type,
        )

    async def member_stream(
        self,
        member: MessageNode[Any, Any],
        prompts: Sequence[PromptCompatible],
        session: Session | None,
        **kwargs: Any,
    ) -> AsyncIterator[RichAgentStreamEvent[Any]]:
        """A member's turn of the team turn of ``session``, in its child session.

        Agents that serialize their sessions' turns take their turn lock, as for any
        caller (``BaseAgent.session_turn``).
        """
        from agentpool.agents.base_agent import BaseAgent as AgentBase

        child = await self.member_session(member, session)
        if isinstance(member, AgentBase):
            async with member.session_turn():
                async for event in member.run_stream(*prompts, session=child, **kwargs):
                    yield event
        elif isinstance(member, BaseTeam):
            async for event in member.run_stream(*prompts, session=child, **kwargs):
                yield event
        else:
            msg = f"Node {member.name} does not support streaming"
            raise TypeError(msg)

    def add_node(self, node: MessageNode[Any, Any]) -> None:
        """Handler for adding new nodes to the team."""
        from agentpool.agents import Agent

        self._nodes.append(node)
        if isinstance(node, Agent):
            # Add MCP aggregating provider from manager
            aggregating_provider = self.mcp.get_aggregating_provider()
            node.tools.add_provider(aggregating_provider)

    def remove_node(self, node: MessageNode[Any, Any]) -> None:
        """Handler for removing nodes from the team."""
        from agentpool.agents import Agent

        self._nodes.remove(node)
        if isinstance(node, Agent):
            # Remove MCP aggregating provider
            aggregating_provider = self.mcp.get_aggregating_provider()
            node.tools.remove_provider(aggregating_provider.name)

    def __repr__(self) -> str:
        """Create readable representation."""
        members = ", ".join(node.name for node in self.nodes)
        name = f" ({self.name})" if self.name else ""
        return f"{self.__class__.__name__}[{len(self.nodes)}]{name}: {members}"

    def __len__(self) -> int:
        """Get number of team members."""
        return len(self.nodes)

    def __iter__(self) -> Iterator[MessageNode[TDeps, TResult]]:
        """Iterate over team members."""
        return iter(self.nodes)

    def __getitem__(self, index_or_name: int | str) -> MessageNode[TDeps, TResult]:
        """Get team member by index or name."""
        if isinstance(index_or_name, str):
            return next(node for node in self.nodes if node.name == index_or_name)
        return self.nodes[index_or_name]

    def __or__(
        self,
        other: Agent[Any, Any] | ProcessorCallback[Any] | BaseTeam[Any, Any],
    ) -> TeamRun[Any, Any]:
        """Create a sequential pipeline."""
        from agentpool.agents import Agent
        from agentpool.delegation.teamrun import TeamRun

        # Handle conversion of callables first
        if callable(other):
            other = Agent.from_callback(other, agent_pool=self.agent_pool)

        # If we're already a TeamRun, extend it
        if isinstance(self, TeamRun):
            if self.validator:
                # If we have a validator, create new TeamRun to preserve validation
                return TeamRun([self, other])
            self.nodes.append(other)
            return self
        # Otherwise create new TeamRun
        return TeamRun([self, other])

    @overload
    def __and__(self, other: Team[None]) -> Team[None]: ...

    @overload
    def __and__(self, other: Team[TDeps]) -> Team[TDeps]: ...

    @overload
    def __and__(self, other: Team[Any]) -> Team[Any]: ...

    @overload
    def __and__(self, other: Agent[TDeps, Any]) -> Team[TDeps]: ...

    @overload
    def __and__(self, other: Agent[Any, Any]) -> Team[Any]: ...

    def __and__(self, other: Team[Any] | Agent[Any, Any] | ProcessorCallback[Any]) -> Team[Any]:
        """Combine teams, preserving type safety for same types."""
        from agentpool.agents import Agent
        from agentpool.delegation.team import Team

        if callable(other):
            other = Agent.from_callback(other, agent_pool=self.agent_pool)

        match other:
            case Team():
                # Flatten when combining Teams
                return Team([*self.nodes, *other.nodes])
            case _:
                # Everything else just becomes a member
                return Team([*self.nodes, other])

    async def get_stats(self) -> AggregatedMessageStats:
        """Get aggregated stats from all team members."""
        stats = [await node.get_stats() for node in self.nodes]
        return AggregatedMessageStats(stats=stats)

    @property
    def is_running(self) -> bool:
        """Whether execution is currently running."""
        return bool(self._main_task and not self._main_task.done())

    def is_busy(self) -> bool:
        """Check if team is processing any tasks."""
        return bool(self.task_manager._pending_tasks or self._main_task)

    async def stop(self) -> None:
        """Stop background execution if running."""
        if self._main_task and not self._main_task.done():
            self._main_task.cancel()
            await self._main_task
        self._main_task = None
        await self.task_manager.cleanup_tasks()

    async def wait(self) -> ChatMessage[Any] | None:
        """Wait for background execution to complete and return last message."""
        if not self._main_task:
            raise RuntimeError("No execution running")
        if self._infinite:
            raise RuntimeError("Cannot wait on infinite execution")
        try:
            return await self._main_task
        finally:
            await self.task_manager.cleanup_tasks()
            self._main_task = None

    async def run_in_background(
        self,
        *prompts: PromptCompatible | None,
        max_count: int | None = 1,  # 1 = single execution, None = indefinite
        interval: float = 1.0,
        **kwargs: Any,
    ) -> ExtendedTeamTalk:
        """Start execution in background.

        Args:
            prompts: Prompts to execute
            max_count: Maximum number of executions (None = run indefinitely)
            interval: Seconds between executions
            **kwargs: Additional args for execute()
        """
        if self._main_task:
            raise RuntimeError("Execution already running")
        self._infinite = max_count is None

        async def _continuous() -> ChatMessage[Any] | None:
            count = 0
            last_message = None
            while max_count is None or count < max_count:
                try:
                    result = await self.execute(*prompts, **kwargs)
                    last_message = result[-1].message if result else None
                    count += 1
                    if max_count is None or count < max_count:
                        await anyio.sleep(interval)
                except asyncio.CancelledError:
                    logger.debug("Background execution cancelled")
                    break
            return last_message

        self._main_task = self.task_manager.create_task(_continuous(), name="main_execution")
        return self._team_talk

    @property
    def execution_stats(self) -> AggregatedTalkStats:
        """Get current execution statistics."""
        return self._team_talk.stats

    @property
    def talk(self) -> ExtendedTeamTalk:
        """Get current connection."""
        return self._team_talk

    @property
    async def cancel(self) -> None:
        """Cancel execution and cleanup."""
        if self._main_task:
            self._main_task.cancel()
        await self.task_manager.cleanup_tasks()

    def get_structure_diagram(self) -> str:
        """Generate mermaid flowchart of node hierarchy."""
        from agentpool.delegation.base_team import BaseTeam

        lines = ["flowchart TD"]

        def add_node(node: MessageNode[Any, Any], parent: str | None = None) -> None:
            """Recursively add node and its members to diagram."""
            node_id = f"node_{id(node)}"
            lines.append(f"    {node_id}[{node.name}]")
            if parent:
                lines.append(f"    {parent} --> {node_id}")
            # If it's a team, recursively add its members
            if isinstance(node, BaseTeam):
                for member in node.nodes:
                    add_node(member, node_id)

        # Start with root nodes (team members)
        for node in self.nodes:
            add_node(node)

        return "\n".join(lines)

    def iter_agents(self) -> Iterator[BaseAgent[Any, Any]]:
        """Recursively iterate over all child agents."""
        from agentpool.agents.base_agent import BaseAgent

        for node in self.nodes:
            match node:
                case BaseTeam():
                    yield from node.iter_agents()
                case BaseAgent():
                    yield node
                case _:
                    raise ValueError(f"Invalid node type: {type(node)}")

    def get_context(
        self,
        data: Any = None,
        input_provider: InputProvider | None = None,
    ) -> TeamContext:
        """Create a new context for this team.

        Args:
            data: Optional custom data to attach to the context
            input_provider: Optional input provider override

        Returns:
            A new TeamContext instance

        Raises:
            ValueError: If team members belong to different pools
        """
        pool_ids: set[int] = set()
        shared_pool: AgentPool | None = None

        for agent in self.iter_agents():
            if pool := agent.agent_pool:
                pool_id = id(pool)
                if pool_id not in pool_ids:
                    pool_ids.add(pool_id)
                    shared_pool = pool

        if not pool_ids:
            logger.debug("No pool found for team.", team=self.name)
            return TeamContext(
                node=self,
                pool=shared_pool,
                input_provider=input_provider,
                data=data,
            )

        if len(pool_ids) > 1:
            raise ValueError(f"Team members in {self.name} belong to different pools")
        return TeamContext(node=self, pool=shared_pool, input_provider=input_provider, data=data)

    @abstractmethod
    async def execute(
        self,
        *prompts: PromptCompatible | None,
        **kwargs: Any,
    ) -> TeamResponse: ...

    @abstractmethod
    def run_stream(
        self,
        *prompts: PromptCompatible,
        session: Session | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[RichAgentStreamEvent[Any]]:
        """Run the team's turn, streaming its members' events (wrapped in SubAgentEvent).

        With ``session``, a turn of it: members run in child sessions of it (see
        :meth:`member_session`). Without, in their own sessions.
        """
        ...


def member_session_id(session_id: str, member: MessageNode[Any, Any]) -> str:
    """ID of the child session ``member`` runs a team turn of session ``session_id`` in."""
    return f"{session_id}/{member.name}"


def member_turns(
    node: MessageNode[Any, Any], session_id: str
) -> list[tuple[BaseAgent, str | None]]:
    """The agents of a turn of ``node`` in session ``session_id``, and the session each runs in.

    For a team, its members' (recursively, in their child sessions; None for agents that run
    in their own session); for an agent, itself in ``session_id``.
    """
    from agentpool.agents.base_agent import BaseAgent as AgentBase

    match node:
        case AgentBase():
            return [(node, session_id)]
        case BaseTeam():
            turns: list[tuple[BaseAgent, str | None]] = []
            for member in node.nodes:
                child = member_session_id(session_id, member)
                if isinstance(member, AgentBase) and not member.capabilities.concurrent_sessions:
                    turns.append((member, None))
                else:
                    turns.extend(member_turns(member, child))
            return turns
        case _:
            return []


if __name__ == "__main__":

    async def main() -> None:
        from agentpool import Agent, Team

        agent = Agent("My Agent", model="openai:gpt-5-nano")
        agent_2 = Agent("My Agent", model="openai:gpt-5-nano")
        team = Team([agent, agent_2], mcp_servers=["uvx mcp-server-git"])
        async with team:
            print(await agent.tools.get_tools())

    anyio.run(main)
