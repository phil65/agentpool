"""Sequential, ordered group of agents / nodes."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from itertools import pairwise
from time import perf_counter
from typing import TYPE_CHECKING, Any, overload
from uuid import uuid4

import anyio
from pydantic_ai import RunUsage

from agentpool.delegation.base_team import BaseTeam
from agentpool.log import get_logger
from agentpool.messaging import AgentResponse, ChatMessage, TeamResponse, TokenCost
from agentpool.messaging.processing import finalize_message, prepare_prompts
from agentpool.talk.talk import Talk, TeamTalk
from agentpool.utils.time_utils import get_now


if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence
    from datetime import datetime

    from evented_config import EventConfig

    from agentpool import MessageNode
    from agentpool.agents.events import RichAgentStreamEvent
    from agentpool.agents.events.events import SubAgentType
    from agentpool.common_types import PromptCompatible
    from agentpool.delegation import AgentPool
    from agentpool.sessions import Session
    from agentpool_config.mcp_server import MCPServerConfig


logger = get_logger(__name__)


@dataclass(frozen=True, kw_only=True)
class ExtendedTeamTalk(TeamTalk):
    """TeamTalk that also provides TeamRunStats interface."""

    errors: list[tuple[str, str, datetime]] = field(default_factory=list)

    def clear(self) -> None:
        """Reset all tracking data."""
        super().clear()  # Clear base TeamTalk
        self.errors.clear()

    def add_error(self, agent: str, error: str) -> None:
        """Track errors from AgentResponses."""
        self.errors.append((agent, error, get_now()))


class TeamRun[TDeps, TResult](BaseTeam[TDeps, TResult]):
    """Handles team operations with monitoring."""

    @overload  # validator set: it defines the output
    def __init__(
        self,
        agents: Sequence[MessageNode[TDeps, Any]],
        *,
        name: str | None = None,
        description: str | None = None,
        display_name: str | None = None,
        shared_prompt: str | None = None,
        mcp_servers: Sequence[str | MCPServerConfig] | None = None,
        event_configs: Sequence[EventConfig] | None = None,
        validator: MessageNode[Any, TResult],
        agent_pool: AgentPool | None = None,
    ) -> None: ...

    @overload
    def __init__(  # no validator, but all nodes same output type.
        self,
        agents: Sequence[MessageNode[TDeps, TResult]],
        *,
        name: str | None = None,
        description: str | None = None,
        display_name: str | None = None,
        shared_prompt: str | None = None,
        mcp_servers: Sequence[str | MCPServerConfig] | None = None,
        event_configs: Sequence[EventConfig] | None = None,
        validator: None = None,
        agent_pool: AgentPool | None = None,
    ) -> None: ...

    @overload
    def __init__(
        self,
        agents: Sequence[MessageNode[TDeps, Any]],
        *,
        name: str | None = None,
        description: str | None = None,
        display_name: str | None = None,
        shared_prompt: str | None = None,
        mcp_servers: Sequence[str | MCPServerConfig] | None = None,
        event_configs: Sequence[EventConfig] | None = None,
        validator: MessageNode[Any, TResult] | None = None,
        agent_pool: AgentPool | None = None,
    ) -> None: ...

    def __init__(
        self,
        agents: Sequence[MessageNode[TDeps, Any]],
        *,
        name: str | None = None,
        description: str | None = None,
        display_name: str | None = None,
        shared_prompt: str | None = None,
        mcp_servers: Sequence[str | MCPServerConfig] | None = None,
        event_configs: Sequence[EventConfig] | None = None,
        validator: MessageNode[Any, TResult] | None = None,
        agent_pool: AgentPool | None = None,
    ) -> None:
        super().__init__(
            agents,
            name=name,
            description=description,
            display_name=display_name,
            shared_prompt=shared_prompt,
            mcp_servers=mcp_servers,
            event_configs=event_configs,
            agent_pool=agent_pool,
        )
        self.validator = validator
        self.result_mode = "last"

    def __prompt__(self) -> str:
        """Format team info for prompts."""
        members = " -> ".join(a.name for a in self.nodes)
        desc = f" - {self.description}" if self.description else ""
        return f"Sequential Team {self.name!r}{desc}\nPipeline: {members}"

    async def run(
        self,
        *prompts: PromptCompatible | None,
        store_history: bool = False,
        **kwargs: Any,
    ) -> ChatMessage[TResult]:
        """Run agents sequentially and return combined message."""
        # Prepare prompts and create user message
        user_msg, processed_prompts = await prepare_prompts(*prompts)
        await self.message_received.emit(user_msg)
        # Execute sequential logic
        message_id = str(uuid4())  # Always generate unique response ID
        result = await self.execute(*processed_prompts, **kwargs)
        all_messages = [r.message for r in result if r.message]
        assert all_messages, "Error during execution, returned None for TeamRun"
        # Determine content based on mode
        match self.result_mode:
            case "last":
                content = all_messages[-1].content
            # case "concat":
            #     content = "\n".join(msg.format() for msg in all_messages)
            case _:
                raise ValueError(f"Invalid result mode: {self.result_mode}")
        run_usage = RunUsage()
        cost = TokenCost(total_cost=Decimal(0))
        for chat_message in all_messages:
            run_usage.incr(chat_message.usage)
            if chat_message.cost_info:
                cost.incr(chat_message.cost_info)
        message = ChatMessage(
            content=content,
            messages=[m for chat_message in all_messages for m in chat_message.messages],
            role="assistant",
            name=self.name,
            usage=run_usage,
            cost_info=cost,
            associated_messages=all_messages,
            message_id=message_id,
            session_id=user_msg.session_id,
            parent_id=user_msg.message_id,
            metadata={
                "execution_order": [r.agent_name for r in result],
                "start_time": result.start_time.isoformat(),
                "errors": {name: str(error) for name, error in result.errors.items()},
            },
        )

        if store_history:
            pass  # Teams could implement their own history management here if needed
        return await finalize_message(message, self)

    async def execute(
        self,
        *prompts: PromptCompatible | None,
        **kwargs: Any,
    ) -> TeamResponse[TResult]:
        """Start execution with optional monitoring."""
        self._team_talk.clear()
        start_time = get_now()
        prompts_ = list(prompts)
        if self.shared_prompt:
            prompts_.insert(0, self.shared_prompt)
        responses = [i async for i in self.execute_iter(*prompts_) if isinstance(i, AgentResponse)]
        return TeamResponse(responses, start_time)

    async def run_iter(
        self,
        *prompts: PromptCompatible,
        **kwargs: Any,
    ) -> AsyncIterator[ChatMessage[Any]]:
        """Yield messages from the execution chain."""
        async for item in self.execute_iter(*prompts, **kwargs):
            match item:
                case AgentResponse(message=message) if message:
                    yield message
                case Talk():
                    pass

    async def execute_iter(
        self,
        *prompt: PromptCompatible,
        **kwargs: Any,
    ) -> AsyncIterator[Talk[Any] | AgentResponse[Any]]:
        connections: list[Talk[Any]] = []
        try:
            all_nodes = list(self.nodes)
            if self.validator:
                all_nodes.append(self.validator)
            first = all_nodes[0]
            connections = [s.connect_to(t, queued=True) for s, t in pairwise(all_nodes)]
            for conn in connections:
                self._team_talk.append(conn)

            # First agent
            start = perf_counter()
            message = await first.run(*prompt, **kwargs)
            timing = perf_counter() - start
            response = AgentResponse[Any](first.name, message=message, timing=timing)
            yield response

            # Process through chain
            for connection in connections:
                target = connection.targets[0]
                yield connection
                # Let errors propagate - they break the chain
                start = perf_counter()
                messages = await connection.trigger()

                if target == all_nodes[-1]:
                    last_talk = Talk[Any](target, [], connection_type="run")
                    if response.message:
                        last_talk.stats.messages.append(response.message)
                    self._team_talk.append(last_talk)

                timing = perf_counter() - start
                yield AgentResponse[Any](target.name, message=messages[0], timing=timing)

        finally:  # Always clean up connections
            for connection in connections:
                connection.disconnect()

    async def run_stream(
        self,
        *prompts: PromptCompatible,
        session: Session | None = None,
        require_all: bool = True,
        **kwargs: Any,
    ) -> AsyncIterator[RichAgentStreamEvent[Any]]:
        """Stream responses through the chain of team members.

        Args:
            prompts: Input prompts to process through the chain
            session: The team session of the turn: members run in child sessions of it
            require_all: If True, fail if any agent fails. If False,
                         continue with remaining agents.
            kwargs: Additional arguments passed to each agent

        Yields:
            RichAgentStreamEvent, with member events wrapped in SubAgentEvent
        """
        from agentpool.agents.events import StreamCompleteEvent, SubAgentEvent
        from agentpool.delegation.team import Team

        current_message = prompts
        for node in self.nodes:
            match node:
                case Team():
                    source_type: SubAgentType = "team_parallel"
                case BaseTeam():
                    source_type = "team_sequential"
                case _:
                    source_type = "agent"

            try:
                async for event in self.member_stream(node, current_message, session, **kwargs):
                    # Handle already-wrapped SubAgentEvents (nested teams)
                    if isinstance(event, SubAgentEvent):
                        yield SubAgentEvent(
                            source_name=event.source_name,
                            source_type=event.source_type,
                            event=event.event,
                            depth=event.depth + 1,
                        )
                    else:
                        yield SubAgentEvent(
                            source_name=node.name,
                            source_type=source_type,
                            event=event,
                        )

                        # Extract content for next agent in chain
                        if isinstance(event, StreamCompleteEvent):
                            current_message = (event.message.content,)

            except Exception as e:
                if require_all:
                    msg = f"Chain broken at {node.name}: {e}"
                    logger.exception(msg)
                    raise ValueError(msg) from e
                logger.warning("Chain handler failed", name=node.name, error=e)


if __name__ == "__main__":

    async def main() -> None:
        from agentpool import Agent, Team
        from agentpool.agents.events import SubAgentEvent

        agent1 = Agent(name="Agent1", model="test")
        agent2 = Agent(name="Agent2", model="test")
        agent3 = Agent(name="Agent3", model="test")
        inner_team = Team([agent1, agent2], name="Parallel")
        outer_run = TeamRun([inner_team, agent3], name="Sequential")
        print("Testing TeamRun containing Team...")
        try:
            async for event in outer_run.run_stream("test"):
                if isinstance(event, SubAgentEvent):
                    print(
                        f"[depth={event.depth}] {event.source_name}: {type(event.event).__name__}"
                    )
                else:
                    print(f"Event: {type(event).__name__}")
        except Exception as e:  # noqa: BLE001
            print(f"Error: {e}")

    anyio.run(main)
