"""AG-UI agent helpers."""

from dataclasses import replace
from typing import TYPE_CHECKING, Any, assert_never
from uuid import uuid4

from pydantic import TypeAdapter

from agentpool.log import get_logger
from agentpool.tools.decisions import Approve, Deny


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from ag_ui.core import Event, ToolMessage
    import httpx2

    from agentpool.agents.context import AgentContext
    from agentpool.tools import Tool
    from agentpool_config.nodes import ToolConfirmationMode


logger = get_logger(__name__)


async def _should_confirm_tool(tool: Tool, confirmation_mode: ToolConfirmationMode) -> bool:
    """Determine if a tool requires confirmation based on mode.

    Args:
        tool: The tool to check
        confirmation_mode: Current confirmation mode

    Returns:
        True if confirmation is required
    """
    match confirmation_mode:
        case "never":
            return False
        case "always":
            return True
        case "per_tool":  # "per_tool" mode - check tool's requires_confirmation attribute
            return tool.requires_confirmation
        case _ as unreachable:
            assert_never(unreachable)


async def execute_tool_call(
    tool: Tool,
    context: AgentContext[Any],
    *,
    confirmation_mode: ToolConfirmationMode = "never",
) -> ToolMessage:
    """Execute a single tool call locally and return the result.

    Tool call details (tool_call_id, tool_input) are read from the context.

    Args:
        tool: The tool to execute
        context: Node context with tool_call_id and tool_input set
        confirmation_mode: Tool confirmation mode

    Returns:
        ToolMessage with execution result
    """
    from ag_ui.core import ToolMessage as AGUIToolMessage

    tool_call_id = context.tool_call_id or ""
    args = context.tool_input

    # Ask the user if the mode says so: a denial is the call's result, told to the model
    if await _should_confirm_tool(tool, confirmation_mode):
        call_ctx = replace(context, tool_name=tool.name)
        match await call_ctx.decide_tool(tool.description or ""):
            case Approve(args=edited):
                args = edited if edited is not None else args
            case Deny(stop=stop) as denial:
                logger.info("Tool call denied by the user", tool=tool.name, stop=stop)
                if stop:
                    call_ctx.stop_turn()
                return AGUIToolMessage(
                    id=str(uuid4()),
                    tool_call_id=tool_call_id,
                    content=denial.reason(tool.name),
                    error="Turn stopped by the user" if stop else None,
                )

    # Execute the tool
    logger.info("Executing tool", tool=tool.name, args=args)
    try:
        result = await tool.run(**args)
        result_str = str(result) if not isinstance(result, str) else result
        logger.debug("Tool executed", tool=tool.name, result=result_str[:100])
        return AGUIToolMessage(id=str(uuid4()), tool_call_id=tool_call_id, content=result_str)
    except Exception as e:
        logger.exception("Tool execution failed", tool=tool.name)
        return AGUIToolMessage(
            id=str(uuid4()),
            tool_call_id=tool_call_id,
            content=f"Error executing tool: {e}",
            error=str(e),
        )


async def parse_sse_stream(response: httpx2.Response) -> AsyncIterator[Event]:
    """Parse Server-Sent Events stream.

    Args:
        response: HTTP response with SSE stream

    Yields:
        Parsed AG-UI events
    """
    from ag_ui.core import Event

    event_adapter = TypeAdapter[Event](Event)
    buffer = ""
    async for chunk in response.aiter_text():
        buffer += chunk
        # Process complete SSE events
        while "\n\n" in buffer:
            event_text, buffer = buffer.split("\n\n", 1)
            # Parse SSE format: "data: {json}\n"
            for line in event_text.split("\n"):
                if not line.startswith("data: "):
                    continue
                json_str = line.removeprefix("data: ")
                try:
                    yield event_adapter.validate_json(json_str)
                except (ValueError, TypeError) as e:
                    logger.warning("Failed to parse AG-UI event", json=json_str[:100], error=str(e))
