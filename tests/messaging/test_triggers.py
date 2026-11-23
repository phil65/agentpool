"""Triggers run where their node's router runs them, and connection triggers fire."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Self

from pydantic_ai.models.test import TestModel

from agentpool import Agent, AgentPool
from agentpool.models.manifest import AgentsManifest
from agentpool_config.events import TimeTriggerConfig


if TYPE_CHECKING:
    import pytest

    from agentpool.agents.base_agent import BaseAgent
    from agentpool.messaging import ChatMessage
    from agentpool.messaging.events import ConnectionEventData


def _timer(name: str) -> TimeTriggerConfig:
    return TimeTriggerConfig(name=name, schedule="0 9 * * *", prompt="tick")


def _text(content: Any) -> str:
    match content:
        case [str(text), *_]:
            return text
        case _:
            return str(content)


async def _received(agent: BaseAgent[Any, Any], count: int) -> list[str]:
    for _ in range(200):
        messages = agent.conversation.chat_messages
        prompts = [_text(m.content) for m in messages if m.role == "user"]
        if len(prompts) >= count:
            return prompts
        await asyncio.sleep(0.01)
    raise AssertionError(f"{agent.name} got {agent.conversation.chat_messages}")


async def test_pool_runs_each_trigger_once(one_shot_sources: list[str]) -> None:
    manifest = AgentsManifest.model_validate({
        "agents": {"a": {"type": "native", "model": "test", "triggers": [_timer("tick")]}}
    })
    async with AgentPool(manifest) as pool:
        agent = pool.get_agent("a")
        assert await _received(agent, 1) == ["event from tick"]
    assert one_shot_sources == ["tick"]  # The pool's, not also the agent's own


async def test_agent_outside_a_pool_runs_its_triggers(one_shot_sources: list[str]) -> None:
    manifest = AgentsManifest.model_validate({
        "agents": {"solo": {"type": "native", "model": "test", "triggers": [_timer("tick")]}}
    })
    agent = manifest.agents["solo"].get_agent()
    async with agent:
        assert await _received(agent, 1) == ["event from tick"]
    assert one_shot_sources == ["tick"]


async def test_connection_trigger_runs_its_node() -> None:
    manifest = AgentsManifest.model_validate({
        "agents": {
            "a": {
                "type": "native",
                "model": "test",
                "connections": [{"type": "node", "name": "b"}],
            },
            "b": {"type": "native", "model": "test"},
            "watcher": {
                "type": "native",
                "model": "test",
                "triggers": [
                    {
                        "type": "connection",
                        "name": "on_forward",
                        "source": "a",
                        "event": "message_forwarded",
                    }
                ],
            },
        }
    })
    async with AgentPool(manifest) as pool:
        await pool.get_agent("a").run("hi")
        watcher = pool.get_agent("watcher")
        [prompt] = await _received(watcher, 1)
    assert prompt.startswith("Connection ")
    assert "message_forwarded" in prompt


async def test_talk_callbacks_see_only_their_connection() -> None:
    a, b, c = (Agent(n, model=TestModel()) for n in ("a", "b", "c"))
    to_b = a.connect_to(b)
    to_c = a.connect_to(c)
    seen: list[tuple[str, ChatMessage[Any] | None]] = []

    def record(event: ConnectionEventData[Any]) -> None:
        seen.append((event.connection.name, event.message))

    to_b.on_event("message_forwarded", record)
    async with a, b, c:
        await a.run("hi")
    assert [name for name, _ in seen] == [to_b.name]
    assert to_c.name != to_b.name


async def test_stopping_while_a_triggered_run_runs_ends_the_trigger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A native run swallows the cancel (it returns its partial answer): stop anyway."""
    from evented.base import EventSource
    from evented.event_data import EventData

    running = asyncio.Event()

    class _Tick(EventData):
        def to_prompt(self) -> str:
            return "tick"

    class EndlessSource(EventSource):
        def __init__(self, config: Any) -> None:
            pass

        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def events(self) -> Any:
            while True:
                yield _Tick(source="endless")

    async def slow(messages: Any, info: Any) -> Any:
        running.set()
        await asyncio.sleep(10)
        yield "late"

    from pydantic_ai.models.function import FunctionModel

    monkeypatch.setattr(EventSource, "from_config", classmethod(lambda cls, c: EndlessSource(c)))
    agent = Agent("busy", model=FunctionModel(stream_function=slow))
    agent.router.triggers.add(agent, _timer("tick"))
    async with agent:
        await asyncio.wait_for(running.wait(), timeout=10)
    # Leaving the context stopped the trigger even though the run swallowed the cancel
