"""Parallel, unordered group of agents / nodes."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from time import perf_counter
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from anyenv.async_run import as_generated
import anyio
from pydantic_ai import RunUsage

from agentpool.agents.base_agent import BaseAgent
from agentpool.agents.events import SubAgentEvent
from agentpool.delegation.base_team import BaseTeam
from agentpool.delegation.teamrun import TeamRun
from agentpool.log import get_logger
from agentpool.messaging import AgentResponse, ChatMessage, TeamResponse, TokenCost
from agentpool.messaging.processing import finalize_message, prepare_prompts
from agentpool.utils.time_utils import get_now


logger = get_logger(__name__)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from toprompt import AnyPromptType

    from agentpool import MessageNode
    from agentpool.agents.events import RichAgentStreamEvent
    from agentpool.agents.events.events import SubAgentType
    from agentpool.common_types import PromptCompatible
    from agentpool.sessions import Session
    from agentpool.talk import Talk


class Team[TDeps = None](BaseTeam[TDeps, Any]):
    """Group of agents that can execute together."""

    async def execute(self, *prompts: PromptCompatible | None, **kwargs: Any) -> TeamResponse:
        """Run all agents in parallel with monitoring."""
        from agentpool.talk.talk import Talk

        self._team_talk.clear()
        start_time = get_now()
        responses: list[AgentResponse[Any]] = []
        errors: dict[str, Exception] = {}
        final_prompt = list(prompts)
        if self.shared_prompt:
            final_prompt.insert(0, self.shared_prompt)
        all_nodes = list(self.nodes)
        # Create Talk connections for monitoring this execution
        execution_talks: list[Talk[Any]] = []
        for node in all_nodes:
            # No actual forwarding, just for tracking
            talk = Talk[Any](node, [], connection_type="run", queued=True, queue_strategy="latest")
            execution_talks.append(talk)
            self._team_talk.append(talk)  # Add to base class's TeamTalk

        async def _run(node: MessageNode[TDeps, Any]) -> None:
            try:
                start = perf_counter()
                message = await node.run(*final_prompt, **kwargs)
                timing = perf_counter() - start
                r = AgentResponse(agent_name=node.name, message=message, timing=timing)
                responses.append(r)
                # Update talk stats for this agent
                talk = next(t for t in execution_talks if t.source == node)
                talk._stats.messages.append(message)
            except Exception as e:  # noqa: BLE001
                errors[node.name] = e

        # Run all agents in parallel
        await asyncio.gather(*[_run(node) for node in all_nodes])
        return TeamResponse(responses=responses, start_time=start_time, errors=errors)

    def __prompt__(self) -> str:
        """Format team info for prompts."""
        members = ", ".join(a.name for a in self.nodes)
        desc = f" - {self.description}" if self.description else ""
        return f"Parallel Team {self.name!r}{desc}\nMembers: {members}"

    async def run_iter(
        self,
        *prompts: AnyPromptType,
        **kwargs: Any,
    ) -> AsyncIterator[ChatMessage[Any]]:
        """Yield messages as they arrive from parallel execution."""
        queue: asyncio.Queue[ChatMessage[Any] | None] = asyncio.Queue()
        failures: dict[str, Exception] = {}

        async def _run(node: MessageNode[TDeps, Any]) -> None:
            try:
                message = await node.run(*prompts, **kwargs)
                await queue.put(message)
            except Exception as e:
                logger.exception("Error executing node", name=node.name)
                failures[node.name] = e
                # Put None to maintain queue count
                await queue.put(None)

        # Get nodes to run
        all_nodes = list(self.nodes)
        # Start all agents
        tasks = [asyncio.create_task(_run(n), name=f"run_{n.name}") for n in all_nodes]
        try:
            # Yield messages as they arrive
            for _ in all_nodes:
                if msg := await queue.get():
                    yield msg

            # If any failures occurred, raise error with details
            if failures:
                error_details = "\n".join(f"- {name}: {error}" for name, error in failures.items())
                raise RuntimeError(f"Some nodes failed to execute:\n{error_details}")

        finally:
            # Clean up any remaining tasks
            for task in tasks:
                if not task.done():
                    task.cancel()

    async def run(
        self,
        *prompts: PromptCompatible | None,
        store_history: bool = False,
        **kwargs: Any,
    ) -> ChatMessage[list[Any]]:
        """Run all agents in parallel and return combined message."""
        # Prepare prompts and create user message
        user_msg, processed_prompts = await prepare_prompts(*prompts)
        await self.message_received.emit(user_msg)
        # Execute team logic
        result = await self.execute(*processed_prompts, **kwargs)
        message_id = str(uuid4())  # Always generate unique response ID
        run_usage = RunUsage()
        cost = TokenCost(total_cost=Decimal(0))
        for msg in result:
            if msg.message:
                run_usage.incr(msg.message.usage)
                if msg.message.cost_info:
                    cost.incr(msg.message.cost_info)
        message = ChatMessage(
            content=[r.message.content for r in result if r.message],
            messages=[m for r in result if r.message for m in r.message.messages],
            role="assistant",
            name=self.name,
            message_id=message_id,
            usage=run_usage,
            cost_info=cost,
            session_id=user_msg.session_id,
            parent_id=user_msg.message_id,
            metadata={
                "agent_names": [r.agent_name for r in result],
                "errors": {name: str(error) for name, error in result.errors.items()},
                "start_time": result.start_time.isoformat(),
            },
        )

        # Teams typically don't store history by default, but allow it
        if store_history:
            # Teams could implement their own history management here if needed
            pass
        # Finalize and route message
        return await finalize_message(message, self)

    async def run_stream(
        self,
        *prompts: PromptCompatible,
        session: Session | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[RichAgentStreamEvent[Any]]:
        """Stream responses from all team members in parallel.

        Args:
            prompts: Input prompts to process in parallel
            session: The team session of the turn: members run in child sessions of it
            kwargs: Additional arguments passed to each agent

        Yields:
            RichAgentStreamEvent, with member events wrapped in SubAgentEvent
        """
        # Get nodes to run
        all_nodes = list(self.nodes)

        # Create list of streams
        async def wrap_stream(
            node: MessageNode[Any, Any],
        ) -> AsyncIterator[RichAgentStreamEvent[Any]]:
            """Wrap a node's stream events in SubAgentEvent."""
            match node:
                case Team():
                    source_type: SubAgentType = "team_parallel"
                case TeamRun():
                    source_type = "team_sequential"
                case BaseAgent():
                    source_type = "agent"
                case _:
                    raise ValueError(f"Unexpected node type: {type(node)}")

            async for event in self.member_stream(node, prompts, session, **kwargs):
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
                        depth=1,
                    )

        streams = [wrap_stream(node) for node in all_nodes]
        # Merge all streams
        async for event in as_generated(streams):
            yield event


if __name__ == "__main__":

    async def main() -> None:
        from agentpool import Agent, TeamRun
        from agentpool.agents.events import SubAgentEvent

        agent_a = Agent(name="A", model="test")
        agent_b = Agent(name="B", model="test")
        agent_c = Agent(name="C", model="test")
        # Test Team containing TeamRun (parallel containing sequential)
        inner_run = TeamRun([agent_a, agent_b], name="Sequential")
        outer_team = Team([inner_run, agent_c], name="Parallel")

        print("Testing Team containing TeamRun...")
        async for event in outer_team.run_stream("test"):
            if isinstance(event, SubAgentEvent):
                print(f"[depth={event.depth}] {event.source_name}: {type(event.event).__name__}")
            else:
                print(f"Event: {type(event).__name__}")

    anyio.run(main)
