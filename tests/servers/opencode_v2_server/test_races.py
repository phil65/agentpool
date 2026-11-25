"""Races and edge cases of the OpenCode V2 runtime (#55, #61)."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import anyio
from pydantic import BaseModel
import pytest

from agentpool.agents.base_agent import BaseAgent
from agentpool.agents.events import PartStartEvent, RunStartedEvent, StreamCompleteEvent
from agentpool.messaging import ChatMessage
from agentpool_server.opencode_v2_server.bus import EventBus
from agentpool_server.opencode_v2_server.event_store import EventStore
from agentpool_server.opencode_v2_server.interactions import PermissionRegistry
from agentpool_server.opencode_v2_server.routes.server import _with_heartbeat

from .conftest import create_session, messages, prompt_and_wait, wait_for


if TYPE_CHECKING:
    from pathlib import Path

    from httpx2 import AsyncClient

    from agentpool_server.opencode_v2_server.state import ServerState


async def test_prompt_during_a_background_turn_runs_after_it(
    client: AsyncClient, state: ServerState
) -> None:
    """#55.1: a prompt sent while the agent runs a turn on its own is not stranded."""
    sid = await create_session(client)
    await prompt_and_wait(client, sid, "hi")
    agent = state.pool.main_agent

    async def emit(event: Any) -> None:
        background = BaseAgent.BackgroundEvent(agent_name=agent.name, session_id=sid, event=event)
        await agent.background_event.emit(background)

    await emit(RunStartedEvent(session_id=sid, run_id="r1", agent_name=agent.name))
    response = await client.post(f"/api/session/{sid}/prompt", json={"text": "queued"})
    assert response.status_code == 200
    reply = ChatMessage[str](content="Background reply", role="assistant", name=agent.name)
    await emit(PartStartEvent.text(index=0, content="Background reply"))
    await emit(StreamCompleteEvent(message=reply))

    async def answered() -> bool:
        types = [(m["type"], m.get("text")) for m in await messages(client, sid)]
        queued = types.index(("user", "queued")) if ("user", "queued") in types else None
        return queued is not None and any(t == "assistant" for t, _ in types[queued:])

    await wait_for(answered)
    assert not state.runners[sid].session.projection.inbox


class _Ping(BaseModel):
    n: int


def _registry(tmp_path: Path) -> PermissionRegistry:
    return PermissionRegistry(publish=lambda *a, **k: None, saved_file=tmp_path / "perm.json")


async def test_reply_removes_api_created_permission(tmp_path: Path) -> None:
    """#55.2a: requests created through the API (no ``ask``) don't stay pending forever."""
    registry = _registry(tmp_path)
    request = registry.create(
        session_id="ses_1", project_id="p", directory="/d", action="edit", resources=["a"]
    )
    await registry.reply("ses_1", request.id, "once", "/d")
    assert request.id not in registry.pending


async def test_concurrent_always_replies_answer_once(tmp_path: Path) -> None:
    """#55.2b: the second of two concurrent replies is refused, not an InvalidStateError."""
    registry = _registry(tmp_path)
    request = registry.create(
        session_id="ses_1", project_id="p", directory="/d", action="edit", resources=["b"]
    )
    results = await asyncio.gather(
        registry.reply("ses_1", request.id, "always", "/d"),
        registry.reply("ses_1", request.id, "always", "/d"),
        return_exceptions=True,
    )
    assert sorted(type(r).__name__ for r in results) == ["InteractionError", "NoneType"]
    assert registry.is_saved("p", "edit", ["b"])
    assert (tmp_path / "perm.json").exists()


async def test_overflowing_sse_subscriber_ends_cleanly() -> None:
    """#61.2a / #55.4: a slow client's stream ends instead of raising into the ASGI server."""
    bus = EventBus(capacity=2)
    stream = _with_heartbeat(bus.subscribe(initial="hello"), interval=5.0)
    assert await anext(stream) == "hello"
    for number in range(5):
        bus.publish(_Ping(n=number), "ping")
    rest = [frame async for frame in stream]  # Ends without SubscriberOverflowError
    assert rest == []
    assert bus.subscriber_count == 0


async def test_shell_output_with_infinite_cursor_is_a_bad_request(
    client: AsyncClient,
) -> None:
    """#61.2c: ``cursor=inf`` is a 400, not a 500."""
    response = await client.post("/api/shell", json={"command": "true"})
    shell_id = response.json()["data"]["id"]
    response = await client.get(f"/api/shell/{shell_id}/output", params={"cursor": "inf"})
    assert response.status_code == 400


async def test_rewriting_an_event_log_is_atomic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#61.2b: a failed rewrite leaves the old log intact (no truncated file)."""
    store = EventStore(tmp_path)
    path = tmp_path / "ses_1.jsonl"
    path.write_text('{"old": 1}\n', encoding="utf-8")

    async def broken_write(self: anyio.Path, data: str, **kwargs: Any) -> int:
        # Truncate, then fail (a crash or a full disk in the middle of the write)
        await anyio.Path.write_bytes(self, b"")
        raise OSError("disk full")

    monkeypatch.setattr(anyio.Path, "write_text", broken_write)
    with pytest.raises(OSError, match="disk full"):
        await store.write("ses_1", [])
    assert path.read_text(encoding="utf-8") == '{"old": 1}\n'
