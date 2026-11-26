"""Steering user messages into a running Claude Code turn."""

from typing import TYPE_CHECKING, Any

from clawd_code_sdk.models import message_adapter
from pydantic_ai import ModelRequest, UserPromptPart

from agentpool.agents.claude_code_agent import ClaudeCodeAgent
from agentpool.agents.claude_code_agent.steering import RunSteering, run_messages
from agentpool.agents.claude_code_agent.stream_adapter import ClaudeCodeStreamedResponse
from agentpool.agents.events import NoticeEvent
from agentpool.agents.events.reconstructor import MessageReconstructor


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from clawd_code_sdk.models.messages import MessagePriority


class FakeClient:
    """The steering part of ClaudeSDKClient, replaying ``messages`` as the CLI's response."""

    def __init__(self, messages: list[Any] | None = None) -> None:
        self.steered: list[tuple[str, MessagePriority]] = []
        self.messages = messages or []

    async def receive_response(self) -> AsyncIterator[Any]:
        for message in self.messages:
            yield message

    async def steer(self, message: str, *, priority: MessagePriority = "now") -> str:
        self.steered.append((message, priority))
        return f"u{len(self.steered)}"


def _lifecycle(command_uuid: str, state: str) -> Any:
    return message_adapter.validate_python({
        "type": "command_lifecycle",
        "command_uuid": command_uuid,
        "state": state,
        "uuid": "frame",
        "session_id": "s",
    })


def _steering() -> tuple[RunSteering, FakeClient, MessageReconstructor]:
    client = FakeClient()
    reconstructor = MessageReconstructor(initial_prompts=["count to 300"])
    steering = RunSteering(client=client, reconstructor=reconstructor)  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]
    return steering, client, reconstructor


async def test_steer_during_a_run_uses_the_cli() -> None:
    agent = ClaudeCodeAgent(name="cc")
    steering, client, _ = _steering()
    agent._steering = steering

    await agent.steer("also check the tests")
    await agent.steer("stop and say BANANA", interrupt=True)
    # While a permission request waits, even an interrupting message waits for the step
    with agent._awaiting_user():
        await agent.steer("and then stop", interrupt=True)

    assert client.steered == [
        ("also check the tests", "next"),
        ("stop and say BANANA", "now"),
        ("and then stop", "next"),
    ]
    assert not agent.has_pending_injections()


async def test_steer_outside_a_run_is_injected() -> None:
    agent = ClaudeCodeAgent(name="cc")
    await agent.steer("later please")
    assert agent.has_pending_injections()


async def test_steered_message_joins_the_history_when_taken_in() -> None:
    steering, _, reconstructor = _steering()
    await steering.send("stop and say BANANA", priority="now")

    steering.on_lifecycle(_lifecycle("u1", "queued"))
    assert len(reconstructor.model_messages) == 1
    steering.on_lifecycle(_lifecycle("u1", "started"))

    match reconstructor.model_messages[-1]:
        case ModelRequest(parts=[UserPromptPart(content="stop and say BANANA")]):
            pass
        case other:
            raise AssertionError(f"steered message not in history: {other}")
    assert steering.pending == {}


async def test_adapter_reports_lifecycle_and_dropped_messages() -> None:
    frames = [_lifecycle("u1", "queued"), _lifecycle("u1", "refused")]
    seen: list[str] = []

    async def source() -> AsyncIterator[Any]:
        for frame in frames:
            yield frame

    response = ClaudeCodeStreamedResponse(
        provider_name="claude_code",
        stream=source(),
        agent_name="cc",
        session_id="local",
        on_command_lifecycle=lambda frame: seen.append(frame.state),
    )
    events = [event async for event in response._get_event_iterator()]

    assert seen == ["queued", "refused"]
    assert events == [
        NoticeEvent(
            title="Message not delivered",
            severity="warning",
            description="Claude Code refused a message sent mid-run.",
        )
    ]


def _result(text: str, *, uuids: list[str] | None = None, origin: str | None = None) -> Any:
    return message_adapter.validate_python({
        "type": "result",
        "subtype": "success",
        "uuid": f"r-{text}",
        "session_id": "s",
        "duration_ms": 1,
        "duration_api_ms": 1,
        "is_error": False,
        "num_turns": 1,
        "total_cost_usd": 0,
        "usage": {"input_tokens": 1, "output_tokens": 1},
        "stop_reason": "end_turn",
        "result": text,
        "user_message_uuids": uuids,
        "origin": {"kind": origin} if origin else None,
    })


IDLE = message_adapter.validate_python({
    "type": "system",
    "subtype": "session_state_changed",
    "state": "idle",
    "uuid": "i",
    "session_id": "s",
})


async def _read(client: FakeClient, steering: RunSteering) -> list[Any]:
    return [message async for message in run_messages(client, steering)]  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]


async def test_run_ends_at_the_result_that_answers_its_prompt() -> None:
    # A background subagent keeps the CLI busy: its follow-up cycle is not part of the run
    answer = _result("started", uuids=["p"])
    follow_up = _result("The background agent finished", origin="task-notification")
    client = FakeClient([_lifecycle("p", "started"), answer, follow_up, IDLE])
    steering = RunSteering(client=client, reconstructor=MessageReconstructor(), unanswered={"p"})  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]

    assert await _read(client, steering) == [_lifecycle("p", "started"), answer]
    assert steering.closed


async def test_steered_run_ends_at_the_answer_of_the_steer() -> None:
    client = FakeClient()
    steering = RunSteering(client=client, reconstructor=MessageReconstructor(), unanswered={"p"})  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]
    await steering.send("stop and say BANANA", priority="now")  # uuid "u1"
    # The cycle the steer interrupted answers the prompt; the steer is answered next
    interrupted = _result("", uuids=["p"])
    banana = _result("BANANA", uuids=["u1"])
    client.messages = [interrupted, banana, IDLE]

    assert await _read(client, steering) == [interrupted, banana]


async def test_steer_folded_into_the_cycle_answers_the_prompt_too() -> None:
    # Priority "next" during a tool call: the steer joins the running cycle, whose one
    # result names only the steer
    client = FakeClient()
    steering = RunSteering(
        client=client,  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]
        reconstructor=MessageReconstructor(),
        unanswered={"p"},
        taken_in={"p"},
    )
    await steering.send("stop and say BANANA", priority="next")  # uuid "u1"
    banana = _result("BANANA", uuids=["u1"])
    follow_up = _result("The background agent finished", origin="task-notification")
    client.messages = [_lifecycle("u1", "started"), banana, follow_up, IDLE]

    assert await _read(client, steering) == [_lifecycle("u1", "started"), banana]


async def test_run_without_named_results_ends_at_idle() -> None:
    messages = [_result("done"), IDLE]
    client = FakeClient(messages)
    steering = RunSteering(client=client, reconstructor=MessageReconstructor(), unanswered={"p"})  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]
    assert await _read(client, steering) == messages
    assert not steering.closed


async def test_steer_after_the_answer_waits_for_the_next_run() -> None:
    agent = ClaudeCodeAgent(name="cc")
    steering, client, _ = _steering()
    steering.closed = True
    agent._steering = steering

    await agent.steer("one more thing")

    assert client.steered == []
    assert agent.has_pending_injections()
