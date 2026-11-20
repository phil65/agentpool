"""Rebuild a V2 event log from AgentPool's stored conversation history.

Used for sessions that have no V2 event log yet (created through another protocol, or
before the V2 server existed). Stored ``ChatMessage`` objects are replayed through the
same :class:`RunEventConverter` that handles live runs, so history and live sessions
render identically.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic_ai.messages import (
    BaseToolCallPart,
    BaseToolReturnPart,
    ModelRequest,
    ModelResponse,
    PartEndEvent,
    PartStartEvent,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from agentpool.agents.events import (
    StreamCompleteEvent,
    ToolCallCompleteEvent,
    ToolCallStartEvent,
)
from agentpool.utils.pydantic_ai_helpers import safe_args_as_dict
from agentpool_server.opencode_v2_server.events import new_message_id
from agentpool_server.opencode_v2_server.run_converter import RunEventConverter
from opencode_v2_sdk.models import (
    SessionExecutionStarted,
    SessionExecutionStartedData,
    SessionExecutionSucceeded,
    SessionExecutionSucceededData,
    SessionInboxDelivered,
    SessionInboxDeliveredData,
    SessionInboxEnqueued,
    SessionInboxEnqueuedData,
    SessionInboxItemUser,
    SessionInboxUserPayload,
)


if TYPE_CHECKING:
    from collections.abc import Sequence

    from agentpool.messaging import ChatMessage
    from agentpool_server.opencode_v2_server.session import V2Session
    from opencode_v2_sdk.models import ModelRef


def user_text(message: ChatMessage[Any]) -> str:
    """Extract the user-visible text of a stored user message."""
    match message.content:
        case str() as text:
            return text
        case list() | tuple() as items:
            return "\n".join(item for item in items if isinstance(item, str))
        case _:
            return str(message.content)


def prompt_text(message: ChatMessage[Any]) -> str | None:
    """User prompt text of the first model request of an assistant message, if any."""
    for model_message in message.messages:
        if isinstance(model_message, ModelRequest):
            texts = [
                part.content if isinstance(part.content, str) else user_prompt_text(part.content)
                for part in model_message.parts
                if isinstance(part, UserPromptPart)
            ]
            return "\n".join(texts) if texts else None
    return None


def user_prompt_text(content: Sequence[Any]) -> str:
    """Text items of a multi-part user prompt."""
    return "\n".join(item for item in content if isinstance(item, str))


def _message_time(message: ChatMessage[Any]) -> int:
    return int(message.timestamp.timestamp() * 1000)


def _v2_message_id(message_id: str) -> str:
    return message_id if message_id.startswith("msg_") else new_message_id()


def _return_error(part: ToolReturnPart | BaseToolReturnPart) -> str | None:
    if isinstance(part, ToolReturnPart) and part.outcome != "success":
        return f"{part.outcome}: {part.model_response_str()}"
    return None


def _replay_assistant(
    converter: RunEventConverter,
    message: ChatMessage[Any],
    *,
    agent_name: str,
) -> None:
    responses = [m for m in message.messages if isinstance(m, ModelResponse | ModelRequest)]
    tool_calls: dict[str, BaseToolCallPart] = {}
    for model_message in responses:
        match model_message:
            case ModelResponse(parts=parts):
                for index, part in enumerate(parts):
                    match part:
                        case TextPart() | ThinkingPart():
                            converter.handle(PartStartEvent(index=index, part=part))
                            converter.handle(PartEndEvent(index=index, part=part))
                        case ToolCallPart():
                            tool_calls[part.tool_call_id] = part
                            converter.handle(PartStartEvent(index=index, part=part))
                            converter.handle(PartEndEvent(index=index, part=part))
                            converter.handle(
                                ToolCallStartEvent(
                                    tool_call_id=part.tool_call_id,
                                    tool_name=part.tool_name,
                                    title=part.tool_name,
                                    raw_input=safe_args_as_dict(part),
                                )
                            )
                        case _:
                            pass
            case ModelRequest(parts=parts):
                for request_part in parts:
                    if not isinstance(request_part, ToolReturnPart | BaseToolReturnPart):
                        continue
                    call = tool_calls.get(request_part.tool_call_id)
                    tool_input = safe_args_as_dict(call) if call else {}
                    converter.handle(
                        ToolCallCompleteEvent(
                            tool_name=request_part.tool_name,
                            tool_call_id=request_part.tool_call_id,
                            tool_input=tool_input,
                            tool_result=request_part.content,
                            agent_name=agent_name,
                            message_id=message.message_id,
                            error=_return_error(request_part),
                        )
                    )
    converter.handle(StreamCompleteEvent(message=message))


def replay_history(
    session: V2Session,
    messages: Sequence[ChatMessage[Any]],
    *,
    agent_name: str,
    model_ref: ModelRef,
) -> None:
    """Emit durable events reproducing the given conversation into a (fresh) session.

    Each user message opens an execution, the following assistant message(s) are replayed
    as its steps, and the execution is closed before the next user message.

    Args:
        session: Session to emit into (should only contain ``session.created``)
        messages: Stored conversation messages, oldest first
        agent_name: Agent ID to attribute assistant steps to
        model_ref: Model reference for assistant steps
    """
    execution_open = False

    def close_execution() -> None:
        nonlocal execution_open
        if execution_open:
            session.emit(
                SessionExecutionSucceeded, SessionExecutionSucceededData(session_id=session.id)
            )
            execution_open = False

    def open_execution() -> None:
        nonlocal execution_open
        if not execution_open:
            session.emit(
                SessionExecutionStarted, SessionExecutionStartedData(session_id=session.id)
            )
            execution_open = True

    def user_turn(message_id: str, text: str) -> None:
        close_execution()
        inbox_id = _v2_message_id(message_id)
        item = SessionInboxItemUser(payload=SessionInboxUserPayload(text=text), delivery="queue")
        session.emit(
            SessionInboxEnqueued,
            SessionInboxEnqueuedData(session_id=session.id, inbox_id=inbox_id, item=item),
        )
        open_execution()
        session.emit(
            SessionInboxDelivered,
            SessionInboxDeliveredData(session_id=session.id, inbox_id=inbox_id),
        )

    previous_role: str | None = None
    try:
        for message in messages:
            clock = _message_time(message)
            session.replay_clock = clock
            if message.role == "user":
                user_turn(message.message_id, user_text(message))
                previous_role = "user"
                continue
            if previous_role != "user" and (prompt := prompt_text(message)) is not None:
                # Storage usually keeps only the final message of a turn; its model
                # request carries the user prompt.
                user_turn(new_message_id(), prompt)
            previous_role = message.role
            open_execution()
            converter = RunEventConverter(
                session_id=session.id,
                agent_id=agent_name,
                model_ref=model_ref,
                emit=session.emit_sink,
                clock=lambda clock=clock: clock,
            )
            _replay_assistant(converter, message, agent_name=agent_name)
            converter.finish()
        close_execution()
    finally:
        session.replay_clock = None
