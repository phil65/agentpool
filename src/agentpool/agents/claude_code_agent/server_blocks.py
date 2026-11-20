"""Messages API blocks the API produces itself (server-executed tools, MCP connector, ...).

Claude Code passes these through unchanged. Server and MCP-connector tool calls are mapped onto
the canonical tool lifecycle (``ToolCallStartEvent`` -> ``ToolCallCompleteEvent``), API-side
compaction onto ``CompactionEvent``. Blocks without user-visible content are dropped.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, assert_never

from anthropic.types.beta import (
    BetaAdvisorToolResultBlock,
    BetaAdvisorToolResultError,
    BetaBashCodeExecutionToolResultBlock,
    BetaBashCodeExecutionToolResultError,
    BetaCodeExecutionToolResultBlock,
    BetaCodeExecutionToolResultError,
    BetaCompactionBlock,
    BetaContainerUploadBlock,
    BetaFallbackBlock,
    BetaMCPToolListingBlock,
    BetaMCPToolResultBlock,
    BetaMCPToolUseBlock,
    BetaRedactedThinkingBlock,
    BetaServerToolUseBlock,
    BetaTextEditorCodeExecutionToolResultBlock,
    BetaTextEditorCodeExecutionToolResultError,
    BetaToolSearchToolResultBlock,
    BetaToolSearchToolResultError,
    BetaWebFetchToolResultBlock,
    BetaWebFetchToolResultErrorBlock,
    BetaWebSearchToolResultBlock,
    BetaWebSearchToolResultError,
)
from pydantic_core import to_jsonable_python

from agentpool.agents.events import (
    CompactionEvent,
    ToolCallCompleteEvent,
    ToolCallProgressEvent,
    ToolCallStartEvent,
)


if TYPE_CHECKING:
    from clawd_code_sdk.models import ServerContentBlock

    from agentpool.agents.events import RichAgentStreamEvent


REDACTED_THINKING = "[redacted thinking]"
"""Placeholder content for thinking the API returned encrypted."""

type ServerToolUse = BetaServerToolUseBlock | BetaMCPToolUseBlock
type ServerToolResult = (
    BetaWebSearchToolResultBlock
    | BetaWebFetchToolResultBlock
    | BetaAdvisorToolResultBlock
    | BetaCodeExecutionToolResultBlock
    | BetaBashCodeExecutionToolResultBlock
    | BetaTextEditorCodeExecutionToolResultBlock
    | BetaToolSearchToolResultBlock
    | BetaMCPToolResultBlock
)


def server_tool_start(block: ServerToolUse) -> ToolCallStartEvent:
    """Start event for a server-executed or MCP-connector tool call."""
    match block:
        case BetaMCPToolUseBlock(id=tc_id, name=name, server_name=server, input=input_):
            title = f"{server}: {name}"
        case BetaServerToolUseBlock(id=tc_id, name=name, input=input_):
            title = name.replace("_", " ").capitalize()
        case _ as unreachable:
            assert_never(unreachable)
    return ToolCallStartEvent(
        tool_call_id=tc_id,
        tool_name=name,
        title=title,
        kind="search" if "search" in name else "fetch" if "fetch" in name else "other",
        raw_input=dict(input_),
    )


def server_tool_error(block: ServerToolResult) -> str | None:
    """Error message of a failed server tool result (None if it succeeded)."""
    match block:
        case BetaMCPToolResultBlock(is_error=True):
            return str(server_tool_output(block))
        case (
            BetaTextEditorCodeExecutionToolResultBlock(
                content=BetaTextEditorCodeExecutionToolResultError(
                    error_code=code, error_message=message
                )
            )
            | BetaToolSearchToolResultBlock(
                content=BetaToolSearchToolResultError(error_code=code, error_message=message)
            )
        ):
            return message or code
        case (
            BetaWebSearchToolResultBlock(content=BetaWebSearchToolResultError(error_code=code))
            | BetaWebFetchToolResultBlock(content=BetaWebFetchToolResultErrorBlock(error_code=code))
            | BetaAdvisorToolResultBlock(content=BetaAdvisorToolResultError(error_code=code))
            | BetaCodeExecutionToolResultBlock(
                content=BetaCodeExecutionToolResultError(error_code=code)
            )
            | BetaBashCodeExecutionToolResultBlock(
                content=BetaBashCodeExecutionToolResultError(error_code=code)
            )
        ):
            return str(code)
        case _:
            return None


def server_tool_output(block: ServerToolResult) -> Any:
    """JSON-compatible result of a server tool call."""
    match block:
        case BetaMCPToolResultBlock(content=str() as text):
            return text
        case BetaMCPToolResultBlock(content=list() as parts):
            return "\n".join(part.text for part in parts)
        case _:
            return to_jsonable_python(block.content)


def server_block_events(
    block: ServerContentBlock,
    *,
    started: set[str],
    calls: dict[str, ToolCallStartEvent],
    agent_name: str,
    session_id: str,
) -> list[RichAgentStreamEvent[Any]]:
    """Events for an API-produced block of a complete assistant message.

    Args:
        block: The block.
        started: IDs of server tool calls whose start was already emitted from the raw
            stream; their complete message only adds the final arguments.
        calls: Open server tool calls by ID (filled here, consumed by their results).
        agent_name: Name of the agent (for completion events).
        session_id: AgentPool session ID (for compaction events).
    """
    match block:
        case BetaServerToolUseBlock() | BetaMCPToolUseBlock():
            start = server_tool_start(block)
            calls[start.tool_call_id] = start
            if start.tool_call_id not in started:
                started.add(start.tool_call_id)
                return [start]
            progress = ToolCallProgressEvent(
                tool_call_id=start.tool_call_id,
                tool_name=start.tool_name,
                title=start.title,
                tool_input=start.raw_input,
            )
            return [progress]
        case (
            BetaWebSearchToolResultBlock()
            | BetaWebFetchToolResultBlock()
            | BetaAdvisorToolResultBlock()
            | BetaCodeExecutionToolResultBlock()
            | BetaBashCodeExecutionToolResultBlock()
            | BetaTextEditorCodeExecutionToolResultBlock()
            | BetaToolSearchToolResultBlock()
            | BetaMCPToolResultBlock()
        ):
            call = calls.pop(block.tool_use_id, None)
            complete = ToolCallCompleteEvent(
                tool_name=call.tool_name if call else block.type.removesuffix("_tool_result"),
                tool_call_id=block.tool_use_id,
                tool_input=call.raw_input if call else {},
                tool_result=server_tool_output(block),
                agent_name=agent_name,
                message_id="",
                error=server_tool_error(block),
            )
            return [complete]
        case BetaCompactionBlock():
            return [CompactionEvent(session_id=session_id, trigger="auto", phase="completed")]
        case (
            # Redacted thinking is shown from the raw stream (like all thinking).
            BetaRedactedThinkingBlock()
            | BetaContainerUploadBlock()
            | BetaFallbackBlock()
            | BetaMCPToolListingBlock()
        ):
            return []
        case _ as unreachable:
            assert_never(unreachable)
