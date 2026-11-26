"""MCP event triggers subscribe to an MCP server's events and run their node on each."""

import asyncio
from pathlib import Path
import sys
from typing import TYPE_CHECKING, Any

import jsonschema
from pydantic_ai.models.test import TestModel
import pytest
from upathtools import to_upath

from agentpool import Agent, AgentPool
from agentpool.models.manifest import AgentsManifest
from agentpool.talk.mcp_events import CursorStore, MCPEventSource
from agentpool_config.events import MCPEventTriggerConfig
from agentpool_config.mcp_server import StdioMCPServerConfig


if TYPE_CHECKING:
    from agentpool.agents.base_agent import BaseAgent


SERVER_SCRIPT = Path(__file__).parent.parent / "mcp_server" / "events_server.py"


def _server(*, events: bool = True) -> dict[str, Any]:
    return {
        "type": "stdio",
        "name": "ticks",
        "command": sys.executable,
        "args": [str(SERVER_SCRIPT)],
        "events": events,
    }


def _trigger(**overrides: Any) -> dict[str, Any]:
    return {
        "type": "mcp_event",
        "name": "on_tick",
        "server": "ticks",
        "event": "tick",
        "arguments": {"label": "x"},
        "prompt": "tick {{ event.data.n }} {{ event.data.label }}",
        "min_poll_interval": 0.1,
        **overrides,
    }


def _manifest(**trigger: Any) -> AgentsManifest:
    return AgentsManifest.model_validate({
        "agents": {
            "watcher": {
                "type": "native",
                "model": "test",
                "mcp_servers": [_server()],
                "triggers": [_trigger(**trigger)],
            }
        }
    })


@pytest.fixture(autouse=True)
def cursor_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "cursors.json"
    monkeypatch.setattr("agentpool.talk.mcp_events.default_cursor_file", lambda: to_upath(path))
    return path


def _ticks(agent: BaseAgent[Any, Any]) -> list[int]:
    ticks: list[int] = []
    for message in agent.conversation.chat_messages:
        if message.role != "user":
            continue
        match message.content:
            case [str(text), *_] | str(text) if text.startswith("tick "):
                _, n, label = text.split()
                assert label == "x"
                ticks.append(int(n))
    return ticks


async def _wait_for_ticks(agent: BaseAgent[Any, Any], count: int) -> list[int]:
    for _ in range(300):
        if len(ticks := _ticks(agent)) >= count:
            return ticks
        await asyncio.sleep(0.05)
    raise AssertionError(f"only got ticks {_ticks(agent)}")


async def test_trigger_runs_its_node_per_event() -> None:
    async with AgentPool(_manifest()) as pool:
        ticks = await _wait_for_ticks(pool.get_agent("watcher"), 3)
    assert ticks == list(range(ticks[0], ticks[0] + len(ticks)))  # In order, none lost


async def test_restart_resumes_with_the_missed_events(cursor_file: Path) -> None:
    async with AgentPool(_manifest()) as pool:
        watcher = pool.get_agent("watcher")
        await _wait_for_ticks(watcher, 2)
    handled = _ticks(watcher)[-1]  # The last tick handled before the pool stopped
    assert cursor_file.exists()
    await asyncio.sleep(0.5)  # Ticks pass while nobody listens
    async with AgentPool(_manifest()) as pool:
        second = await _wait_for_ticks(pool.get_agent("watcher"), 6)
    # Resumed after the last handled tick (once more if the stop cut its run short),
    # with the ticks missed meanwhile first, none lost
    assert second[0] in (handled, handled + 1)
    assert second == list(range(second[0], second[0] + len(second)))


async def test_without_resume_a_restart_starts_from_now(cursor_file: Path) -> None:
    async with AgentPool(_manifest(resume=False)) as pool:
        await _wait_for_ticks(pool.get_agent("watcher"), 1)
    assert not cursor_file.exists()


def _source(agent: Agent[None, str], **trigger: Any) -> MCPEventSource:
    config = MCPEventTriggerConfig.model_validate(_trigger(**trigger))
    return MCPEventSource(config, agent, CursorStore())


@pytest.mark.parametrize(
    ("events", "trigger", "error"),
    [
        (False, {}, ValueError),  # The server's events are not enabled
        (True, {"event": "tock"}, ValueError),  # No such event
        (True, {"arguments": {}}, jsonschema.ValidationError),  # Missing "label"
    ],
    ids=["events-off", "unknown-event", "bad-arguments"],
)
async def test_subscription_is_checked_on_start(
    events: bool, trigger: dict[str, Any], error: type[Exception]
) -> None:
    server = StdioMCPServerConfig.model_validate(_server(events=events))
    agent = Agent("watcher", model=TestModel(), mcp_servers=[server])
    async with agent:
        with pytest.raises(error):
            await _source(agent, **trigger).__aenter__()
