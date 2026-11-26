"""The main Agent. Can do all sort of crazy things."""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from pydantic_ai import (
    BaseToolCallPart,
    BaseToolReturnPart,
    FinalResultEvent,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ModelResponse,
    NativeToolCallPart,
    PartDeltaEvent,
    PartEndEvent,
    PartStartEvent,
    TextPart,
    ToolReturnPart,
)

from agentpool import log
from agentpool.acp_utils import generate_tool_title, infer_tool_kind
from agentpool.agents.events import ToolCallCompleteEvent, ToolCallStartEvent
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


@dataclass
class ToolEventTracker:
    """Translates pydantic-ai tool events into the canonical tool lifecycle events.

    Emits one ``ToolCallStartEvent`` when a call is issued and one ``ToolCallCompleteEvent``
    (with ``error`` for failed, retried, denied or interrupted calls) when it ends.
    """

    agent_name: str
    message_id: str
    pending: dict[str, BaseToolCallPart] = field(default_factory=dict)
    started: set[str] = field(default_factory=set)

    def process(
        self, event: RichAgentStreamEvent[Any]
    ) -> list[ToolCallStartEvent | ToolCallCompleteEvent]:
        """Canonical events derived from a pydantic-ai stream event."""
        match event:
            case (
                PartStartEvent(part=NativeToolCallPart() as call_part)
                | FunctionToolCallEvent(part=call_part)
            ):
                # Native (provider-executed) tools start with their part, function tools
                # once their arguments are complete and execution begins.
                self.pending[call_part.tool_call_id] = call_part
                return (
                    [self._start(call_part)] if call_part.tool_call_id not in self.started else []
                )
            case PartStartEvent(part=BaseToolCallPart() as call_part):
                self.pending[call_part.tool_call_id] = call_part  # Arguments still streaming
            case PartStartEvent(part=BaseToolReturnPart(tool_call_id=call_id, content=content)):
                return self._complete(call_id, content, None)
            case FunctionToolResultEvent(part=ToolReturnPart(tool_call_id=call_id) as result):
                error = None
                if result.outcome != "success":
                    error = f"{result.outcome}: {result.model_response_str()}"
                return self._complete(call_id, result.content, error)
            case _:
                pass
        return []

    def _start(self, part: BaseToolCallPart) -> ToolCallStartEvent:
        self.started.add(part.tool_call_id)
        args = safe_args_as_dict(part)
        return ToolCallStartEvent(
            tool_call_id=part.tool_call_id,
            tool_name=part.tool_name,
            title=generate_tool_title(part.tool_name, args),
            kind=infer_tool_kind(part.tool_name),
            raw_input=args,
        )

    def _complete(
        self, call_id: str, content: Any, error: str | None
    ) -> list[ToolCallStartEvent | ToolCallCompleteEvent]:
        call_info = self.pending.pop(call_id, None)
        if call_info is None:
            return []
        self.started.discard(call_id)
        return [
            ToolCallCompleteEvent(
                tool_name=call_info.tool_name,
                tool_call_id=call_id,
                tool_input=safe_args_as_dict(call_info),
                tool_result=content,
                agent_name=self.agent_name,
                message_id=self.message_id,
                error=error,
            )
        ]


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
