"""What a native agent sends to its model as conversation history."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic_ai import ModelRequest, ModelResponse
from pydantic_ai.messages import UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
import pytest

from agentpool import Agent, AgentPool, AgentsManifest
from agentpool_config.storage import SQLStorageConfig, StorageConfig


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from pydantic_ai import ModelMessage


def _prompts(messages: list[ModelMessage]) -> list[Any]:
    """User prompts in the order the model saw them."""
    return [
        part.content
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart)
    ]


class _RecordingModel:
    def __init__(self) -> None:
        self.calls: list[list[ModelMessage]] = []
        self.model = FunctionModel(stream_function=self._stream)

    async def _stream(self, messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        self.calls.append(messages)
        yield "ok"


@pytest.fixture
async def pool() -> AsyncIterator[AgentPool[Any]]:
    storage = StorageConfig(providers=[SQLStorageConfig(url="sqlite+aiosqlite:///:memory:")])
    async with AgentPool(AgentsManifest(storage=storage)) as pool:
        yield pool


def _agent(pool: AgentPool[Any], name: str, model: _RecordingModel) -> Agent[Any, Any]:
    agent = Agent(name=name, model=model.model, agent_pool=pool)
    pool.register(name, agent)
    return agent


async def test_each_prompt_is_sent_once(pool: AgentPool[Any]) -> None:
    model = _RecordingModel()
    agent = _agent(pool, "a", model)
    for prompt in ["first", "second", "third"]:
        async for _ in agent.run_stream(prompt, session_id="s"):
            pass
    assert _prompts(model.calls[-1]) == [["first"], ["second"], ["third"]]
    assert [type(m) for m in model.calls[-1]] == [
        ModelRequest,
        ModelResponse,
        ModelRequest,
        ModelResponse,
        ModelRequest,
    ]


async def test_reloaded_session_continues_with_its_history(pool: AgentPool[Any]) -> None:
    model = _RecordingModel()
    agent = _agent(pool, "a", model)
    for prompt in ["first", "second"]:
        async for _ in agent.run_stream(prompt, session_id="s"):
            pass
    restored = _agent(pool, "b", model)
    assert await restored.load_session("s")
    async for _ in restored.run_stream("after reload", session_id="s"):
        pass
    assert _prompts(model.calls[-1]) == [["first"], ["second"], ["after reload"]]
