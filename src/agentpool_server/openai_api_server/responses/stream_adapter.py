"""Adapter that converts pydantic-ai stream events to OpenAI Responses API SSE events.

Maps agent-side tool calls to MCP call output items, since MCP calls
are the Responses API's model for server-side tool execution.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import anyenv
from openai.types.responses import (
    Response,
    ResponseCompletedEvent,
    ResponseContentPartAddedEvent,
    ResponseContentPartDoneEvent,
    ResponseCreatedEvent,
    ResponseInProgressEvent,
    ResponseMcpCallArgumentsDoneEvent,
    ResponseMcpCallCompletedEvent,
    ResponseMcpCallFailedEvent,
    ResponseMcpCallInProgressEvent,
    ResponseOutputItemAddedEvent,
    ResponseOutputItemDoneEvent,
    ResponseOutputMessage,
    ResponseOutputText,
    ResponseTextDeltaEvent,
    ResponseTextDoneEvent,
)
from openai.types.responses.mcp_tool_call_error import McpToolExecutionError
from openai.types.responses.response_output_item import McpCall
from pydantic_ai import (
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
)

from agentpool.agents.events import CompactionEvent, ToolCallCompleteEvent, ToolCallStartEvent
from agentpool.log import get_logger


if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from openai.types.responses import (
        ResponseStatus,
    )
    from pydantic_ai import UserContent

    from agentpool.agents.events import RichAgentStreamEvent
    from agentpool.common_types import SupportsRunStream
    from agentpool_server.openai_api_server.responses.models import ResponseRequest

logger = get_logger(__name__)

SERVER_LABEL = "agentpool"


async def stream_responses(
    agent: SupportsRunStream[Any],
    content_parts: list[UserContent],
    adapter: ResponsesStreamAdapter,
) -> AsyncGenerator[str]:
    """Stream a responses API request through the adapter.

    ``content_parts`` come from ``extract_user_content``, validated before the
    streaming response starts (an error raised here could not change the status).
    """

    async def _event_gen() -> AsyncGenerator[Any]:
        async for event in agent.run_stream(*content_parts):
            yield event

    async for line in adapter.stream(_event_gen()):
        yield line


def _sse_line(event: Any) -> str:
    """Format a Responses API event as an SSE line."""
    event_type = event.type
    data = event.model_dump_json()
    return f"event: {event_type}\ndata: {data}\n\n"


@dataclass
class ResponsesStreamAdapter:
    """Converts pydantic-ai stream events to OpenAI Responses API SSE events.

    Tracks output items (message + tool calls) and emits proper lifecycle
    events for each. Tool calls are mapped to MCP call items.
    """

    request: ResponseRequest
    response_id: str = field(default_factory=lambda: f"resp_{uuid4().hex}")
    _seq: int = field(default=0, init=False)
    _msg_id: str = field(default_factory=lambda: f"msg_{uuid4().hex}")
    _text_parts: list[str] = field(default_factory=list)
    _output_index: int = field(default=0, init=False)
    _msg_output_index: int = field(default=-1, init=False)
    _msg_started: bool = field(default=False, init=False)
    _tool_output_indices: dict[str, int] = field(default_factory=dict)
    _tool_items: dict[str, McpCall] = field(default_factory=dict)
    _created_at: int = field(default_factory=lambda: int(datetime.now().timestamp()))

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def _base_response(self, status: ResponseStatus = "in_progress") -> Response:
        """Build the Response envelope."""
        output: list[Any] = list(self._tool_items.values())
        text = "".join(self._text_parts)
        msg_status = "completed" if status == "completed" else "in_progress"
        output_text = ResponseOutputText(text=text, annotations=[], type="output_text")
        output_msg = ResponseOutputMessage(
            id=self._msg_id,
            role="assistant",
            content=[output_text],
            status=msg_status,
            type="message",
        )
        output.append(output_msg)

        return Response(
            id=self.response_id,
            created_at=self._created_at,
            model=self.request.model,
            object="response",
            output=output,
            parallel_tool_calls=self.request.parallel_tool_calls,
            tool_choice="auto",
            tools=[],
            status=status,
            instructions=self.request.instructions,
            max_output_tokens=self.request.max_output_tokens,
            temperature=self.request.temperature,
            metadata=self.request.metadata,
        )

    def _ensure_msg_started(self) -> list[str]:
        """Emit message output item + content part if not yet started."""
        if self._msg_started:
            return []
        self._msg_started = True
        self._msg_output_index = self._output_index
        self._output_index += 1

        lines: list[str] = []
        msg_item = ResponseOutputMessage(
            id=self._msg_id,
            role="assistant",
            content=[],
            status="in_progress",
            type="message",
        )
        lines.append(
            _sse_line(
                ResponseOutputItemAddedEvent(
                    item=msg_item,
                    output_index=self._msg_output_index,
                    sequence_number=self._next_seq(),
                    type="response.output_item.added",
                )
            )
        )
        text_part = ResponseOutputText(text="", annotations=[], type="output_text")
        lines.append(
            _sse_line(
                ResponseContentPartAddedEvent(
                    content_index=0,
                    item_id=self._msg_id,
                    output_index=self._msg_output_index,
                    part=text_part,
                    sequence_number=self._next_seq(),
                    type="response.content_part.added",
                )
            )
        )
        return lines

    async def stream(
        self,
        events: AsyncGenerator[RichAgentStreamEvent[Any]],
    ) -> AsyncGenerator[str]:
        """Convert agent stream events to SSE lines."""
        resp = self._base_response("in_progress")
        yield _sse_line(
            ResponseCreatedEvent(
                response=resp,
                sequence_number=self._next_seq(),
                type="response.created",
            )
        )
        yield _sse_line(
            ResponseInProgressEvent(
                response=self._base_response("in_progress"),
                sequence_number=self._next_seq(),
                type="response.in_progress",
            )
        )

        async for event in events:
            for line in self._handle_event(event):
                yield line

        for line in self._finalize():
            yield line

    def _handle_event(self, event: RichAgentStreamEvent[Any]) -> list[str]:
        """Convert a single agent event to SSE lines."""
        lines: list[str] = []

        match event:
            case PartStartEvent(part=TextPart(content=delta)) if delta:
                lines.extend(self._ensure_msg_started())
                self._text_parts.append(delta)
                lines.append(
                    _sse_line(
                        ResponseTextDeltaEvent(
                            content_index=0,
                            delta=delta,
                            item_id=self._msg_id,
                            logprobs=[],
                            output_index=self._msg_output_index,
                            sequence_number=self._next_seq(),
                            type="response.output_text.delta",
                        )
                    )
                )

            case PartDeltaEvent(delta=TextPartDelta(content_delta=delta)) if delta:
                lines.extend(self._ensure_msg_started())
                self._text_parts.append(delta)
                lines.append(
                    _sse_line(
                        ResponseTextDeltaEvent(
                            content_index=0,
                            delta=delta,
                            item_id=self._msg_id,
                            logprobs=[],
                            output_index=self._msg_output_index,
                            sequence_number=self._next_seq(),
                            type="response.output_text.delta",
                        )
                    )
                )

            # Canonical tool lifecycle (all agent types); repeated starts only update a call
            case ToolCallStartEvent(tool_call_id=tc_id, tool_name=name, raw_input=raw_input) if (
                tc_id not in self._tool_items
            ):
                lines.extend(self._start_tool_call(tc_id, name, raw_input))

            case ToolCallCompleteEvent(tool_call_id=tc_id, error=str() as error_msg):
                lines.extend(self._fail_tool_call(tc_id, error_msg))

            case ToolCallCompleteEvent(tool_call_id=tc_id, tool_name=name, tool_result=out):
                lines.extend(self._complete_tool_call(tc_id, name, str(out)))

            case CompactionEvent(phase="starting"):
                lines.extend(self._ensure_msg_started())
                text = event.format()
                self._text_parts.append(text)
                lines.append(
                    _sse_line(
                        ResponseTextDeltaEvent(
                            content_index=0,
                            delta=text,
                            item_id=self._msg_id,
                            logprobs=[],
                            output_index=self._msg_output_index,
                            sequence_number=self._next_seq(),
                            type="response.output_text.delta",
                        )
                    )
                )

        return lines

    def _start_tool_call(
        self, tool_call_id: str, tool_name: str, args: dict[str, Any]
    ) -> list[str]:
        """Emit events for a new MCP tool call."""
        args_json = anyenv.dump_json(args)
        output_idx = self._output_index
        self._output_index += 1
        self._tool_output_indices[tool_call_id] = output_idx

        item = McpCall(
            id=tool_call_id,
            arguments=args_json,
            name=tool_name,
            server_label=SERVER_LABEL,
            type="mcp_call",
            status="in_progress",
        )
        self._tool_items[tool_call_id] = item

        return [
            _sse_line(
                ResponseOutputItemAddedEvent(
                    item=item,
                    output_index=output_idx,
                    sequence_number=self._next_seq(),
                    type="response.output_item.added",
                )
            ),
            _sse_line(
                ResponseMcpCallInProgressEvent(
                    item_id=tool_call_id,
                    output_index=output_idx,
                    sequence_number=self._next_seq(),
                    type="response.mcp_call.in_progress",
                )
            ),
            _sse_line(
                ResponseMcpCallArgumentsDoneEvent(
                    arguments=args_json,
                    item_id=tool_call_id,
                    output_index=output_idx,
                    sequence_number=self._next_seq(),
                    type="response.mcp_call_arguments.done",
                )
            ),
        ]

    def _complete_tool_call(self, tool_call_id: str, tool_name: str, output: str) -> list[str]:
        """Emit events for a completed tool call."""
        output_idx = self._tool_output_indices.get(tool_call_id, 0)
        lines: list[str] = []

        if tool_call_id in self._tool_items:
            item = self._tool_items[tool_call_id]
            self._tool_items[tool_call_id] = McpCall(
                id=item.id,
                arguments=item.arguments,
                name=item.name,
                server_label=item.server_label,
                type="mcp_call",
                status="completed",
                output=output,
            )

        lines.append(
            _sse_line(
                ResponseMcpCallCompletedEvent(
                    item_id=tool_call_id,
                    output_index=output_idx,
                    sequence_number=self._next_seq(),
                    type="response.mcp_call.completed",
                )
            )
        )
        if tool_call_id in self._tool_items:
            lines.append(
                _sse_line(
                    ResponseOutputItemDoneEvent(
                        item=self._tool_items[tool_call_id],
                        output_index=output_idx,
                        sequence_number=self._next_seq(),
                        type="response.output_item.done",
                    )
                )
            )
        return lines

    def _fail_tool_call(self, tool_call_id: str, error: str) -> list[str]:
        """Emit events for a failed tool call."""
        output_idx = self._tool_output_indices.get(tool_call_id, 0)
        lines: list[str] = []

        if tool_call_id in self._tool_items:
            item = self._tool_items[tool_call_id]
            self._tool_items[tool_call_id] = McpCall(
                id=item.id,
                arguments=item.arguments,
                name=item.name,
                server_label=item.server_label,
                type="mcp_call",
                status="failed",
                error=McpToolExecutionError(content=error, type="mcp_tool_execution_error"),
            )

        lines.append(
            _sse_line(
                ResponseMcpCallFailedEvent(
                    item_id=tool_call_id,
                    output_index=output_idx,
                    sequence_number=self._next_seq(),
                    type="response.mcp_call.failed",
                )
            )
        )
        if tool_call_id in self._tool_items:
            lines.append(
                _sse_line(
                    ResponseOutputItemDoneEvent(
                        item=self._tool_items[tool_call_id],
                        output_index=output_idx,
                        sequence_number=self._next_seq(),
                        type="response.output_item.done",
                    )
                )
            )
        return lines

    def _finalize(self) -> list[str]:
        """Emit closing events."""
        lines: list[str] = []
        lines.extend(self._ensure_msg_started())

        full_text = "".join(self._text_parts)

        lines.append(
            _sse_line(
                ResponseTextDoneEvent(
                    content_index=0,
                    item_id=self._msg_id,
                    logprobs=[],
                    output_index=self._msg_output_index,
                    sequence_number=self._next_seq(),
                    text=full_text,
                    type="response.output_text.done",
                )
            )
        )

        text_part = ResponseOutputText(text=full_text, annotations=[], type="output_text")
        lines.append(
            _sse_line(
                ResponseContentPartDoneEvent(
                    content_index=0,
                    item_id=self._msg_id,
                    output_index=self._msg_output_index,
                    part=text_part,
                    sequence_number=self._next_seq(),
                    type="response.content_part.done",
                )
            )
        )

        msg_item = ResponseOutputMessage(
            id=self._msg_id,
            role="assistant",
            content=[text_part],
            status="completed",
            type="message",
        )
        lines.append(
            _sse_line(
                ResponseOutputItemDoneEvent(
                    item=msg_item,
                    output_index=self._msg_output_index,
                    sequence_number=self._next_seq(),
                    type="response.output_item.done",
                )
            )
        )

        lines.append(
            _sse_line(
                ResponseCompletedEvent(
                    response=self._base_response("completed"),
                    sequence_number=self._next_seq(),
                    type="response.completed",
                )
            )
        )

        return lines
