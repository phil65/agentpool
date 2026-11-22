"""A V2 session's directory and instructions reach its turns, not other sessions'."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic_ai import ModelRequest, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel

from agentpool import Agent, AgentContext

from .conftest import create_session, prompt_and_wait


if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from httpx import AsyncClient
    from pydantic_ai import ModelMessage

    from agentpool import AgentPool
    from agentpool_server.opencode_v2_server.state import ServerState


async def where(ctx: AgentContext) -> str:
    """Report the working directory."""
    return str(ctx.agent.env.cwd)


def _user_text(request: ModelRequest) -> str:
    return " ".join(str(p.content) for p in request.parts if isinstance(p, UserPromptPart))


async def test_turns_run_in_their_sessions_directory_with_its_instructions(
    client: AsyncClient, pool: AgentPool[Any], state: ServerState, tmp_path: Path
) -> None:
    seen: list[tuple[str, str]] = []  # (instructions, user prompt) of each turn's request
    ran_in: list[str] = []

    async def stream(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | DeltaToolCalls]:
        request = messages[-1]
        assert isinstance(request, ModelRequest)
        if returns := [p for p in request.parts if isinstance(p, ToolReturnPart)]:
            ran_in.extend(str(p.content) for p in returns)
            yield "done"
            return
        seen.append((request.instructions or "", _user_text(request)))
        yield {0: DeltaToolCall(name="where", json_args="{}")}

    agent = pool.all_agents["helper"]
    assert isinstance(agent, Agent)
    agent._model = FunctionModel(stream_function=stream)
    agent.tools.register_tool(where)
    one, two = tmp_path / "one", tmp_path / "two"
    one.mkdir()
    two.mkdir()
    first = await create_session(client, agent="helper", location={"directory": str(one)})
    second = await create_session(client, agent="helper", location={"directory": str(two)})
    url = "/api/experimental/session/{}/instructions/entries/style"
    response = await client.put(url.format(first), json={"value": "terse"})
    assert response.status_code == 204, response.text

    await prompt_and_wait(client, first, "hi")
    await prompt_and_wait(client, second, "hi")
    await prompt_and_wait(client, first, "again")

    assert ran_in == [str(one), str(two), str(one)]
    (first_turn, first_prompt), (second_turn, _), (third_turn, third_prompt) = seen
    assert '- style: "terse"' in first_turn
    assert '- style: "terse"' in third_turn  # With every run, not once
    assert "style" not in second_turn  # Not another session's
    assert "style" not in first_prompt  # Instructions, not prompt text
    assert "style" not in third_prompt
    assert agent.env.cwd not in (str(one), str(two))  # The shared agent keeps its own
    for runner in state.runners.values():
        await runner.environments.close()


def test_instructions_are_the_entries_then_the_latest_activation_of_each_skill() -> None:
    from agentpool_server.opencode_v2_server.projector import SessionProjection
    from agentpool_server.opencode_v2_server.session_environment import session_instructions
    from opencode_v2_sdk.models import SessionMessageSkill, SessionMessageSkillTime

    def skill(message_id: str, skill_id: str, text: str) -> SessionMessageSkill:
        time = SessionMessageSkillTime(created=0)
        return SessionMessageSkill(
            id=message_id, time=time, skill=skill_id, name=skill_id, text=text
        )

    projection = SessionProjection(session_id="ses_1", instructions={"style": '"terse"'})
    projection.messages = [
        skill("msg_1", "a", "old"),
        skill("msg_2", "b", "B"),
        skill("msg_3", "a", "new"),
    ]
    text = session_instructions(projection)
    assert text.index('- style: "terse"') < text.index("<skill name='b'>")
    assert text.index("<skill name='b'>") < text.index("<skill name='a'>\nnew")
    assert "old" not in text
    assert session_instructions(SessionProjection(session_id="ses_2")) == ""


def test_only_agents_without_session_instructions_get_changes_as_prompt_text() -> None:
    from agentpool.agents import AGUIAgent
    from agentpool_server.opencode_v2_server.runner import _takes_session_instructions

    assert _takes_session_instructions(Agent(name="native", model="test"))
    assert not _takes_session_instructions(AGUIAgent(endpoint="http://agui.test", name="agui"))
