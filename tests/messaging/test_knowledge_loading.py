"""Configured knowledge / resources reach the model (#51)."""

from typing import TYPE_CHECKING

from pydantic_ai.models.function import AgentInfo, FunctionModel

from agentpool import Agent
from agentpool.messaging.message_history import MessageHistory
from agentpool_config.knowledge import Knowledge


if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from pydantic_ai import ModelMessage


async def test_message_history_loads_its_resources(tmp_path: Path) -> None:
    path = tmp_path / "facts.txt"
    path.write_text("the code word is BANANA")
    async with MessageHistory(resources=[str(path)]) as history:
        assert any("BANANA" in str(p) for p in history.get_pending_parts())


async def test_agent_knowledge_is_in_the_model_context(tmp_path: Path) -> None:
    path = tmp_path / "facts.txt"
    path.write_text("the code word is BANANA")
    seen: list[str] = []

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        seen.append(str(messages))
        yield "ok"

    knowledge = Knowledge(paths=[str(path)])
    agent = Agent(name="a", model=FunctionModel(stream_function=model), knowledge=knowledge)
    async with agent:
        await agent.run("what is the code word?")
    assert "BANANA" in seen[0]
