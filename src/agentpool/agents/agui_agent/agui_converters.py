"""AG-UI to native event converters.

This module provides conversion from AG-UI protocol events to native agentpool
streaming events, enabling AGUIAgent to yield the same event types as native agents.

Also provides conversion of native Tool objects to AG-UI Tool format for
client-side tool execution.
"""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING, Any, assert_never
from uuid import uuid4

import anyenv
from pydantic_ai import (
    CachePoint,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UploadedFile,
    UserPromptPart,
)
from pydantic_ai.messages import (
    AudioUrl,
    BinaryContent,
    DocumentUrl,
    ImageUrl,
    TextContent,
    VideoUrl,
)

from agentpool.agents.events import (
    CustomEvent,
    PartDeltaEvent,
    PlanUpdateEvent,
    RunErrorEvent,
    RunStartedEvent,
    ToolCallProgressEvent,
    ToolCallStartEvent as NativeToolCallStartEvent,
)
from agentpool.utils.todos import PlanEntry


if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from ag_ui.core import (
        Event,
        InputContent,
        Message,
        Tool as AGUITool,
    )
    from pydantic_ai import ModelMessage, UserContent

    from agentpool.agents.events import RichAgentStreamEvent
    from agentpool.tools.base import Tool


def agui_to_native_event(event: Event) -> Iterator[RichAgentStreamEvent[Any]]:
    """Convert AG-UI event to native streaming event.

    Args:
        event: AG-UI Event from SSE stream

    Returns:
        Corresponding native event, or None if no mapping exists
    """
    from ag_ui.core import (
        ActivityDeltaEvent,
        ActivitySnapshotEvent,
        CustomEvent as AGUICustomEvent,
        MessagesSnapshotEvent,
        RawEvent,
        ReasoningEncryptedValueEvent,
        ReasoningEndEvent,
        ReasoningMessageChunkEvent,
        ReasoningMessageContentEvent,
        ReasoningMessageEndEvent,
        ReasoningMessageStartEvent,
        ReasoningStartEvent,
        RunErrorEvent as AGUIRunErrorEvent,
        RunFinishedEvent,
        RunStartedEvent as AGUIRunStartedEvent,
        StateDeltaEvent,
        StateSnapshotEvent,
        StepFinishedEvent,
        StepStartedEvent,
        TextMessageChunkEvent,
        TextMessageContentEvent,
        TextMessageEndEvent,
        TextMessageStartEvent,
        ThinkingEndEvent,
        ThinkingStartEvent,
        ThinkingTextMessageContentEvent,
        ThinkingTextMessageEndEvent,
        ThinkingTextMessageStartEvent,
        ToolCallArgsEvent,
        ToolCallChunkEvent,
        ToolCallEndEvent,
        ToolCallResultEvent,
        ToolCallStartEvent,
    )

    match event:
        # === Lifecycle Events ===

        case AGUIRunStartedEvent(thread_id=thread_id, run_id=run_id):
            yield RunStartedEvent(session_id=thread_id, run_id=run_id)

        case AGUIRunErrorEvent(message=message, code=code):
            yield RunErrorEvent(message=message, code=code)

        # === Text Message Events ===
        case TextMessageContentEvent(delta=delta) | TextMessageChunkEvent(delta=str() as delta):
            yield PartDeltaEvent.text(index=0, content=delta)

        case TextMessageStartEvent() | TextMessageEndEvent():
            pass

        # === Thinking/Reasoning Events ===

        case (
            ThinkingTextMessageContentEvent(delta=delta)
            | ReasoningMessageContentEvent(delta=delta)
            | ReasoningMessageChunkEvent(delta=str() as delta)
        ):
            yield PartDeltaEvent.thinking(index=0, content=delta)

        case (
            ThinkingStartEvent()
            | ThinkingEndEvent()
            | ThinkingTextMessageStartEvent()
            | ThinkingTextMessageEndEvent()
        ):
            # These mark thinking blocks but don't carry content
            pass

        case (
            ReasoningStartEvent()
            | ReasoningEndEvent()
            | ReasoningMessageStartEvent()
            | ReasoningMessageEndEvent()
            | ReasoningEncryptedValueEvent()
        ):
            # These mark reasoning blocks but don't carry streamable content
            pass

        # === Tool Call Events ===

        case ToolCallStartEvent(tool_call_id=str() as tc_id, tool_call_name=name):
            yield NativeToolCallStartEvent(tool_call_id=tc_id, tool_name=name, title=name)

        case ToolCallChunkEvent(tool_call_id=str() as tc_id, tool_call_name=str() as name):
            yield NativeToolCallStartEvent(tool_call_id=tc_id, tool_name=name, title=name)

        case ToolCallArgsEvent(tool_call_id=tc_id, delta=_):
            yield ToolCallProgressEvent(tool_call_id=tc_id, status="in_progress")

        case ToolCallResultEvent(tool_call_id=tc_id, content=content, message_id=_):
            yield ToolCallProgressEvent(tool_call_id=tc_id, status="completed", message=content)

        case ToolCallEndEvent(tool_call_id=tc_id):
            yield ToolCallProgressEvent(tool_call_id=tc_id, status="completed")

        # === Activity Events -> PlanUpdateEvent ===

        case ActivitySnapshotEvent(activity_type=activity_type, content=content):
            # Map activity content to plan entries if it looks like a plan
            if (
                activity_type.upper() == "PLAN"
                and isinstance(content, list)
                and (entries := _content_to_plan_entries(content))
            ):
                yield PlanUpdateEvent(entries=entries)
            # For other activity types, wrap as custom event
            yield CustomEvent(
                event_data={"activity_type": activity_type, "content": content},
                event_type=f"activity_{activity_type.lower()}",
                source="ag-ui",
            )

        case ActivityDeltaEvent(activity_type=activity_type, patch=patch):
            yield CustomEvent(
                event_data={"activity_type": activity_type, "patch": patch},
                event_type=f"activity_delta_{activity_type.lower()}",
                source="ag-ui",
            )

        # === State Management Events ===

        case StateSnapshotEvent(snapshot=snapshot):
            yield CustomEvent(event_data=snapshot, event_type="state_snapshot", source="ag-ui")

        case StateDeltaEvent(delta=delta):
            yield CustomEvent(event_data=delta, event_type="state_delta", source="ag-ui")

        case MessagesSnapshotEvent(messages=messages):
            data = [m.model_dump() for m in messages]
            yield CustomEvent(event_data=data, event_type="messages_snapshot", source="ag-ui")

        # === Special Events ===

        case RawEvent(event=raw_event, source=source):
            yield CustomEvent(event_data=raw_event, event_type="raw", source=source or "ag-ui")

        case AGUICustomEvent(name=name, value=value):
            yield CustomEvent(event_data=value, event_type=name, source="ag-ui")

        case (
            TextMessageChunkEvent()
            | ToolCallChunkEvent()
            | RunFinishedEvent()
            | StepStartedEvent()
            | StepFinishedEvent()
            | ReasoningMessageChunkEvent()
        ):
            pass

        case _ as unreachable:
            assert_never(unreachable)  # ty:ignore[type-assertion-failure]


def _content_to_plan_entries(content: list[Any]) -> list[PlanEntry]:
    """Convert AG-UI activity content to PlanEntry list.

    Args:
        content: List of plan items from ActivitySnapshotEvent

    Returns:
        List of PlanEntry objects
    """
    entries: list[PlanEntry] = []
    for item in content:
        if isinstance(item, dict):
            # Try to extract plan entry fields
            entry_content = item.get("content") or item.get("text") or item.get("description", "")
            priority = item.get("priority", "medium")
            status = item.get("status", "pending")

            # Normalize values
            if priority not in ("high", "medium", "low"):
                priority = "medium"
            if status not in ("pending", "in_progress", "completed"):
                status = "pending"

            if entry_content:
                entry = PlanEntry(content=str(entry_content), priority=priority, status=status)
                entries.append(entry)
        elif isinstance(item, str):
            entries.append(PlanEntry(content=item, priority="medium", status="pending"))
    return entries


def to_agui_input_content(parts: Sequence[UserContent]) -> list[InputContent]:
    """Convert pydantic-ai UserContent parts to AG-UI InputContent format."""
    from ag_ui.core import (
        AudioInputContent,
        DocumentInputContent,
        ImageInputContent,
        InputContentDataSource,
        InputContentUrlSource,
        TextInputContent,
        VideoInputContent,
    )

    result: list[InputContent] = []
    for part in parts:
        match part:
            case str() as text:
                result.append(TextInputContent(text=text))

            case TextContent(content=text):
                result.append(TextInputContent(text=text))

            case ImageUrl(url=url):
                mime = part.media_type or "image/png"
                source = InputContentUrlSource(value=str(url), mime_type=mime)
                result.append(ImageInputContent(source=source))

            case AudioUrl(url=url):
                mime = part.media_type or "audio/mpeg"
                source = InputContentUrlSource(value=str(url), mime_type=mime)
                result.append(AudioInputContent(source=source))

            case VideoUrl(url=url):
                mime = part.media_type or "video/mp4"
                source = InputContentUrlSource(value=str(url), mime_type=mime)
                result.append(VideoInputContent(source=source))

            case DocumentUrl(url=url):
                mime = part.media_type or "application/pdf"
                source = InputContentUrlSource(value=str(url), mime_type=mime)
                result.append(DocumentInputContent(source=source))

            case BinaryContent(data=data, media_type=media_type):
                encoded = base64.b64encode(data).decode()
                mime = media_type or "application/octet-stream"
                data_source = InputContentDataSource(value=encoded, mime_type=mime)
                if part.is_image:
                    result.append(ImageInputContent(source=data_source))
                elif part.is_audio:
                    result.append(AudioInputContent(source=data_source))
                elif part.is_video:
                    result.append(VideoInputContent(source=data_source))
                else:  # aguis BinaryContent is deprecated
                    result.append(DocumentInputContent(source=data_source))
            case UploadedFile() | CachePoint():
                pass
            case _ as unreachable:
                assert_never(unreachable)
    return result


def to_agui_tool(tool: Tool) -> AGUITool:
    """Convert native Tool to AG-UI Tool format."""
    from ag_ui.core import Tool as AGUITool

    func_schema = tool.schema["function"]
    return AGUITool(
        name=func_schema["name"],
        description=func_schema["description"],
        parameters=func_schema["parameters"],
    )


def model_messages_to_agui(messages: Sequence[ModelMessage]) -> Iterator[Message]:
    """Convert pydantic-ai ModelMessage sequence to AG-UI Message format.

    This converts the conversation history from pydantic-ai's internal format
    to AG-UI protocol format for sending to remote AG-UI servers.

    Args:
        messages: Sequence of pydantic-ai ModelRequest/ModelResponse

    Returns:
        List of AG-UI Message objects (UserMessage, AssistantMessage, etc.)
    """
    from ag_ui.core import (
        AssistantMessage,
        FunctionCall,
        SystemMessage,
        ToolCall,
        ToolMessage,
        UserMessage,
    )

    for msg in messages:
        match msg:
            case ModelRequest(parts=request_parts):
                # ModelRequest can contain user prompts, system prompts, or tool returns
                for req_part in request_parts:
                    match req_part:
                        case UserPromptPart(content=str() as content):
                            yield UserMessage(id=str(uuid4()), content=content)

                        case UserPromptPart(content=list() as content):
                            agui_parts = to_agui_input_content(content)
                            yield UserMessage(id=str(uuid4()), content=agui_parts)

                        case UserPromptPart(content=content):
                            yield UserMessage(id=str(uuid4()), content=str(content))

                        case SystemPromptPart(content=content):
                            yield SystemMessage(id=str(uuid4()), content=content)

                        case ToolReturnPart(tool_call_id=tool_call_id, content=content):
                            # Convert content to string
                            if isinstance(content, str):
                                content_str = content
                            else:
                                content_str = anyenv.dump_json(content)
                            yield ToolMessage(
                                id=str(uuid4()),
                                tool_call_id=tool_call_id,
                                content=content_str,
                            )

            case ModelResponse(parts=response_parts):
                # ModelResponse contains assistant content and/or tool calls
                text_parts: list[str] = []
                tool_calls: list[ToolCall] = []

                for resp_part in response_parts:
                    match resp_part:
                        case TextPart(content=content):
                            text_parts.append(content)

                        case ThinkingPart(content=content):
                            # Include thinking in content (some UIs show it)
                            if content:
                                text_parts.append(f"[thinking] {content}")

                        case ToolCallPart(tool_call_id=tc_id, tool_name=tool_name, args=args):
                            # Convert args to JSON string
                            match args:
                                case str():
                                    args_str = args
                                case dict():
                                    args_str = anyenv.dump_json(args)
                                case None:
                                    args_str = ""
                                case _ as unreachable:
                                    assert_never(unreachable)  # ty:ignore[type-assertion-failure]
                            call = FunctionCall(name=tool_name, arguments=args_str)
                            tc = ToolCall(id=tc_id, type="function", function=call)
                            tool_calls.append(tc)

                # Create AssistantMessage with content and/or tool_calls
                yield AssistantMessage(
                    id=str(uuid4()),
                    content=" ".join(text_parts) if text_parts else None,
                    tool_calls=tool_calls or None,
                )
