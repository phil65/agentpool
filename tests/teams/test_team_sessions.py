"""A team turn of a session runs its members in child sessions of it."""

from typing import TYPE_CHECKING

from pydantic_ai import ModelRequest, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from agentpool import Agent, AgentPool, AgentsManifest
from agentpool.agents.events import StreamCompleteEvent, SubAgentEvent
from agentpool.delegation.base_team import member_session_id, member_turns
from agentpool.sessions import Session


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from pydantic_ai import ModelMessage


def _echo_history() -> FunctionModel:
    """Answers with the user prompts it has seen in the conversation so far."""

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        prompts = [
            part.content if isinstance(part.content, str) else " ".join(map(str, part.content))
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, UserPromptPart)
        ]
        yield " | ".join(prompts)

    return FunctionModel(stream_function=stream)


async def _answers(stream: AsyncIterator[object]) -> dict[str, str]:
    answers: dict[str, str] = {}
    async for event in stream:
        if isinstance(event, SubAgentEvent) and isinstance(event.event, StreamCompleteEvent):
            answers[event.source_name] = str(event.event.message.content)
    return answers


async def test_team_sessions_keep_their_members_conversations_apart() -> None:
    async with AgentPool(AgentsManifest()) as pool:
        for name in ("a", "b"):
            await pool.add_agent(Agent(name=name, model=_echo_history()))
        team = pool.create_team([pool.get_agent("a"), pool.get_agent("b")], name="duo")
        first, second = Session.create("duo"), Session.create("duo")
        await _answers(team.run_stream("one", session=first))
        await _answers(team.run_stream("two", session=second))
        answers = await _answers(team.run_stream("again", session=first))
        assert answers == {"a": "one | again", "b": "one | again"}  # Not "two"
        child = pool.sessions.get(member_session_id(first.session_id, pool.get_agent("a")))
        assert child is not None
        assert child.data.parent_id == first.session_id
        assert not pool.get_agent("a").conversation.get_history()  # Its own session untouched


async def test_sequential_teams_use_child_sessions_too() -> None:
    async with AgentPool(AgentsManifest()) as pool:
        for name in ("a", "b"):
            await pool.add_agent(Agent(name=name, model=_echo_history()))
        chain = pool.create_team_run([pool.get_agent("a"), pool.get_agent("b")], name="chain")
        session = Session.create("chain")
        answers = await _answers(chain.run_stream("go", session=session))
        assert set(answers) == {"a", "b"}
        for name in ("a", "b"):
            member = pool.get_agent(name)
            assert pool.sessions.get(member_session_id(session.session_id, member)) is not None


async def test_agents_with_one_native_session_run_in_their_own() -> None:
    from agentpool.agents import AGUIAgent

    async with AgentPool(AgentsManifest()) as pool:
        await pool.add_agent(Agent(name="a", model=_echo_history()))
        await pool.add_agent(AGUIAgent(endpoint="http://agui.test", name="remote"))
        team = pool.create_team([pool.get_agent("a"), pool.get_agent("remote")], name="mixed")
        session = Session.create("mixed")
        assert await team.member_session(pool.get_agent("remote"), session) is None
        assert member_turns(team, session.session_id) == [
            (pool.get_agent("a"), member_session_id(session.session_id, pool.get_agent("a"))),
            (pool.get_agent("remote"), None),
        ]
