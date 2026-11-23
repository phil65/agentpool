"""T3Agent against a replay of a real T3 Code environment's MCP answers.

``data/two_turns.json`` holds the structured results of the tool calls of a live
two-turn run (claudeAgent / haiku: a shell command and a reply, then a follow-up),
in call order, with repeated identical thread reads left out.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

import httpx2
from mcp import types
from pyt3client import T3Client
import pytest

from agentpool.agents.events import (
    NoticeEvent,
    StreamCompleteEvent,
    ToolCallCompleteEvent,
    ToolCallStartEvent,
)
from agentpool.agents.t3_agent import T3Agent
from agentpool.ui.mock_provider import MockInputProvider


DATA = Path(__file__).parent / "data" / "two_turns.json"
BASE_URL = "http://t3.test"


@dataclass
class ReplayServer:
    """Answers T3's ``/mcp`` endpoint with recorded tool results, in order."""

    script: list[dict[str, Any]]
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        if request.method == "DELETE":
            return httpx2.Response(200)
        body = json.loads(request.content)
        headers = {"mcp-session-id": "s"}
        match body["method"]:
            case "initialize":
                result: dict[str, Any] = {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "T3 Code", "version": "replay"},
                }
            case "notifications/initialized":
                return httpx2.Response(202, headers=headers)
            case "tools/call":
                name, arguments = body["params"]["name"], body["params"]["arguments"]
                self.calls.append((name, arguments))
                expected = self.script.pop(0)
                assert expected["tool"] == name, f"called {name}, expected {expected['tool']}"
                payload = expected["result"]
                result = {"content": [{"type": "text", "text": json.dumps(payload)}]}
                if expected.get("structured", True):
                    result["structuredContent"] = payload
            case other:
                raise AssertionError(f"unexpected MCP method {other}")
        response = {"jsonrpc": "2.0", "id": body["id"], "result": result}
        return httpx2.Response(200, json=response, headers=headers)


def _script() -> list[dict[str, Any]]:
    return json.loads(DATA.read_text())


def _agent(server: ReplayServer, **kwargs: Any) -> tuple[T3Client, T3Agent[None]]:
    http = httpx2.AsyncClient(transport=httpx2.MockTransport(server.handle))
    client = T3Client(BASE_URL, "token", http_client=http)
    return client, T3Agent(
        project="/work/proj",
        provider="claudeAgent",
        model="claude-haiku-5-5",
        runtime_mode="full-access",
        client=client,
        enable_logging=False,
        poll_interval=0.001,
        **kwargs,
    )


async def _run(agent: T3Agent[None], prompt: str) -> list[Any]:
    return [event async for event in agent.run_stream(prompt)]


async def test_two_turns_launch_then_send() -> None:
    server = ReplayServer(_script())
    client, agent = _agent(server)
    async with client, agent:
        first = await _run(agent, "Run the shell command `echo hello-from-t3`, then reply.")
        second = await _run(agent, "What did the command print? One word.")
    assert not server.script, "every recorded call was made"
    names = [name for name, _ in server.calls]
    assert names.count("t3_thread_launch") == 1
    assert names.count("t3_thread_send") == 1
    launch = next(args for name, args in server.calls if name == "t3_thread_launch")
    assert launch["modelSelection"] == {"instanceId": "claudeAgent", "model": "claude-haiku-5-5"}
    assert launch["runtimeMode"] == "full-access"

    starts = [e for e in first if isinstance(e, ToolCallStartEvent)]
    assert [s.title for s in starts] == ["Preparing workspace", "`echo hello-from-t3`"]
    done = [e for e in first if isinstance(e, ToolCallCompleteEvent)]
    assert done[-1].tool_result == "$ echo hello-from-t3\nhello-from-t3"
    assert not [e for e in first + second if isinstance(e, NoticeEvent)]

    [complete] = [e for e in first if isinstance(e, StreamCompleteEvent)]
    assert complete.message.content == "The command printed `hello-from-t3`, as expected."
    assert complete.message.finish_reason == "stop"
    [complete] = [e for e in second if isinstance(e, StreamCompleteEvent)]
    assert complete.message.content == "hello-from-t3"


class _ChooseBlue(MockInputProvider):
    async def get_elicitation(
        self, params: types.ElicitRequestParams, *, tool_call_id: str | None = None
    ) -> types.ElicitResult | types.ErrorData:
        assert isinstance(params, types.ElicitRequestFormParams)
        assert params.requested_schema["properties"]["color"]["enum"] == ["Red", "Blue"]
        return types.ElicitResult(action="accept", content={"color": "Blue"})


def _with_question(script: list[dict[str, Any]], *, listed: list[str]) -> list[dict[str, Any]]:
    """The first turn, with a pending request reported by its third thread read."""
    first_turn = script[: next(i for i, c in enumerate(script) if c["tool"] == "t3_thread_send")]
    reads = [i for i, c in enumerate(first_turn) if c["tool"] == "t3_thread_read"]
    waiting = copy.deepcopy(first_turn[reads[2]])
    waiting["result"]["thread"]["pendingRequestCount"] = 1
    extra: list[dict[str, Any]] = [
        waiting,
        {"tool": "t3_pending_request", "result": {"requestIds": listed}, "structured": False},
    ]
    if listed:
        question = {
            "id": "color",
            "header": "Color",
            "question": "Which color?",
            "options": [
                {"label": "Red", "description": ""},
                {"label": "Blue", "description": "", "value": "blue"},
            ],
        }
        read = {"requestId": "req-1", "questions": [question]}
        extra += [
            {"tool": "t3_pending_request", "result": read, "structured": False},
            {"tool": "t3_pending_request", "result": {"sequence": 7}, "structured": False},
        ]
    return [*first_turn[: reads[2]], *extra, *first_turn[reads[2] :]]


async def test_questions_are_answered_through_the_input_provider() -> None:
    server = ReplayServer(_with_question(_script(), listed=["req-1"]))
    client, agent = _agent(server, input_provider=_ChooseBlue())
    async with client, agent:
        events = await _run(agent, "Run the shell command `echo hello-from-t3`, then reply.")
    assert not server.script
    respond = [args for name, args in server.calls if args.get("action") == "respond"]
    assert respond == [
        {
            "action": "respond",
            "threadId": respond[0]["threadId"],
            "requestId": "req-1",
            "answers": {"color": "blue"},
        }
    ]
    assert not [e for e in events if isinstance(e, NoticeEvent)]


async def test_waiting_for_an_approval_is_reported_once() -> None:
    server = ReplayServer(_with_question(_script(), listed=[]))
    client, agent = _agent(server)
    async with client, agent:
        events = await _run(agent, "Run the shell command `echo hello-from-t3`, then reply.")
    notices = [e for e in events if isinstance(e, NoticeEvent)]
    assert [n.title for n in notices] == ["The T3 thread waits for approval in T3 Code"]


async def test_project_folder_must_exist() -> None:
    server = ReplayServer(_script())
    client, agent = _agent(server)
    agent._project = "/elsewhere"
    with pytest.raises(RuntimeError, match="No T3 project"):
        async with client, agent:
            pass
