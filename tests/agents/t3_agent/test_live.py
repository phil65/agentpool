"""Live updates over T3's native socket: text streaming and approvals."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from mcp import types
from pydantic_ai import PartDeltaEvent, PartStartEvent, TextPart, TextPartDelta
from pyt3client.native import T3NativeClient, ThreadEvent, TurnItem
import pytest

from agentpool.agents.events import NoticeEvent, StreamCompleteEvent
from agentpool.agents.t3_agent import t3_agent
from agentpool.agents.t3_agent.items import ItemTracker
from agentpool.agents.t3_agent.live import ThreadFeed
from agentpool.tools.decisions import Approve, Deny
from agentpool.ui.mock_provider import MockInputProvider

from .test_t3_agent import ReplayServer, _agent, _run, _script


if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping

    from pydantic import JsonValue
    from pyt3client.native import ApprovalDecision, ThreadStreamItem

    from agentpool.agents.context import AgentContext
    from agentpool.tools.decisions import ToolDecision


def _turn_item(
    item_id: str,
    type_: str,
    status: str,
    text: str = "",
    *,
    run_id: str = "run",
    native_id: str | None = None,
    request: tuple[str, str, str] | None = None,
    questions: list[dict[str, JsonValue]] | None = None,
) -> TurnItem:
    """A native turn item; `request` is (request id, kind, prompt) of an approval."""
    data: dict[str, JsonValue] = {
        "id": item_id,
        "threadId": "th",
        "runId": run_id,
        "type": type_,
        "status": status,
        "text": text,
    }
    if native_id is not None:
        data["nativeItemRef"] = {"driver": "claudeAgent", "nativeId": native_id}
    if request is not None:
        data["requestId"], data["requestKind"], data["prompt"] = request
    if questions is not None:
        data["questions"] = list(questions)
    return TurnItem.model_validate(data)


def test_a_stale_or_cut_off_copy_does_not_shrink_text() -> None:
    tracker = ItemTracker(agent_name="t3", message_id="m")
    first = list(tracker.update([_turn_item("a", "assistant_message", "running", "Hello wor")]))
    assert len(first) == 1
    assert list(tracker.update([_turn_item("a", "assistant_message", "running", "Hello")])) == []
    [delta] = tracker.update([_turn_item("a", "assistant_message", "completed", "Hello world")])
    assert isinstance(delta, PartDeltaEvent)
    assert tracker.reply == "Hello world"


async def test_feed_collects_texts_approvals_and_tool_links() -> None:
    feed = ThreadFeed("th")
    assert await feed.wait(0.01) is None
    feed.add(_turn_item("turn-item:cmd", "command_execution", "running", native_id="toolu_1"))
    feed.add(_turn_item("r", "reasoning", "running", "a"))
    feed.add(_turn_item("r", "reasoning", "running", "ab"))
    approval = _turn_item(
        "turn-item:req",
        "approval_request",
        "waiting",
        native_id="toolu_1",
        request=("q1", "command", "ls"),
    )
    feed.add(approval)
    update = await feed.wait(0.01)
    assert update is not None
    assert [t.text for t in update.texts] == ["ab"]
    assert update.approvals == [approval]
    assert update.changed
    assert feed.tool_call_id(approval) == "turn-item:cmd"
    assert await feed.wait(0.01) is None


class FakeNative(T3NativeClient):
    """A native client without a socket: streams `events` once the thread is subscribed."""

    def __init__(self, events: list[TurnItem]) -> None:
        super().__init__("http://t3.test", "native-token")
        self.events = events
        self.responses: list[tuple[str, ApprovalDecision | None]] = []
        self.answers: list[tuple[str, dict[str, JsonValue]]] = []
        self.subscribed = asyncio.Event()

    async def subscribe_thread(
        self, thread_id: str, *, after_sequence: int | None = None
    ) -> AsyncIterator[ThreadStreamItem]:
        self.subscribed.set()
        for item in self.events:
            payload = item.model_dump(mode="json", by_alias=True)
            event = {"type": "turn-item.updated", "payload": payload}
            yield ThreadEvent.model_validate({"kind": "event", "sequence": 1, "event": event})
            await asyncio.sleep(0)
        await asyncio.Event().wait()  # Stays open, like a real subscription

    async def respond_to_request(
        self,
        thread_id: str,
        request_id: str,
        *,
        decision: ApprovalDecision | None = None,
        answers: Mapping[str, JsonValue] | None = None,
        feedback: str | None = None,
    ) -> int:
        if answers is not None:
            self.answers.append((request_id, dict(answers)))
        else:
            self.responses.append((request_id, decision))
        return 1


class _Approve(MockInputProvider):
    def __init__(self, result: ToolDecision) -> None:
        super().__init__()
        self.result: ToolDecision = result
        self.asked: list[tuple[str | None, str | None, dict[str, Any]]] = []

    async def get_tool_confirmation(
        self, context: AgentContext[Any], tool_description: str = ""
    ) -> ToolDecision:
        self.asked.append((context.tool_name, context.tool_call_id, context.tool_input))
        return self.result


def _first_turn() -> tuple[list[dict[str, Any]], str, str, str]:
    """The recorded first turn, its run id, and its reply's item id and text."""
    script = _script()
    script = script[: next(i for i, c in enumerate(script) if c["tool"] == "t3_thread_send")]
    launched = next(c for c in script if c["tool"] == "t3_thread_launch")
    last_read = next(c for c in reversed(script) if c["tool"] == "t3_thread_read")
    reply = next(i for i in last_read["result"]["items"] if i["type"] == "assistant_message")
    return script, str(launched["result"]["runId"]), str(reply["itemId"]), str(reply["text"])


@pytest.mark.parametrize(
    ("result", "decision"),
    [(Approve(), "accept"), (Deny(), "decline"), (Deny(stop=True), "cancel")],
)
async def test_live_text_streams_and_approvals_are_answered(
    monkeypatch: pytest.MonkeyPatch, result: ToolDecision, decision: ApprovalDecision
) -> None:
    monkeypatch.setattr(t3_agent, "LIVE_READ_INTERVAL", 0.01)
    script, run_id, reply_id, reply = _first_turn()
    events = [
        _turn_item("turn-item:cmd", "command_execution", "running", run_id=run_id, native_id="t9"),
        _turn_item(
            "turn-item:req",
            "approval_request",
            "waiting",
            run_id=run_id,
            native_id="t9",
            request=("q1", "command", "echo hello-from-t3"),
        ),
        _turn_item(reply_id, "assistant_message", "running", reply[:11], run_id=run_id),
        _turn_item(reply_id, "assistant_message", "running", reply[:19], run_id=run_id),
    ]
    native = FakeNative(events)
    provider = _Approve(result)
    client, agent = _agent(ReplayServer(script), input_provider=provider, native_client=native)
    async with client, agent:
        streamed = await _run(agent, "Run the shell command `echo hello-from-t3`, then reply.")
    assert native.subscribed.is_set()
    assert provider.asked == [("command", "turn-item:cmd", {"command": "echo hello-from-t3"})]
    assert native.responses == [("q1", decision)]
    starts = [e.part for e in streamed if isinstance(e, PartStartEvent)]
    assert [p.content for p in starts if isinstance(p, TextPart)] == [reply[:11]]
    deltas = [e.delta for e in streamed if isinstance(e, PartDeltaEvent)]
    assert "".join(d.content_delta for d in deltas if isinstance(d, TextPartDelta)) == reply[11:]
    assert not [e for e in streamed if isinstance(e, NoticeEvent)]
    assert isinstance(streamed[-1], StreamCompleteEvent)
    assert streamed[-1].message.content == reply


class _ChooseBlue(MockInputProvider):
    async def get_elicitation(
        self, params: types.ElicitRequestParams, *, tool_call_id: str | None = None
    ) -> types.ElicitResult | types.ErrorData:
        return types.ElicitResult(action="accept", content={"color": "Blue"})


async def test_live_questions_are_answered_over_the_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(t3_agent, "LIVE_READ_INTERVAL", 0.01)
    script, run_id, _, _ = _first_turn()
    question = {
        "id": "color",
        "header": "Color",
        "question": "Which color?",
        "options": [{"label": "Red", "description": ""}, {"label": "Blue", "description": ""}],
    }
    item = _turn_item(
        "turn-item:q", "user_input_request", "waiting", run_id=run_id, questions=[question]
    ).model_copy(update={"request_id": "q7"})
    native = FakeNative([item, item])  # Reported twice: asked once
    client, agent = _agent(ReplayServer(script), input_provider=_ChooseBlue(), native_client=native)
    async with client, agent:
        await _run(agent, "Run the shell command `echo hello-from-t3`, then reply.")
    assert native.answers == [("q7", {"color": "Blue"})]
