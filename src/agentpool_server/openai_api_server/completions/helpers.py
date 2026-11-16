"""Helpers for OpenAI-compatible API server."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

import anyenv
from pydantic_ai import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.messages import NativeToolCallPart, NativeToolReturnPart

from agentpool.agents.events import CompactionEvent, ToolCallStartEvent
from agentpool.log import get_logger
from agentpool.utils.pydantic_ai_helpers import safe_args_as_dict


if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from agentpool.common_types import SupportsRunStream
    from agentpool_server.openai_api_server.completions.models import ChatCompletionRequest

logger = get_logger(__name__)


def _format_tool_call(tool_name: str, args: dict[str, Any]) -> str:
    """Format a tool call as readable text."""
    args_str = ", ".join(f"{k}={v!r}" for k, v in args.items()) if args else ""
    return f"\n🔧 **{tool_name}**({args_str})\n"


def _format_tool_result(tool_name: str, content: Any, is_error: bool = False) -> str:
    """Format a tool result as readable text."""
    result_str = str(content)
    if len(result_str) > 500:  # noqa: PLR2004
        result_str = result_str[:500] + "..."
    icon = "❌" if is_error else "✅"
    return f"{icon} {tool_name}: {result_str}\n\n"


async def stream_response(
    agent: SupportsRunStream[Any],
    content: str,
    request: ChatCompletionRequest,
) -> AsyncGenerator[str]:
    """Generate streaming response chunks."""
    response_id = f"chatcmpl-{int(time.time() * 1000)}"
    created = int(time.time())

    def _make_chunk(text: str) -> str:
        chunk_data = {
            "id": response_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": request.model,
            "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}],
        }
        return f"data: {anyenv.dump_json(chunk_data)}\n\n"

    try:
        # First chunk with role
        first_chunk = {
            "id": response_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": request.model,
            "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
        }
        yield f"data: {anyenv.dump_json(first_chunk)}\n\n"

        async for event in agent.run_stream(content):
            match event:
                # Text output
                case PartStartEvent(part=TextPart(content=delta)) if delta:
                    yield _make_chunk(delta)
                case PartDeltaEvent(delta=TextPartDelta(content_delta=delta)) if delta:
                    yield _make_chunk(delta)

                # Tool call started (pydantic-ai function tools)
                case FunctionToolCallEvent(part=ToolCallPart() as part):
                    args = safe_args_as_dict(part, default={})
                    yield _make_chunk(_format_tool_call(part.tool_name, args))

                # Tool call started (builtin tools)
                case PartStartEvent(part=NativeToolCallPart() as part):
                    args = safe_args_as_dict(part, default={})
                    yield _make_chunk(_format_tool_call(part.tool_name, args))

                # Rich tool call start (custom events from our agents)
                case ToolCallStartEvent(tool_name=name, title=title):
                    label = title or name
                    yield _make_chunk(f"\n🔧 **{label}**\n")

                # Tool failed with retry
                case FunctionToolResultEvent(
                    part=ToolReturnPart(outcome="failed" | "retried", tool_name=name) as result
                ):
                    error_msg = result.model_response_str(wrap_if_error=False)
                    yield _make_chunk(_format_tool_result(name, error_msg, is_error=True))

                # Tool completed successfully
                case FunctionToolResultEvent(part=ToolReturnPart(content=out, tool_name=name)):
                    yield _make_chunk(_format_tool_result(name, out))

                # Builtin tool completed
                case PartStartEvent(
                    part=NativeToolReturnPart(content=out, tool_name=name),
                ):
                    yield _make_chunk(_format_tool_result(name, out))

                # Compaction
                case CompactionEvent(phase="starting"):
                    yield _make_chunk(event.format())

        final_chunk = {
            "id": response_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": request.model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
        yield f"data: {anyenv.dump_json(final_chunk)}\n\n"
        yield "data: [DONE]\n\n"

    except Exception as e:
        logger.exception("Error during streaming response")
        yield _make_chunk(f"Error: {e!s}")
        error_final = {
            "id": response_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": request.model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
        yield f"data: {anyenv.dump_json(error_final)}\n\n"
        yield "data: [DONE]\n\n"
