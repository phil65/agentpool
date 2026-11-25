"""OpenAI-compatible responses endpoint."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from openai.types.responses import (
    Response,
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
    ResponseUsage,
)
from openai.types.responses.response_usage import InputTokensDetails, OutputTokensDetails


if TYPE_CHECKING:
    from openai.types.responses import ResponseOutputItem
    from pydantic_ai import UserContent

    from agentpool.agents.base_agent import BaseAgent
    from agentpool_server.openai_api_server.responses.models import ResponseRequest


async def handle_request(
    request: ResponseRequest,
    agent: BaseAgent[Any, Any],
    content_parts: list[UserContent],
) -> Response:
    """Run the agent on ``content_parts`` (validated input of ``request``)."""
    message = await agent.run(*content_parts)
    text = ResponseOutputText(text=str(message.content), annotations=[], type="output_text")
    output_msg_id = f"msg_{uuid4().hex}"
    output_msg = ResponseOutputMessage(
        id=output_msg_id,
        role="assistant",
        content=[text],
        status="completed",
        type="message",
    )

    calls = [
        ResponseFunctionToolCall(
            type="function_call",
            arguments=str(tc.args),
            call_id=tc.tool_call_id,
            name=tc.tool_name,
        )
        for tc in message.get_tool_calls()
    ]
    output: list[ResponseOutputItem] = [*calls, output_msg]

    usage_info = ResponseUsage(
        input_tokens=message.usage.input_tokens,
        input_tokens_details=InputTokensDetails(
            cached_tokens=message.usage.cache_read_tokens,
            cache_write_tokens=message.usage.cache_write_tokens,
        ),
        output_tokens=message.usage.output_tokens,
        output_tokens_details=OutputTokensDetails(reasoning_tokens=0),
        total_tokens=message.usage.total_tokens,
    )

    return Response(
        id=f"resp_{uuid4().hex}",
        created_at=int(datetime.now().timestamp()),
        model=request.model,
        object="response",
        output=output,
        parallel_tool_calls=True,
        tool_choice="auto",
        tools=[],
        status="completed",
        instructions=request.instructions,
        max_output_tokens=request.max_output_tokens,
        temperature=request.temperature,
        usage=usage_info,
        metadata=request.metadata,
    )
