"""Built-in event handlers for simple console output."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any

from pydantic_ai import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
    ToolCallPart,
    ToolReturnPart,
)

from agentpool.agents.events import (
    RunErrorEvent,
    StreamCompleteEvent,
    ToolCallProgressEvent,
    ToolCallStartEvent,
)
from agentpool.utils.pydantic_ai_helpers import safe_args_as_dict


if TYPE_CHECKING:
    from collections.abc import Sequence

    from agentpool.agents.context import AgentContext
    from agentpool.agents.events import RichAgentStreamEvent
    from agentpool.common_types import AnyEventHandlerType, IndividualEventHandler


async def simple_print_handler(ctx: AgentContext[Any], event: RichAgentStreamEvent[Any]) -> None:
    """Simple event handler that prints text and basic tool information.

    Focus: Core text output and minimal tool notifications.
    Prints:
    - Text content (streaming)
    - Tool calls (name only)
    - Errors
    """
    match event:
        case (
            PartStartEvent(part=TextPart(content=delta))
            | PartDeltaEvent(delta=TextPartDelta(content_delta=delta))
        ):
            print(delta, end="", flush=True, file=sys.stderr)

        case FunctionToolCallEvent(part=ToolCallPart() as part):
            kwargs_str = ", ".join(f"{k}={v!r}" for k, v in safe_args_as_dict(part).items())
            print(f"\n🔧 {part.tool_name}({kwargs_str})", flush=True, file=sys.stderr)

        case FunctionToolResultEvent(part=ToolReturnPart() as return_part):
            print(f"Result: {return_part.content}", file=sys.stderr)

        case RunErrorEvent(message=message):
            print(f"\n❌ Error: {message}", flush=True, file=sys.stderr)

        case StreamCompleteEvent():
            print(file=sys.stderr)  # Final newline


async def detailed_print_handler(ctx: AgentContext[Any], event: RichAgentStreamEvent[Any]) -> None:
    """Detailed event handler with rich tool execution information.

    Focus: Comprehensive execution visibility.
    Prints:
    - Text content (streaming)
    - Thinking content
    - Tool calls with inputs
    - Tool results
    - Tool progress with title
    - Errors
    """
    match event:
        case (
            PartStartEvent(part=TextPart(content=delta))
            | PartDeltaEvent(delta=TextPartDelta(content_delta=delta))
        ):
            print(delta, end="", flush=True, file=sys.stderr)

        case (
            PartStartEvent(part=ThinkingPart(content=delta))
            | PartDeltaEvent(delta=ThinkingPartDelta(content_delta=delta))
        ):
            if delta:
                print(f"\n💭 {delta}", end="", flush=True, file=sys.stderr)

        case ToolCallStartEvent(tool_name=tool_name, title=title, tool_call_id=call_id):
            print(f"\n🔧 {tool_name}: {title} (#{call_id[:8]})", flush=True, file=sys.stderr)

        case FunctionToolCallEvent(part=ToolCallPart(tool_name=tool_name, args=args)):
            args_str = str(args)
            if len(args_str) > 100:  # noqa: PLR2004
                args_str = args_str[:97] + "..."
            print(f"  📝 Input: {args_str}", flush=True, file=sys.stderr)

        case FunctionToolResultEvent(part=ToolReturnPart(content=content, tool_name=tool_name)):
            result_str = str(content)
            if len(result_str) > 150:  # noqa: PLR2004
                result_str = result_str[:147] + "..."
            print(f"  ✅ {tool_name}: {result_str}", flush=True, file=sys.stderr)

        case ToolCallProgressEvent(title=title, status=status):
            if title:
                emoji = {"completed": "✅", "failed": "❌"}.get(status, "⏳")
                print(f"  {emoji} {title}", flush=True, file=sys.stderr)

        case RunErrorEvent(message=message, code=code):
            error_info = f" [{code}]" if code else ""
            print(f"\n❌ Error{error_info}: {message}", flush=True, file=sys.stderr)

        case StreamCompleteEvent():
            print(file=sys.stderr)  # Final newline


def create_file_stream_handler(
    path: str,
    mode: str = "a",
    include_tools: bool = False,
    include_thinking: bool = False,
) -> IndividualEventHandler:
    """Create an event handler that streams text output to a file.

    Args:
        path: Path to the output file
        mode: File open mode ('w' for overwrite, 'a' for append)
        include_tools: Whether to include tool call/result information
        include_thinking: Whether to include thinking content

    Returns:
        Event handler function that writes to the specified file
    """
    from pathlib import Path

    file_path = Path(path).expanduser()
    file_path.parent.mkdir(parents=True, exist_ok=True)

    # Open file handle that persists across calls
    file_handle = file_path.open(mode, encoding="utf-8")

    async def file_stream_handler(ctx: AgentContext[Any], event: RichAgentStreamEvent[Any]) -> None:
        """Stream agent output to file."""
        match event:
            case (
                PartStartEvent(part=TextPart(content=delta))
                | PartDeltaEvent(delta=TextPartDelta(content_delta=delta))
            ):
                file_handle.write(delta)
                file_handle.flush()

            case (
                PartStartEvent(part=ThinkingPart(content=delta))
                | PartDeltaEvent(delta=ThinkingPartDelta(content_delta=delta))
            ):
                if include_thinking and delta:
                    file_handle.write(f"\n[thinking] {delta}")
                    file_handle.flush()

            case FunctionToolCallEvent(part=ToolCallPart() as part):
                if include_tools:
                    kwargs_str = ", ".join(f"{k}={v!r}" for k, v in safe_args_as_dict(part).items())
                    file_handle.write(f"\n[tool] {part.tool_name}({kwargs_str})\n")
                    file_handle.flush()

            case FunctionToolResultEvent(part=ToolReturnPart() as return_part):
                if include_tools:
                    file_handle.write(f"[result] {return_part.content}\n")
                    file_handle.flush()

            case RunErrorEvent(message=message):
                file_handle.write(f"\n[error] {message}\n")
                file_handle.flush()

            case StreamCompleteEvent():
                file_handle.write("\n")
                file_handle.flush()

    return file_stream_handler


def resolve_event_handlers(
    event_handlers: Sequence[AnyEventHandlerType] | None,
) -> list[IndividualEventHandler]:
    """Resolve event handlers, converting builtin handler names to actual handlers."""
    if not event_handlers:
        return []
    builtin_map = {"simple": simple_print_handler, "detailed": detailed_print_handler}
    return [builtin_map[h] if isinstance(h, str) else h for h in event_handlers]
