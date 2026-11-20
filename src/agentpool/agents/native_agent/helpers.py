"""The main Agent. Can do all sort of crazy things."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

from pydantic_ai import (
    BaseToolCallPart,
    BaseToolReturnPart,
    FinalResultEvent,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ModelResponse,
    PartDeltaEvent,
    PartEndEvent,
    PartStartEvent,
    TextPart,
    ToolReturnPart,
)

from agentpool import log
from agentpool.agents.events import ToolCallCompleteEvent
from agentpool.utils.pydantic_ai_helpers import safe_args_as_dict


if TYPE_CHECKING:
    from collections.abc import AsyncIterable, AsyncIterator

    from pydantic_ai import AgentStreamEvent as PydanticAIStreamEvent, ModelMessage
    from pydantic_ai.models import Model
    from pydantic_ai.native_tools import AbstractNativeTool

    from agentpool.agents.events import RichAgentStreamEvent
    from agentpool.agents.events.events import AgentStreamEvent

logger = log.get_logger(__name__)


def filter_builtin_tools(
    tools: list[AbstractNativeTool],
    model: Model,
) -> list[AbstractNativeTool]:
    """Filter builtin tools to those supported by the model.

    Args:
        tools: Builtin tools to filter
        model: Resolved model instance

    Returns:
        Filtered list containing only tools the model supports
    """
    from pydantic_ai.native_tools import SUPPORTED_NATIVE_TOOLS

    try:
        supported = model.profile.get("supported_native_tools", SUPPORTED_NATIVE_TOOLS)
    except NotImplementedError:  # some models (->FallbackModel) raise this when accessing profile
        return tools
    filtered = [t for t in tools if isinstance(t, tuple(supported))]
    if len(filtered) != len(tools):
        dropped = [type(t).__name__ for t in tools if not isinstance(t, tuple(supported))]
        logger.info("Dropping unsupported builtin tools for %s: %s", model.model_name, dropped)
    return filtered


async def forward_supported_events(
    stream: AsyncIterable[PydanticAIStreamEvent],
) -> AsyncIterator[AgentStreamEvent]:
    """Forward the pydantic-ai stream events agentpool handles, dropping the rest.

    pydantic-ai also emits realtime, capability, deferred-tool and enqueued-message
    events, which have no agentpool counterpart and are not forwarded.
    """
    async for event in stream:
        match event:
            case (
                PartStartEvent()
                | PartDeltaEvent()
                | PartEndEvent()
                | FinalResultEvent()
                | FunctionToolCallEvent()
                | FunctionToolResultEvent()
            ):
                yield event
            case _:
                pass


def process_tool_event(
    agent_name: str,
    event: RichAgentStreamEvent[Any],
    pending_tool_calls: dict[str, BaseToolCallPart],
    message_id: str,
) -> ToolCallCompleteEvent | None:
    """Process tool-related events and return combined event when complete.

    Args:
        agent_name: Name of the agent
        event: The streaming event to process
        pending_tool_calls: Dict tracking in-progress tool calls by ID
        message_id: Message ID for the combined event

    Returns:
        ToolCallCompleteEvent if a tool call completed, None otherwise
    """
    # Both function and native tools use PartStartEvent with BaseToolCallPart/BaseToolReturnPart.
    match event:
        case (
            PartStartEvent(part=BaseToolCallPart() as tool_part)
            | FunctionToolCallEvent(part=tool_part)
        ):
            pending_tool_calls[tool_part.tool_call_id] = tool_part
        case (
            PartStartEvent(part=BaseToolReturnPart(tool_call_id=call_id, content=content))
            | FunctionToolResultEvent(part=ToolReturnPart(tool_call_id=call_id, content=content))
        ):
            if call_info := pending_tool_calls.pop(call_id, None):
                return ToolCallCompleteEvent(
                    tool_name=call_info.tool_name,
                    tool_call_id=call_id,
                    tool_input=safe_args_as_dict(call_info),
                    tool_result=content,
                    agent_name=agent_name,
                    message_id=message_id,
                )
    return None


def extract_text_from_messages(
    messages: list[ModelMessage],
    include_interruption_note: bool = False,
) -> str:
    """Extract text content from pydantic-ai messages.

    Args:
        messages: List of ModelRequest/ModelResponse messages
        include_interruption_note: Whether to append interruption notice

    Returns:
        Concatenated text content from all ModelResponse TextParts
    """
    content = "".join(
        part.content
        for msg in messages
        if isinstance(msg, ModelResponse)
        for part in msg.parts
        if isinstance(part, TextPart)
    )
    if include_interruption_note:
        if content:
            content += "\n\n"
        content += "[Request interrupted by user]"
    return content


async def response_cost(response: ModelResponse) -> Decimal | None:
    """Cost of a single model response (provider-reported cost, else calculated)."""
    from pydantic_ai.usage import RunUsage

    from agentpool.messaging.messages import TokenCost

    provider_cost = (response.provider_details or {}).get("cost")
    if provider_cost is not None:
        return Decimal(str(provider_cost))
    usage = RunUsage(requests=1)
    usage.incr(response.usage)
    cost = await TokenCost.from_usage(
        model=response.model_name or "", usage=usage, provider=response.provider_name
    )
    return cost.total_cost if cost is not None else None
