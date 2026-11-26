"""End-to-end tests of the OpenCode V2 session API against TestModel agents."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from typing import TYPE_CHECKING, Any

import pytest

from agentpool.agents.base_agent import BaseAgent
from agentpool.agents.events import (
    NoticeEvent,
    PartStartEvent,
    RunStartedEvent,
    StreamCompleteEvent,
    TaskFinishedEvent,
    TaskStartedEvent,
)
from agentpool.agents.usage_limits import UsageLimits, UsageWindow
from agentpool.messaging import ChatMessage
from agentpool_server.opencode_v2_server.event_store import EventStore
from agentpool_server.opencode_v2_server.session import V2Session
from opencode_v2_sdk._version import OPENCODE_VERSION
from opencode_v2_sdk.models import ExperimentalJobUpdated, TuiToastShow

from .conftest import create_session, messages, prompt_and_wait, wait_for


if TYPE_CHECKING:
    from httpx2 import AsyncClient

    from agentpool_server.opencode_v2_server.state import ServerState


async def test_info_and_location(client: AsyncClient) -> None:
    info = (await client.get("/api/info")).json()
    assert info["version"] == OPENCODE_VERSION
    location = (await client.get("/api/location")).json()
    assert location["project"]["id"]


async def test_agents_and_models(client: AsyncClient) -> None:
    agents = (await client.get("/api/agent")).json()["data"]
    assert {a["id"] for a in agents} >= {"helper", "other", "guarded"}
    helper = next(a for a in agents if a["id"] == "helper")
    assert helper["model"] == {"id": "test", "providerID": "test"}
    models = (await client.get("/api/model")).json()["data"]
    assert any(m["providerID"] == "test" and m["id"] == "test" for m in models)
    default = (await client.get("/api/model/default")).json()["data"]
    assert default["id"] == "test"


async def test_prompt_creates_tool_and_text_steps(client: AsyncClient) -> None:
    sid = await create_session(client)
    await prompt_and_wait(client, sid, "hello")
    msgs = await messages(client, sid)
    assert [m["type"] for m in msgs] == ["user", "assistant", "assistant", "idle"]
    assert msgs[0]["text"] == "hello"
    tool = msgs[1]["content"][0]
    assert tool["type"] == "tool"
    assert tool["name"] == "add"
    assert tool["state"]["status"] == "completed"
    assert msgs[2]["content"][0]["type"] == "text"
    assert msgs[2]["finish"] == "stop"
    assert msgs[3]["outcome"] == "succeeded"
    # Each step carries the usage of its own model request.
    assert msgs[1]["finish"] == "tool-calls"
    assert msgs[1]["tokens"]["input"] > 0
    assert msgs[2]["tokens"]["output"] > 0
    session = (await client.get(f"/api/session/{sid}")).json()["data"]
    assert session["tokens"]["input"] > 0
    assert session["outcome"] == "succeeded"


async def test_log_replays_durable_events(client: AsyncClient) -> None:
    sid = await create_session(client)
    await prompt_and_wait(client, sid, "hello")
    response = await client.get(f"/api/experimental/session/{sid}/log")
    frames = [json.loads(f[6:]) for f in response.text.split("\n\n") if f.startswith("data:")]
    types = [f["type"] for f in frames]
    assert types[0] == "session.created"
    assert "session.text.delta" not in types  # ephemeral events are not logged
    assert types[-1] == "session.execution.succeeded"
    seqs = [f["durable"]["seq"] for f in frames]
    assert seqs == sorted(seqs)
    after = seqs[3]
    response = await client.get(
        f"/api/experimental/session/{sid}/log", params={"after": str(after)}
    )
    replay = [json.loads(f[6:]) for f in response.text.split("\n\n") if f.startswith("data:")]
    assert [f["durable"]["seq"] for f in replay] == seqs[4:]


async def test_session_survives_restart(client: AsyncClient, state: ServerState) -> None:
    sid = await create_session(client, title="Persistent")
    await prompt_and_wait(client, sid, "hello")
    before = await messages(client, sid)
    session = state.sessions.pop(sid)
    await session.flush()
    reloaded = await state.get_session(sid)
    assert reloaded is not session
    assert [m.id for m in reloaded.projection.messages] == [m["id"] for m in before]
    assert reloaded.info.title == "Persistent"


async def test_history_replay_without_log(client: AsyncClient, state: ServerState) -> None:
    sid = await create_session(client)
    await prompt_and_wait(client, sid, "hello")
    session = state.sessions.pop(sid)
    await session.flush()
    await state.store.delete(sid)
    rebuilt = await state.get_session(sid)
    types = [m.type for m in rebuilt.projection.messages]
    assert types[0] == "user"
    assert "assistant" in types
    assert types[-1] == "idle"
    tools = [
        part
        for m in rebuilt.projection.messages
        if m.type == "assistant"
        for part in m.content
        if part.type == "tool"
    ]
    assert [t.name for t in tools] == ["add"]
    assert tools[0].state.status == "completed"


async def test_message_pagination(client: AsyncClient) -> None:
    sid = await create_session(client)
    await prompt_and_wait(client, sid, "one")
    first = (await client.get(f"/api/session/{sid}/message", params={"limit": "2"})).json()
    assert [m["type"] for m in first["data"]] == ["idle", "assistant"]  # newest first
    second = (
        await client.get(
            f"/api/session/{sid}/message", params={"limit": "2", "cursor": first["cursor"]["next"]}
        )
    ).json()
    assert [m["type"] for m in second["data"]] == ["assistant", "user"]
    bad = await client.get(
        f"/api/session/{sid}/message", params={"cursor": first["cursor"]["next"], "order": "asc"}
    )
    assert bad.status_code == 400
    assert bad.json()["_tag"] == "InvalidCursorError"


async def test_typed_errors(client: AsyncClient) -> None:
    response = await client.get("/api/session/ses_missing")
    assert response.status_code == 404
    assert response.json()["_tag"] == "SessionNotFoundError"
    sid = await create_session(client)
    response = await client.post(f"/api/session/{sid}/agent", json={"agent": "nope"})
    assert response.json()["_tag"] == "AgentNotFoundError"
    response = await client.post(
        f"/api/session/{sid}/model", json={"model": {"id": "nope", "providerID": "x"}}
    )
    assert response.status_code == 400
    assert response.json()["_tag"] == "UnsupportedModelError"
    response = await client.get("/api/does-not-exist")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/plain")


async def test_switch_agent_records_message(client: AsyncClient) -> None:
    sid = await create_session(client)
    response = await client.post(f"/api/session/{sid}/agent", json={"agent": "other"})
    assert response.status_code == 204
    await prompt_and_wait(client, sid, "hi")
    msgs = await messages(client, sid)
    assert msgs[0]["type"] == "agent-switched"
    assert msgs[0]["agent"] == "other"
    assert msgs[0]["previous"] == "helper"
    assistant = next(m for m in msgs if m["type"] == "assistant")
    assert assistant["agent"] == "other"


async def test_update_archive_and_list(client: AsyncClient) -> None:
    sid = await create_session(client)
    await client.patch(f"/api/session/{sid}", json={"title": "Renamed", "archived": True})
    info = (await client.get(f"/api/session/{sid}")).json()["data"]
    assert info["title"] == "Renamed"
    assert info["time"]["archived"]
    archived = (await client.get("/api/session", params={"archived": "true"})).json()["data"]
    assert [s["id"] for s in archived] == [sid]
    active = (await client.get("/api/session", params={"archived": "false"})).json()["data"]
    assert sid not in [s["id"] for s in active]


async def test_inbox_queue_and_cancel(client: AsyncClient, state: ServerState) -> None:
    sid = await create_session(client)
    body: dict[str, Any] = {"text": "queued", "resume": False, "delivery": "queue"}
    response = await client.post(f"/api/session/{sid}/prompt", json=body)
    inbox_id = response.json()["data"]["id"]
    inbox = (await client.get(f"/api/session/{sid}/inbox")).json()["data"]
    assert [i["id"] for i in inbox] == [inbox_id]
    assert (await client.delete(f"/api/session/{sid}/inbox/{inbox_id}")).status_code == 204
    assert (await client.get(f"/api/session/{sid}/inbox")).json()["data"] == []
    assert await messages(client, sid) == []
    assert not state.is_running(sid)


async def test_permission_flow(client: AsyncClient, state: ServerState) -> None:
    sid = await create_session(client, agent="guarded")
    await client.post(f"/api/session/{sid}/prompt", json={"text": "add"})

    async def asked() -> bool:
        return bool((await client.get(f"/api/session/{sid}/permission")).json()["data"])

    await wait_for(asked)
    request = (await client.get(f"/api/session/{sid}/permission")).json()["data"][0]
    assert request["action"] == "add"
    response = await client.post(
        f"/api/session/{sid}/permission/{request['id']}/reply", json={"decision": "once"}
    )
    assert response.status_code == 204
    await client.post(f"/api/experimental/session/{sid}/wait")
    msgs = await messages(client, sid)
    tool = next(m for m in msgs if m["type"] == "assistant")["content"][0]
    assert tool["state"]["status"] == "completed"


async def test_interrupt_while_waiting_for_permission(client: AsyncClient) -> None:
    sid = await create_session(client, agent="guarded")
    await client.post(f"/api/session/{sid}/prompt", json={"text": "add"})

    async def asked() -> bool:
        return bool((await client.get(f"/api/session/{sid}/permission")).json()["data"])

    await wait_for(asked)
    response = await client.post(f"/api/session/{sid}/interrupt")
    assert response.status_code == 200
    await client.post(f"/api/experimental/session/{sid}/wait")
    msgs = await messages(client, sid)
    assert msgs[-1]["type"] == "idle"
    assert msgs[-1]["outcome"] in {"interrupted", "succeeded"}
    assert (await client.get(f"/api/session/{sid}/permission")).json()["data"] == []


async def test_fork_copies_history(client: AsyncClient) -> None:
    sid = await create_session(client, title="Base")
    await prompt_and_wait(client, sid, "first")
    await prompt_and_wait(client, sid, "second")
    original = await messages(client, sid)
    second_user = [m for m in original if m["type"] == "user"][1]
    response = await client.post(f"/api/session/{sid}/fork", json={"before": second_user["id"]})
    fork = response.json()["data"]
    assert fork["fork"]["sessionID"] == sid
    forked = await messages(client, fork["id"])
    assert [m["type"] for m in forked] == [
        m["type"] for m in original[: original.index(second_user)]
    ]


async def test_revert_commit_truncates(client: AsyncClient) -> None:
    sid = await create_session(client)
    await prompt_and_wait(client, sid, "first")
    await prompt_and_wait(client, sid, "second")
    target = [m for m in await messages(client, sid) if m["type"] == "user"][1]
    staged = await client.post(f"/api/session/{sid}/revert/stage", json={"messageID": target["id"]})
    assert staged.json()["data"]["messageID"] == target["id"]
    info = (await client.get(f"/api/session/{sid}")).json()["data"]
    assert info["revert"]["messageID"] == target["id"]
    assert (await client.post(f"/api/session/{sid}/revert/commit")).status_code == 204
    remaining = await messages(client, sid)
    assert target["id"] not in [m["id"] for m in remaining]
    assert [m["type"] for m in remaining].count("user") == 1
    assert "revert" not in (await client.get(f"/api/session/{sid}")).json()["data"]


async def test_export_import_roundtrip(client: AsyncClient) -> None:
    sid = await create_session(client, title="Exported")
    await prompt_and_wait(client, sid, "hello")
    exported = (await client.get(f"/api/experimental/session/{sid}/export")).json()["data"]
    response = await client.post("/api/experimental/session/import", json=exported)
    assert response.status_code == 200, response.text
    imported = response.json()["data"]
    assert imported["id"] != sid
    assert imported["title"] == "Exported"
    copied = await messages(client, imported["id"])
    assert [m["type"] for m in copied] == [m["type"] for m in exported["messages"]]


async def test_stats(client: AsyncClient) -> None:
    sid = await create_session(client)
    await prompt_and_wait(client, sid, "hello")
    stats = (
        await client.get("/api/experimental/session/stats", params={"tools": "detail"})
    ).json()["data"]
    assert stats["prompts"] == 1
    assert stats["steps"] == 2
    assert stats["tools"]["totals"]["calls"] == 1
    assert stats["tools"]["usage"][0]["name"] == "add"


async def test_slash_command(client: AsyncClient) -> None:
    sid = await create_session(client)
    commands = (await client.get("/api/command")).json()["data"]
    assert any(c["name"] == "help" for c in commands)
    response = await client.post(f"/api/session/{sid}/command", json={"name": "help", "text": ""})
    assert response.status_code == 204
    await client.post(f"/api/experimental/session/{sid}/wait")
    msgs = await messages(client, sid)
    assert msgs[0]["type"] == "user"
    assert msgs[0]["text"] == "/help"
    assert any(m["type"] == "assistant" for m in msgs)


async def test_session_shell(client: AsyncClient, state: ServerState) -> None:
    sid = await create_session(client)
    assert (
        await client.post(f"/api/session/{sid}/shell", json={"command": "echo hi"})
    ).status_code == 204

    async def ended() -> bool:
        msgs = await messages(client, sid)
        return bool(msgs) and msgs[0].get("status") == "exited"

    await wait_for(ended)
    shell = (await messages(client, sid))[0]
    assert shell["type"] == "shell"
    assert shell["output"]["output"].strip() == "hi"


async def test_delete_session(client: AsyncClient, state: ServerState) -> None:
    sid = await create_session(client)
    child = await create_session(client, parentID=sid)
    assert (await client.delete(f"/api/session/{sid}")).status_code == 204
    assert (await client.get(f"/api/session/{sid}")).status_code == 404
    assert (await client.get(f"/api/session/{child}")).status_code == 404
    assert not await state.store.exists(sid)


@pytest.mark.parametrize("password", ["secret"])
async def test_basic_auth(pool: Any, tmp_path: Any, password: str) -> None:
    from httpx2 import ASGITransport, AsyncClient as Client

    from agentpool_server.opencode_v2_server.server import create_app

    app = create_app(pool, working_dir=str(tmp_path), password=password, store=EventStore(tmp_path))
    async with Client(transport=ASGITransport(app=app), base_url="http://test") as http:
        assert (await http.get("/api/info")).status_code == 401
        ok = await http.get("/api/info", auth=("opencode", password))
        assert ok.status_code == 200
        assert (await http.get("/api/info", auth=("opencode", "wrong"))).status_code == 401


def test_session_from_events_requires_created() -> None:
    from agentpool_server.opencode_v2_server.bus import EventBus

    with pytest.raises(ValueError, match=r"session\.created"):
        V2Session.from_events([], bus=EventBus(), store=EventStore("/nonexistent"))


async def test_usage_limits(
    client: AsyncClient, state: ServerState, monkeypatch: pytest.MonkeyPatch
) -> None:
    response = await client.get("/api/experimental/usage/limits")
    assert response.json().get("data") is None  # Native agents have no plan limits

    reset = datetime(2026, 11, 22, 13, tzinfo=UTC)
    limits = UsageLimits(
        windows=[
            UsageWindow(id="session", label="Session", period="session", percent=66),
            UsageWindow(id="weekly", label="Weekly", period="weekly", percent=35, resets_at=reset),
        ],
        plan="pro",
        usage_url="https://claude.ai/settings/usage",
    )
    agent = state.pool.main_agent

    async def get_usage_limits() -> UsageLimits:
        return limits

    monkeypatch.setattr(agent, "get_usage_limits", get_usage_limits)
    data = (await client.get("/api/experimental/usage/limits")).json()["data"]
    assert data["workspace"] == {
        "id": agent.name,
        "name": f"{agent.name} (pro)",
        "usageURL": "https://claude.ai/settings/usage",
    }
    assert data["go"]["rolling"] == {"percent": 66, "limited": False, "resetsAt": 0}
    assert data["go"]["weekly"]["resetsAt"] == round(reset.timestamp() * 1000)
    assert data["go"]["monthly"]["percent"] == 0
    assert data["budgets"] == []


async def test_background_events_reach_the_bound_session(
    client: AsyncClient, state: ServerState, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = await create_session(client)
    await prompt_and_wait(client, sid, "hi")  # Creates the session's runner
    runner = state.runners[sid]
    agent = state.pool.main_agent
    started = TaskStartedEvent(task_id="bg1", kind="agent", description="Scan", background=True)
    runner.tasks.observe(started)
    emitted: list[tuple[type[Any], Any]] = []
    monkeypatch.setattr(runner.session, "emit_sink", lambda cls, data: emitted.append((cls, data)))
    for event in (
        TaskFinishedEvent(task_id="bg1", status="completed"),
        NoticeEvent(title="Scan finished"),
    ):
        background = BaseAgent.BackgroundEvent(agent_name=agent.name, session_id=sid, event=event)
        await agent.background_event.emit(background)
    jobs = (await client.get("/api/experimental/job", params={"sessionID": sid})).json()["data"]
    # Listed (finished) until an execution consumes it
    assert [(job["id"], job["status"]) for job in jobs] == [("bg1", "completed")]
    (job_cls, job_update), (toast_cls, toast) = emitted
    assert job_cls is ExperimentalJobUpdated
    assert (job_update.info.id, job_update.info.status) == ("bg1", "completed")
    assert toast_cls is TuiToastShow
    assert toast.message == "Scan finished"
    # Events of other sessions are not routed here
    other = BaseAgent.BackgroundEvent(
        agent_name=agent.name, session_id="ses_other", event=NoticeEvent(title="elsewhere")
    )
    await agent.background_event.emit(other)
    assert len(emitted) == 2


async def test_turn_the_agent_took_on_its_own_is_an_execution(
    client: AsyncClient, state: ServerState
) -> None:
    sid = await create_session(client)
    await prompt_and_wait(client, sid, "hi")
    agent = state.pool.main_agent
    reply = ChatMessage[str](content="The scan found 3 issues.", role="assistant", name=agent.name)
    for event in (
        RunStartedEvent(session_id=sid, run_id="r1", agent_name=agent.name),
        PartStartEvent.text(index=0, content="The scan found 3 issues."),
        StreamCompleteEvent(message=reply),
    ):
        background = BaseAgent.BackgroundEvent(agent_name=agent.name, session_id=sid, event=event)
        await agent.background_event.emit(background)
    msgs = await messages(client, sid)
    texts = [
        part["text"]
        for m in msgs
        if m["type"] == "assistant"
        for part in m["content"]
        if part["type"] == "text"
    ]
    assert texts[-1] == "The scan found 3 issues."
    assert not state.runners[sid].running
    # A new prompt still works afterwards
    await prompt_and_wait(client, sid, "again")


async def test_cancel_shell_job(client: AsyncClient, state: ServerState) -> None:
    sid = await create_session(client)
    updates: list[Any] = []
    publish = state.shells.publish

    def record(event: Any, kind: str, directory: str) -> None:
        if isinstance(event, ExperimentalJobUpdated):
            updates.append(event.data.info)
        publish(event, kind, directory)

    state.shells.publish = record
    await client.post(f"/api/session/{sid}/shell", json={"command": "sleep 30"})

    async def listed() -> list[dict[str, Any]]:
        response = await client.get("/api/experimental/job", params={"sessionID": sid})
        return response.json()["data"]

    await wait_for(listed)
    (job,) = await listed()
    assert job["status"] == "running"
    response = await client.post(f"/api/experimental/job/{job['id']}/cancel")
    assert response.status_code == 204
    (job,) = await listed()
    assert job["status"] == "cancelled"
    assert [(u.id, u.status) for u in updates] == [
        (job["id"], "running"),
        (job["id"], "cancelled"),
    ]
    # Cancelling a finished or unknown job does nothing
    assert (await client.post(f"/api/experimental/job/{job['id']}/cancel")).status_code == 204
    assert (await client.post("/api/experimental/job/nope/cancel")).status_code == 204


async def test_cancel_agent_task_job(
    client: AsyncClient, state: ServerState, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = await create_session(client)
    await prompt_and_wait(client, sid, "hi")  # Creates the session's runner
    runner = state.runners[sid]
    emitted: list[tuple[type[Any], Any]] = []
    monkeypatch.setattr(runner.session, "emit_sink", lambda cls, data: emitted.append((cls, data)))
    runner.tasks.observe(TaskStartedEvent(task_id="bg1", kind="agent", description="Scan"))
    stopped: list[str] = []

    async def stop_task(task_id: str) -> None:
        stopped.append(task_id)

    monkeypatch.setattr(state.pool.main_agent, "stop_task", stop_task)
    assert (await client.post("/api/experimental/job/bg1/cancel")).status_code == 204
    assert stopped == ["bg1"]
    ((cls, update),) = emitted
    assert cls is ExperimentalJobUpdated
    assert (update.info.id, update.info.status) == ("bg1", "running")


async def test_machines_are_forbidden(client: AsyncClient) -> None:
    for method, path in [
        ("GET", "/api/experimental/machine"),
        ("POST", "/api/experimental/machine"),
        ("DELETE", "/api/experimental/machine/gpu"),
    ]:
        response = await client.request(method, path)
        assert response.status_code == 403
        assert response.json()["_tag"] == "ForbiddenError"


async def test_delete_session_with_a_parent_cycle(client: AsyncClient, state: ServerState) -> None:
    """Corrupted data (a session that is its child's parent) must not recurse forever."""
    sid = await create_session(client)
    child = await create_session(client, parentID=sid)
    state.sessions[sid].info.parent_id = child
    assert (await client.delete(f"/api/session/{sid}")).status_code == 204
    assert (await client.get(f"/api/session/{child}")).status_code == 404
