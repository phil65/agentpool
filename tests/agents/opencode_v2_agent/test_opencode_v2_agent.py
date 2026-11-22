"""End-to-end tests for OpenCodeV2Agent against mock V2 servers and AgentPool's V2 server."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

import httpx

from agentpool import Agent, AgentPool
from agentpool.agents.events import (
    RunStartedEvent,
    StreamCompleteEvent,
    ToolCallCompleteEvent,
    ToolCallStartEvent,
)
from agentpool.agents.opencode_v2_agent import OpenCodeV2Agent
from agentpool.resource_providers import StaticResourceProvider
from agentpool.tools.base import FunctionTool
from agentpool.ui.mock_provider import MockInputProvider
from agentpool_server.opencode_v2_server.server import create_app
from opencode_v2_sdk.client import OpenCodeV2Client


if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    import pytest


def _sse(payload: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(payload, separators=(',', ':'))}\n\n".encode()


class _FakeV2Server:
    """In-memory OpenCode V2 HTTP/SSE mock server for deterministic agent tests."""

    def __init__(self) -> None:
        self.event_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self.calls: list[tuple[str, str, Any]] = []
        self.permission_replies: list[tuple[str, dict[str, Any]]] = []
        self.form_replies: list[tuple[str, dict[str, Any]]] = []
        self.mcp_servers: dict[str, dict[str, Any]] = {}
        self.interrupted = False
        self.session_counter = 0

    def push_event(self, payload: dict[str, Any]) -> None:
        self.event_queue.put_nowait(_sse(payload))

    def _event_stream_response(self) -> httpx.Response:
        queue = self.event_queue

        class _Stream(httpx.AsyncByteStream):
            async def __aiter__(self) -> AsyncIterator[bytes]:
                yield _sse({"id": "evt_0", "type": "server.connected", "data": {}})
                while (chunk := await queue.get()) is not None:
                    yield chunk

        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=_Stream(),
        )

    def _handle_mcp(self, method: str, rest: str, body: Any) -> httpx.Response | None:
        if method == "PUT":
            self.mcp_servers[rest] = body
            return httpx.Response(204)
        if method == "POST" and rest.endswith("/connect"):
            return httpx.Response(204)
        if method == "DELETE":
            self.mcp_servers.pop(rest, None)
            return httpx.Response(204)
        return None

    def _handle_static(self, path: str) -> httpx.Response | None:
        static_payloads: dict[str, dict[str, Any]] = {
            "/api/info": {
                "version": "2.3.2",
                "pid": 123,
                "urls": ["http://v2.test"],
                "paths": {"tmp": "/tmp"},
            },
            "/api/agent": {
                "location": {"directory": "/work"},
                "data": [
                    {
                        "id": "build",
                        "name": "Build",
                        "request": {"settings": {}, "headers": {}, "body": {}},
                        "mode": "primary",
                        "hidden": False,
                        "permissions": [],
                    },
                    {
                        "id": "plan",
                        "name": "Plan",
                        "request": {"settings": {}, "headers": {}, "body": {}},
                        "mode": "primary",
                        "hidden": False,
                        "permissions": [],
                    },
                ],
            },
            "/api/model": {
                "location": {"directory": "/work"},
                "data": [
                    {
                        "id": "claude-sonnet-4-5",
                        "modelID": "claude-sonnet-4-5",
                        "providerID": "anthropic",
                        "name": "Claude Sonnet 4.5",
                        "capabilities": {
                            "tools": True,
                            "input": ["text", "image"],
                            "output": ["text"],
                        },
                        "variants": [{"id": "medium"}, {"id": "high"}],
                        "time": {"released": 0},
                        "cost": [],
                        "status": "active",
                        "enabled": True,
                        "limit": {"context": 200000, "output": 16000},
                    }
                ],
            },
            "/api/credential": {
                "data": [
                    {
                        "id": "cred_1",
                        "integrationID": "anthropic",
                        "label": "Default",
                        "active": True,
                        "value": {"type": "key", "key": "***"},
                    }
                ]
            },
            "/api/mcp": {
                "location": {"directory": "/work"},
                "data": [
                    {"name": name, "status": {"status": "connected"}} for name in self.mcp_servers
                ],
            },
        }
        payload = static_payloads.get(path)
        return httpx.Response(200, json=payload) if payload is not None else None

    def _handle_session(self, method: str, path: str, body: Any) -> httpx.Response | None:
        empty_tokens = {
            "input": 0,
            "output": 0,
            "reasoning": 0,
            "cache": {"read": 0, "write": 0},
        }
        if path == "/api/session" and method == "POST":
            self.session_counter += 1
            sid = f"ses_{self.session_counter}"
            data = {
                "id": sid,
                "projectID": "proj_1",
                "agent": body.get("agent") or "build",
                "model": body.get("model"),
                "cost": 0,
                "tokens": empty_tokens,
                "time": {"created": 1000, "updated": 1000},
                "location": {"directory": "/work"},
            }
            return httpx.Response(200, json={"data": data})
        if path == "/api/session" and method == "GET":
            item = {
                "id": "ses_1",
                "projectID": "proj_1",
                "title": "Existing session",
                "cost": 0,
                "tokens": empty_tokens,
                "time": {"created": 1000, "updated": 2000},
                "location": {"directory": "/work"},
            }
            return httpx.Response(200, json={"data": [item], "cursor": {}})
        if path == "/api/session/ses_1" and method == "GET":
            item = {
                "id": "ses_1",
                "projectID": "proj_1",
                "title": "Existing session",
                "agent": "plan",
                "model": {
                    "id": "claude-sonnet-4-5",
                    "providerID": "anthropic",
                    "variant": "high",
                },
                "cost": 0,
                "tokens": empty_tokens,
                "time": {"created": 1000, "updated": 2000},
                "location": {"directory": "/work"},
            }
            return httpx.Response(200, json={"data": item})
        if path == "/api/session/ses_1/message" and method == "GET":
            msgs = [
                {
                    "id": "msg_u1",
                    "type": "user",
                    "time": {"created": 1000},
                    "text": "Earlier question",
                },
                {
                    "id": "msg_a1",
                    "type": "assistant",
                    "time": {"created": 1500},
                    "agent": "plan",
                    "model": {"id": "claude-sonnet-4-5", "providerID": "anthropic"},
                    "content": [{"type": "text", "text": "Earlier answer"}],
                    "finish": "stop",
                },
            ]
            return httpx.Response(200, json={"data": msgs, "cursor": {}})
        return self._handle_session_action(method, path, body)

    def _handle_session_action(self, method: str, path: str, body: Any) -> httpx.Response | None:
        if path.endswith("/prompt") and method == "POST":
            sid = path.split("/")[3]
            payload = {
                "id": "msg_in_1",
                "sessionID": sid,
                "time": {"created": 1000},
                "payload": {"text": body["text"]},
                "delivery": body.get("delivery", "steer"),
            }
            return httpx.Response(200, json={"data": payload})
        if path.endswith("/interrupt") and method == "POST":
            self.interrupted = True
            return httpx.Response(200, json={"interrupted": True})
        if "/permission/" in path and path.endswith("/reply"):
            self.permission_replies.append((path.split("/")[5], body))
            return httpx.Response(204)
        if "/form/" in path and path.endswith("/reply"):
            self.form_replies.append((path.split("/")[5], body))
            return httpx.Response(204)
        if path.endswith(("/agent", "/model")) or method == "PATCH":
            return httpx.Response(204)
        return None

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, path, body))
        if path == "/api/event":
            return self._event_stream_response()
        if path.startswith("/api/experimental/mcp/"):
            rest = path.removeprefix("/api/experimental/mcp/")
            if (resp := self._handle_mcp(request.method, rest, body)) is not None:
                return resp
        if (resp := self._handle_static(path)) is not None:
            return resp
        if (resp := self._handle_session(request.method, path, body)) is not None:
            return resp
        return httpx.Response(404, json={"error": f"Unhandled {request.method} {path}"})


async def _drive_sample_turn(server: _FakeV2Server) -> None:
    while not any(p.endswith("/prompt") for _, p, _ in server.calls):
        await asyncio.sleep(0.01)
    server.push_event({
        "id": "evt_1",
        "created": 1000,
        "durable": {"aggregateID": "ses_1", "seq": 1},
        "type": "session.execution.started",
        "data": {"sessionID": "ses_1"},
    })
    server.push_event({
        "id": "evt_2",
        "created": 1001,
        "location": {"directory": "/work"},
        "type": "permission.asked",
        "data": {
            "id": "perm_1",
            "sessionID": "ses_1",
            "action": "bash",
            "resources": ["echo hi"],
            "message": "Run echo hi?",
        },
    })
    server.push_event({
        "id": "evt_3",
        "created": 1002,
        "location": {"directory": "/work"},
        "type": "form.created",
        "data": {
            "form": {
                "id": "frm_1",
                "sessionID": "ses_1",
                "title": "Confirm?",
                "fields": [{"key": "choice", "type": "string", "title": "Choice"}],
            }
        },
    })
    server.push_event({
        "id": "evt_4",
        "created": 1003,
        "durable": {"aggregateID": "ses_1", "seq": 2},
        "type": "session.step.started",
        "data": {
            "sessionID": "ses_1",
            "assistantMessageID": "msg_1",
            "agent": "build",
            "model": {"id": "claude-sonnet-4-5", "providerID": "anthropic"},
            "started": 1000,
        },
    })
    server.push_event({
        "id": "evt_5",
        "created": 1004,
        "durable": {"aggregateID": "ses_1", "seq": 3},
        "type": "session.tool.input.started",
        "data": {
            "sessionID": "ses_1",
            "assistantMessageID": "msg_1",
            "id": "call_1",
            "name": "bash",
        },
    })
    server.push_event({
        "id": "evt_6",
        "created": 1005,
        "durable": {"aggregateID": "ses_1", "seq": 4},
        "type": "session.tool.called",
        "data": {
            "sessionID": "ses_1",
            "assistantMessageID": "msg_1",
            "id": "call_1",
            "input": {"command": "echo hi"},
            "executed": False,
        },
    })
    server.push_event({
        "id": "evt_7",
        "created": 1006,
        "durable": {"aggregateID": "ses_1", "seq": 5},
        "type": "session.tool.success",
        "data": {
            "sessionID": "ses_1",
            "assistantMessageID": "msg_1",
            "id": "call_1",
            "content": [{"type": "text", "text": "hi"}],
            "executed": True,
        },
    })
    server.push_event({
        "id": "evt_8",
        "created": 1007,
        "type": "session.text.delta",
        "data": {
            "sessionID": "ses_1",
            "assistantMessageID": "msg_1",
            "ordinal": 0,
            "delta": "Done!",
        },
    })
    server.push_event({
        "id": "evt_9",
        "created": 1008,
        "durable": {"aggregateID": "ses_1", "seq": 6},
        "type": "session.step.ended",
        "data": {
            "sessionID": "ses_1",
            "assistantMessageID": "msg_1",
            "finish": "stop",
            "cost": 0.01,
            "tokens": {
                "input": 30,
                "output": 10,
                "reasoning": 0,
                "cache": {"read": 0, "write": 0},
            },
        },
    })
    while not server.permission_replies or not server.form_replies:
        await asyncio.sleep(0.01)
    server.push_event({
        "id": "evt_10",
        "created": 1009,
        "durable": {"aggregateID": "ses_1", "seq": 7},
        "type": "session.execution.succeeded",
        "data": {"sessionID": "ses_1"},
    })


async def test_mock_turn_with_permissions_forms_tools_and_options() -> None:
    server = _FakeV2Server()
    http = httpx.AsyncClient(
        base_url="http://v2.test", transport=httpx.MockTransport(server.handle)
    )
    client = OpenCodeV2Client("http://v2.test", http_client=http)
    provider = MockInputProvider(
        tool_confirmation="allow",
        elicitation_response={"choice": "Yes"},
    )

    def greet(who: str) -> str:
        """Greet a user."""
        return f"hi {who}"

    agent = OpenCodeV2Agent(
        name="oc-test",
        client=client,
        model="anthropic/claude-sonnet-4-5",
        permission_mode="ask",
        input_provider=provider,
        toolsets=[StaticResourceProvider(tools=[FunctionTool.from_callable(greet)])],
        enable_logging=False,
    )

    async with http, agent:
        # MCP bridge was registered on enter
        assert len(server.mcp_servers) == 1
        mcp_info = await agent.get_mcp_server_info()
        assert len(mcp_info) == 1

        driver = asyncio.create_task(_drive_sample_turn(server))
        events = [e async for e in agent.run_stream("Say hi")]
        await driver

        assert isinstance(events[0], RunStartedEvent)
        assert isinstance(events[-1], StreamCompleteEvent)
        assert any(isinstance(e, ToolCallStartEvent) for e in events)
        assert any(isinstance(e, ToolCallCompleteEvent) for e in events)
        assert events[-1].message.content == "Done!"
        assert events[-1].message.usage.input_tokens == 30
        assert server.permission_replies == [("perm_1", {"decision": "once"})]
        assert server.form_replies == [("frm_1", {"answer": {"choice": "Yes"}})]

        # Auth, models, modes, interrupt, sessions
        auth = await agent.get_auth_status()
        assert auth.state == "authenticated"
        models = await agent.get_available_models()
        assert models is not None
        assert models[0].id == "anthropic/claude-sonnet-4-5"

        modes = await agent.get_modes()
        assert [m.id for m in modes] == ["mode", "agent", "thought_level", "model"]
        await agent.set_mode("plan", "agent")
        await agent.set_mode("high", "thought_level")
        await agent.interrupt()
        assert server.interrupted is True

        sessions = await agent.list_sessions()
        assert [s.session_id for s in sessions] == ["ses_1"]
        loaded = await agent.load_session("ses_1")
        assert loaded is not None
        assert [m.content for m in agent.conversation.chat_messages] == [
            "Earlier question",
            "Earlier answer",
        ]

    # Bridge server removed on exit
    assert server.mcp_servers == {}


async def test_roundtrip_against_agentpool_opencode_v2_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Drive AgentPool's real OpenCode V2 FastAPI server over HTTP/SSE via OpenCodeV2Agent."""
    import socket

    from pydantic_ai.models.test import TestModel
    import uvicorn

    from agentpool_server.opencode_v2_server.event_store import EventStore

    monkeypatch.delenv("OPENCODE_PASSWORD", raising=False)
    monkeypatch.delenv("OPENCODE_SERVER_PASSWORD", raising=False)

    pool: AgentPool[None] = AgentPool()
    backend_agent = Agent(
        name="echo",
        model=TestModel(custom_output_text="Hello from V2 server!"),
    )
    pool.register("echo", backend_agent)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]

    async with pool:
        app = create_app(pool, working_dir=str(tmp_path), store=EventStore(tmp_path / "events"))
        app.state.v2_state.permissions.saved_file = tmp_path / "permissions.json"
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        server = uvicorn.Server(config)
        serve_task = asyncio.create_task(server.serve(sockets=[sock]))
        try:
            while not server.started:
                await asyncio.sleep(0.01)

            remote = OpenCodeV2Agent(
                name="remote-v2",
                url=f"http://127.0.0.1:{port}",
                env=str(tmp_path),
                agent="echo",
                enable_logging=False,
            )
            async with remote:
                result = await remote.run("Ping!")
                assert result.content == "Hello from V2 server!"
                assert result.finish_reason == "stop"

                sessions = await remote.list_sessions()
                assert len(sessions) == 1
                loaded = await remote.load_session(sessions[0].session_id)
                assert loaded is not None
                assert any(
                    m.content == "Hello from V2 server!" for m in remote.conversation.chat_messages
                )
        finally:
            server.should_exit = True
            await serve_task
            sock.close()
