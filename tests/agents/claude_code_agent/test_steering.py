"""Steering user messages into a running Claude Code turn."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from clawd_code_sdk.models import message_adapter
from pydantic_ai import ModelRequest, UserPromptPart

from agentpool.agents.claude_code_agent import ClaudeCodeAgent
from agentpool.agents.claude_code_agent.steering import RunSteering
from agentpool.agents.claude_code_agent.stream_adapter import ClaudeCodeStreamedResponse
from agentpool.agents.events import NoticeEvent
from agentpool.agents.events.reconstructor import MessageReconstructor


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from clawd_code_sdk.models.messages import MessagePriority


class FakeClient:
    """The steering part of ClaudeSDKClient."""

    def __init__(self) -> None:
        self.steered: list[tuple[str, MessagePriority]] = []

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

    await agent.steer("stop and say BANANA")
    # While a permission request waits, the message waits for its answer
    with agent._awaiting_user():
        await agent.steer("and then stop")

    assert client.steered == [("stop and say BANANA", "now"), ("and then stop", "later")]
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
        tool_metadata={},
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
