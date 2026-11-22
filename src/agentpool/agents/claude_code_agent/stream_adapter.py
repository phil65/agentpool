"""Stream adapter for converting Claude SDK messages to agentpool events.

Tool Call Event Flow
--------------------
The SDK streams events in a specific order. Understanding this is critical for
avoiding race conditions with permission dialogs:

1. **content_block_start** (StreamEvent)
   - Contains tool_use_id, tool name
   - We emit ToolCallStartEvent here (early, with empty args)
   - ACP converter sends `tool_call` notification to client

2. **content_block_delta** (StreamEvent, multiple)
   - Contains input_json_delta with partial JSON args
   - We emit PartDeltaEvent(ToolCallPartDelta) for streaming
   - ACP converter accumulates args, doesn't send notifications

3. **AssistantMessage** with ToolUseBlock
   - Contains complete tool call info (id, name, full args)
   - We do NOT emit events here (would race with permission)
   - Just track file modifications silently

4. **content_block_stop**, **message_delta**, **message_stop** (StreamEvent)
   - Signal completion of the message

5. **can_use_tool callback** (~100ms after message_stop)
   - SDK calls our permission callback
   - We send permission request to ACP client
   - Client shows permission dialog to user
   - IMPORTANT: No notifications should be sent while dialog is open!

6. **Tool execution or denial**
   - If allowed: tool runs, emits ToolCallCompleteEvent
   - If denied: SDK receives denial, continues with next turn
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import TYPE_CHECKING, Any, assert_never, cast

from clawd_code_sdk.models import (
    AuthStatusMessage,
    BackgroundTasksChangedMessage,
    CommandLifecycleMessage,
    CommandsChangedMessage,
    ControlRequestProgressMessage,
    ConversationResetMessage,
    ElicitationCompleteMessage,
    FilesPersistedSystemMessage,
    HookProgressSystemMessage,
    HookResponseSystemMessage,
    HookStartedSystemMessage,
    InformationalSystemMessage,
    InitSystemMessage,
    LocalCommandOutputMessage,
    MemoryRecallSystemMessage,
    MirrorErrorSystemMessage,
    ModelRefusalFallbackMessage,
    ModelRefusalNoFallbackMessage,
    NotificationSystemMessage,
    PermissionDeniedSystemMessage,
    PluginInstallSystemMessage,
    PromptSuggestionMessage,
    RateLimitMessage,
    ResultErrorMessage,
    ResultSuccessMessage,
    SessionStateChangedMessage,
    SessionWritesLostMessage,
    StatusSystemMessage,
    TaskNotificationSystemMessage,
    TaskProgressSystemMessage,
    TaskStartedSystemMessage,
    TaskUpdatedSystemMessage,
    ThinkingTokensMessage,
    ToolProgressMessage,
    ToolUseSummaryMessage,
    WorkerShuttingDownMessage,
)
from pydantic_ai import PartEndEvent, TextPart
from pydantic_ai.usage import RequestUsage

from agentpool.agents.claude_code_agent.converters import (
    structured_results,
    to_bash_display,
    tool_result_events,
)
from agentpool.agents.claude_code_agent.notices import RateLimitNotices, message_notice
from agentpool.agents.claude_code_agent.server_blocks import (
    REDACTED_THINKING,
    server_block_events,
    server_tool_start,
)
from agentpool.agents.events import (
    CompactionEvent,
    NoticeEvent,
    PartDeltaEvent,
    PartStartEvent,
    PromptSuggestionEvent,
    RetryEvent,
    StepCompletedEvent,
    StepStartedEvent,
    SubAgentEvent,
    TaskFinishedEvent,
    TaskStartedEvent,
    TaskUpdatedEvent,
    TaskUsage,
    TerminalContentItem,
    ToolCallCompleteEvent,
    ToolCallProgressEvent,
    ToolCallStartEvent,
)
from agentpool.agents.events.infer_info import derive_rich_tool_info
from agentpool.log import get_logger
from agentpool.utils.streams.streamed_response import StreamedResponse
from agentpool.utils.time_utils import get_now


if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterator
    from datetime import datetime

    from anthropic.types.beta import BetaRawContentBlockDelta, BetaUsage
    from clawd_code_sdk import AssistantMessage, Message, ResultMessage, ToolUseBlock, UserMessage
    from clawd_code_sdk.models import ClaudeCodeCommandInfo, StopReason
    from clawd_code_sdk.models.messages import RateLimitInfo
    from pydantic_ai.messages import FinishReason

    from agentpool.agents.events import RichAgentStreamEvent
    from agentpool.agents.events.events import TaskKind, ToolCallContentItem

logger = get_logger(__name__)

_MCP_TOOL_PATTERN = re.compile(r"^mcp__agentpool-(.+)-tools__(.+)$")
"""Pattern to detect CC-provided tool names."""


def _strip_mcp_prefix(tool_name: str) -> str:
    """Strip MCP server prefix from tool names for cleaner UI display."""
    if match := _MCP_TOOL_PATTERN.match(tool_name):
        return match.group(2)
    return tool_name


@dataclass(kw_only=True)
class _Step:
    """The API message (model request) currently streaming."""

    step_id: str
    model_name: str
    usage: RequestUsage
    finish_reason: FinishReason | None = None


def _finish_reason(stop_reason: str | None) -> FinishReason | None:
    """Map an Anthropic stop reason onto pydantic-ai's finish reasons."""
    match stop_reason:
        case "end_turn" | "stop_sequence" | "pause_turn":
            return "stop"
        case "max_tokens" | "model_context_window_exceeded":
            return "length"
        case "tool_use":
            return "tool_call"
        case "refusal":
            return "content_filter"
        case _:
            return None


@dataclass(kw_only=True)
class _Subagent:
    """A Claude Code subagent, identified by the tool call that started it."""

    name: str
    depth: int = 1


@dataclass(kw_only=True)
class _StreamState:
    """Per-stream bookkeeping."""

    pending_tool_calls: dict[str, ToolUseBlock] = field(default_factory=dict)
    """Main-conversation tool calls awaiting their result."""
    streaming_tc_id: str | None = None
    """Tool call whose arguments are currently streaming."""
    step: _Step | None = None
    server_started: set[str] = field(default_factory=set)
    """Server tool calls whose start was emitted from the raw stream."""
    server_calls: dict[str, ToolCallStartEvent] = field(default_factory=dict)
    """Open server / MCP-connector tool calls (main conversation and subagents)."""
    subagents: dict[str, _Subagent] = field(default_factory=dict)
    """Subagents by the ID of the tool call that started them."""
    subagent_calls: dict[str, ToolUseBlock] = field(default_factory=dict)
    """Tool calls made by subagents, awaiting their result."""
    streamed_messages: set[str] = field(default_factory=set)
    """IDs of the assistant messages whose text arrived as stream deltas."""

    def subagent(self, parent_tool_use_id: str, subagent_type: str | None) -> _Subagent:
        """The subagent behind a frame (registered on first sight if task_started was missed)."""
        if (known := self.subagents.get(parent_tool_use_id)) is not None:
            return known
        subagent = _Subagent(name=subagent_type or "subagent")
        self.subagents[parent_tool_use_id] = subagent
        return subagent


def _task_kind(task_type: str | None, agent_type: str | None) -> TaskKind:
    """Kind of a Claude Code task (``task_type``: local_agent, local_bash, mcp_task, ...)."""
    match task_type:
        case "local_agent" | "remote_agent":
            return "agent"
        case "local_bash":
            return "shell"
        case _:
            return "agent" if agent_type else "other"


def _tool_start(tc_id: str, raw_tool_name: str, input_: dict[str, Any]) -> ToolCallStartEvent:
    """Start event for a Claude Code tool call.

    Bash calls get a display-only terminal: Claude Code executes commands itself, so the
    ``_meta`` virtual terminal convention is used instead of the ACP terminal/create RPC.
    """
    rich_info = derive_rich_tool_info(raw_tool_name, input_)
    if raw_tool_name == "Bash":
        tc_content: list[ToolCallContentItem] = [TerminalContentItem(terminal_id=tc_id)]
        meta: dict[str, Any] | None = {"terminal_info": {"terminal_id": tc_id}}
    else:
        tc_content = rich_info.content
        meta = None
    return ToolCallStartEvent(
        tool_call_id=tc_id,
        tool_name=_strip_mcp_prefix(raw_tool_name),
        title=rich_info.title,
        kind=rich_info.kind,
        locations=[],
        content=tc_content,
        raw_input=input_,
        field_meta=meta,
    )


@dataclass(kw_only=True)
class ClaudeCodeStreamedResponse(StreamedResponse):
    """Streamed Claude Code response.

    Message types without a mapping are listed in the final ``case`` of the dispatch and
    are deliberately not surfaced:

    - ``thinking_tokens``: an estimate only; the real usage arrives with the step.
    - ``session_state_changed``, ``files_persisted``, ``plugin_install``, ``hook_started`` /
      ``hook_progress``, ``worker_shutting_down``, ``elicitation_complete``,
      ``conversation_reset``, ``tool_use_summary``, ``auth_status``, ``init``,
      ``memory_recall``, ``control_request_progress`` (except retries): Claude Code internals.
    - ``task_updated`` status patches: the end of a task is reported by
      ``task_notification``. ``background_tasks_changed``: a snapshot of what the task
      events already describe.
    - Subagent ``stream_event`` frames: subagent activity is derived from its complete
      messages (Claude Code does not stream subagents).
    """

    stream: AsyncIterator[Message | RichAgentStreamEvent[Any]]
    agent_name: str
    session_id: str
    _timestamp: datetime = field(default_factory=get_now)
    _model_name: str | None = None
    _result_message: ResultMessage | None = None
    """The SDK ResultMessage captured at end of stream (contains usage, cost, etc.)."""
    on_rate_limit: Callable[[RateLimitInfo], None] | None = None
    rate_limit_notices: RateLimitNotices = field(default_factory=RateLimitNotices)
    """Shows each rate limit state once; pass the agent's to keep that across turns."""
    on_command_lifecycle: Callable[[CommandLifecycleMessage], None] | None = None
    """Called with the queue progress of the messages steered into the run."""
    on_commands_changed: Callable[[list[ClaudeCodeCommandInfo]], None] | None = None
    on_request: Callable[[str, BetaUsage], None] | None = None
    """Called when a main-conversation request starts, with its ID and usage."""
    """Called with the CLI's commands when they change (e.g. MCP prompts once connected)."""
    """Receives the rate limit windows reported during the stream."""

    async def _get_event_iterator(self) -> AsyncIterator[RichAgentStreamEvent[Any]]:  # noqa: PLR0915
        from anthropic.types.beta import (
            BetaMCPToolUseBlock,
            BetaRawContentBlockDeltaEvent as ContentBlockDeltaEvent,
            BetaRawContentBlockStartEvent as ContentBlockStartEvent,
            BetaRawContentBlockStopEvent as ContentBlockStopEvent,
            BetaRawMessageDeltaEvent as MessageDeltaEvent,
            BetaRawMessageStartEvent as MessageStartEvent,
            BetaRawMessageStopEvent as MessageStopEvent,
            BetaRedactedThinkingBlock,
            BetaServerToolUseBlock,
            BetaTextBlock as AnthTextBlock,
            BetaThinkingBlock as AnthThinkingBlock,
            BetaToolUseBlock as AnthToolUseBlock,
        )
        from clawd_code_sdk.models import (
            APIRetrySystemMessage,
            AssistantMessage,
            CompactBoundarySystemMessage,
            MessageUnion,
            ResultMessage,
            StreamEvent,
            UserMessage,
        )

        state = _StreamState()
        # Note: message reconstruction (model_messages, response_parts, text accumulation)
        # is handled by the caller's MessageReconstructor, which observes the yielded events.

        async for event_or_message in self.stream:
            if not isinstance(event_or_message, MessageUnion):
                yield event_or_message
                continue
            message = event_or_message
            match message:
                # Subagent frames: wrap their activity, keep it out of the main conversation
                case AssistantMessage(parent_tool_use_id=str() as parent_id):
                    for e in self._subagent_assistant(state, message, parent_id):
                        yield e
                case UserMessage(parent_tool_use_id=str() as parent_id):
                    for e in self._subagent_user(state, message, parent_id):
                        yield e
                case StreamEvent(parent_tool_use_id=str()):
                    pass

                case AssistantMessage(model=model):
                    if model:
                        self._model_name = model
                    for e in self._assistant_blocks(state, message):
                        yield e

                case UserMessage():
                    for e in self._tool_results(state, message):
                        yield e

                # Real-time streaming: content_block_start
                case StreamEvent(
                    event=ContentBlockStartEvent(index=idx, content_block=content_block)
                ):
                    match content_block:
                        case AnthTextBlock():
                            yield PartStartEvent.text(index=idx, content="")
                        case AnthThinkingBlock():
                            yield PartStartEvent.thinking(index=idx, content="")
                        case BetaRedactedThinkingBlock():
                            yield PartStartEvent.thinking(index=idx, content=REDACTED_THINKING)
                        case AnthToolUseBlock(id=tc_id, name=raw_tool_name, input=input_):
                            state.streaming_tc_id = tc_id
                            yield _tool_start(tc_id, raw_tool_name, input_)
                        case BetaServerToolUseBlock() | BetaMCPToolUseBlock():
                            start = server_tool_start(content_block)
                            state.streaming_tc_id = start.tool_call_id
                            state.server_started.add(start.tool_call_id)
                            yield start
                        case _:
                            # Results and other API blocks: from the complete message
                            pass

                # API message lifecycle of the main conversation = one step each
                case StreamEvent(event=MessageStartEvent(message=api_message)):
                    state.streamed_messages.add(api_message.id)
                    if self.on_request is not None:
                        self.on_request(api_message.id, api_message.usage)
                    usage = api_message.usage
                    state.step = _Step(
                        step_id=api_message.id,
                        model_name=api_message.model,
                        usage=RequestUsage(
                            input_tokens=usage.input_tokens,
                            cache_read_tokens=usage.cache_read_input_tokens or 0,
                            cache_write_tokens=usage.cache_creation_input_tokens or 0,
                            output_tokens=usage.output_tokens,
                        ),
                    )
                    yield StepStartedEvent(
                        step_id=state.step.step_id, model_name=state.step.model_name
                    )
                case StreamEvent(event=MessageDeltaEvent(delta=delta, usage=delta_usage)):
                    if (step := state.step) is not None:
                        step.finish_reason = _finish_reason(delta.stop_reason)
                        step.usage.output_tokens = delta_usage.output_tokens
                        if delta_usage.input_tokens is not None:
                            step.usage.input_tokens = delta_usage.input_tokens
                case StreamEvent(event=MessageStopEvent()):
                    if (step := state.step) is not None:
                        yield StepCompletedEvent(
                            step_id=step.step_id,
                            usage=step.usage,
                            finish_reason=step.finish_reason,
                            model_name=step.model_name,
                        )
                        state.step = None

                # content_block_delta events
                case StreamEvent(event=ContentBlockDeltaEvent(index=index, delta=delta)):
                    for e in handle_delta(
                        index=index, delta=delta, streaming_tc_id=state.streaming_tc_id
                    ):
                        yield e
                # content_block_stop
                case StreamEvent(event=ContentBlockStopEvent(index=index)):
                    state.streaming_tc_id = None
                    yield PartEndEvent(index=index, part=TextPart(content=""))

                case StatusSystemMessage(compact_result="failed", compact_error=error):
                    yield CompactionEvent(session_id=self.session_id, phase="failed", error=error)

                case StatusSystemMessage(status="compacting"):
                    yield CompactionEvent(
                        session_id=self.session_id, trigger="auto", phase="starting"
                    )

                case CompactBoundarySystemMessage(compact_metadata=compact_metadata):
                    yield CompactionEvent(
                        session_id=self.session_id,
                        trigger=compact_metadata["trigger"],
                        phase="completed",
                        pre_tokens=compact_metadata["pre_tokens"],
                    )

                # Messages steered into the run (see ClaudeCodeAgent.steer)
                case CommandLifecycleMessage(state=lifecycle_state):
                    if self.on_command_lifecycle is not None:
                        self.on_command_lifecycle(message)
                    if lifecycle_state in ("discarded", "refused"):
                        yield NoticeEvent(
                            title="Message not delivered",
                            severity="warning",
                            description=f"Claude Code {lifecycle_state} a message sent mid-run.",
                        )

                case CommandsChangedMessage(commands=commands):
                    if self.on_commands_changed is not None:
                        self.on_commands_changed(commands)

                # A local command's output (/context, /cost) is the run's reply
                case LocalCommandOutputMessage(content=content):
                    if content:
                        yield PartStartEvent.text(index=0, content=content)
                        yield PartEndEvent(index=0, part=TextPart(content=content))

                case PromptSuggestionMessage(suggestion=suggestion):
                    yield PromptSuggestionEvent(suggestions=[suggestion])

                case TaskStartedSystemMessage(task_id=task_id, tool_use_id=tc_id):
                    agent_type = message.subagent_type
                    depth = message.spawn_depth or 1
                    if tc_id is not None:
                        name = agent_type or message.task_type or "subagent"
                        state.subagents[tc_id] = _Subagent(name=name, depth=depth)
                    yield TaskStartedEvent(
                        task_id=task_id,
                        kind=_task_kind(message.task_type, agent_type),
                        description=message.description,
                        tool_call_id=tc_id,
                        background=bool(message.is_backgrounded),
                        agent_type=agent_type,
                        parent_task_id=message.parent_task_id,
                        depth=depth,
                    )

                case TaskProgressSystemMessage(task_id=task_id, usage=task_usage):
                    last_tool = message.last_tool_name
                    summary = message.summary or (f"Running {last_tool}" if last_tool else None)
                    yield TaskUpdatedEvent(
                        task_id=task_id,
                        message=summary,
                        last_tool=last_tool,
                        usage=TaskUsage(**task_usage),
                    )
                    if (tc_id := message.tool_use_id) is not None:
                        tool_use = state.pending_tool_calls.get(tc_id)
                        yield ToolCallProgressEvent(
                            tool_call_id=tc_id,
                            tool_name=_strip_mcp_prefix(tool_use.name) if tool_use else None,
                            message=summary,
                            field_meta={"task_usage": dict(task_usage)},
                        )

                case TaskUpdatedSystemMessage(task_id=task_id, patch=patch) if (
                    patch.description is not None or patch.is_backgrounded is not None
                ):
                    yield TaskUpdatedEvent(
                        task_id=task_id,
                        description=patch.description,
                        background=patch.is_backgrounded,
                    )

                case TaskNotificationSystemMessage(task_id=task_id, status=task_status):
                    task_usage = message.usage
                    yield TaskFinishedEvent(
                        task_id=task_id,
                        status="cancelled" if task_status == "stopped" else task_status,
                        summary=message.summary or None,
                        output_ref=message.output_file or None,
                        usage=TaskUsage(**task_usage) if task_usage is not None else None,
                    )

                case APIRetrySystemMessage(attempt=attempt, error=error):
                    yield RetryEvent(
                        attempt=attempt,
                        max_retries=message.max_retries,
                        delay_ms=message.retry_delay_ms,
                        error="no response" if message.no_response else error.replace("_", " "),
                        status_code=message.error_status,
                    )

                case ControlRequestProgressMessage(
                    status="api_retry", attempt=int() as attempt, retry_delay_ms=float() as delay
                ):
                    yield RetryEvent(
                        attempt=attempt,
                        max_retries=message.max_retries,
                        delay_ms=delay,
                        error="request failed",
                        status_code=message.error_status,
                    )

                case (
                    ModelRefusalFallbackMessage()
                    | ModelRefusalNoFallbackMessage()
                    | PermissionDeniedSystemMessage()
                    | NotificationSystemMessage()
                    | InformationalSystemMessage()
                    | SessionWritesLostMessage()
                    | MirrorErrorSystemMessage()
                    | HookResponseSystemMessage()
                    | RateLimitMessage()
                ):
                    if isinstance(message, RateLimitMessage):
                        if self.on_rate_limit is not None:
                            self.on_rate_limit(message.rate_limit_info)
                        notice = self.rate_limit_notices.notice(message.rate_limit_info)
                    else:
                        notice = message_notice(message)
                    if notice is not None:
                        yield notice

                case (
                    StreamEvent()
                    | ResultSuccessMessage()
                    | ResultErrorMessage()
                    | HookStartedSystemMessage()
                    | HookProgressSystemMessage()
                    | AuthStatusMessage()
                    | ToolProgressMessage()
                    | ToolUseSummaryMessage()
                    | InitSystemMessage()
                    | StatusSystemMessage()
                    | TaskStartedSystemMessage()
                    | TaskProgressSystemMessage()
                    | TaskNotificationSystemMessage()
                    | FilesPersistedSystemMessage()
                    | SessionStateChangedMessage()
                    | ElicitationCompleteMessage()
                    | PluginInstallSystemMessage()
                    | MemoryRecallSystemMessage()
                    | TaskUpdatedSystemMessage()
                    | BackgroundTasksChangedMessage()
                    | ControlRequestProgressMessage()
                    | ConversationResetMessage()
                    | ThinkingTokensMessage()
                    | WorkerShuttingDownMessage()
                ):
                    pass
                case _ as unreachable:
                    assert_never(unreachable)

            # Check for result (end of response)
            if isinstance(message, ResultMessage):
                self._result_message = message

    def _assistant_blocks(
        self, state: _StreamState, message: AssistantMessage
    ) -> Iterator[RichAgentStreamEvent[Any]]:
        """Events for the complete blocks of a main-conversation assistant message.

        Text and thinking were already streamed, except for a message that was never
        streamed: a local command's reply (``/context``), which no model wrote.
        Tool calls get their final arguments.
        """
        from clawd_code_sdk.models import TextBlock, ThinkingBlock, ToolUseBlock

        streamed = message.message.id in state.streamed_messages
        for index, block in enumerate(message.content):
            match block:
                case TextBlock(text=text) if not streamed and text:
                    yield PartStartEvent.text(index=index, content=text)
                    yield PartEndEvent(index=index, part=TextPart(content=text))
                case ToolUseBlock(id=tc_id, name=name, input=input_data):
                    state.pending_tool_calls[tc_id] = block
                    # The start was emitted while streaming (with empty args)
                    rich_info = derive_rich_tool_info(name, input_data)
                    yield ToolCallProgressEvent(
                        tool_call_id=tc_id,
                        tool_name=_strip_mcp_prefix(name),
                        title=rich_info.title,
                        tool_input=cast(dict[str, Any], input_data),
                    )
                case ThinkingBlock() | TextBlock():
                    pass  # Already handled via streaming deltas
                case _:
                    yield from server_block_events(
                        block,
                        started=state.server_started,
                        calls=state.server_calls,
                        agent_name=self.agent_name,
                        session_id=self.session_id,
                    )

    def _tool_results(
        self, state: _StreamState, message: UserMessage
    ) -> Iterator[RichAgentStreamEvent[Any]]:
        """Completion events for the tool results of a main-conversation user message."""
        from clawd_code_sdk.models import DocumentBlock, ImageBlock, TextBlock, ToolResultBlock

        if isinstance(message.content, str):
            return
        structured = structured_results(message.tool_use_result)
        result_index = 0
        for user_block in message.content:
            match user_block:
                case ToolResultBlock(tool_use_id=tc_id, is_error=is_error):
                    tool_structured = (
                        structured[result_index] if result_index < len(structured) else None
                    )
                    result_index += 1
                    tool_use = state.pending_tool_calls.pop(tc_id, None)
                    if tool_use is None:
                        # A result for a call this run never started (e.g. one the CLI
                        # closes after an interrupted earlier run): nothing to complete.
                        logger.debug("Tool result without tool call", tool_call_id=tc_id)
                        continue
                    result_content = user_block.get_parsed_content()
                    stripped = _strip_mcp_prefix(tool_use.name)
                    tool_input = cast(dict[str, Any], tool_use.input)
                    if tool_use.name == "Bash":
                        # Output and exit to the display-only terminal before completion
                        text = str(result_content) if result_content else ""
                        display = to_bash_display(text, tool_structured, is_error=bool(is_error))
                        yield ToolCallProgressEvent(
                            tool_call_id=tc_id,
                            tool_name=stripped,
                            field_meta={
                                "terminal_output": {"terminal_id": tc_id, "data": display.output},
                            },
                        )
                        yield ToolCallProgressEvent(
                            tool_call_id=tc_id,
                            tool_name=stripped,
                            field_meta={
                                "terminal_exit": {
                                    "terminal_id": tc_id,
                                    "exit_code": display.exit_code,
                                    "signal": None,
                                },
                            },
                        )
                    # What the structured result shows beyond its text (diffs, the plan)
                    yield from tool_result_events(tc_id, stripped, tool_structured)

                    yield ToolCallCompleteEvent(
                        tool_name=stripped,
                        tool_call_id=tc_id,
                        tool_input=tool_input,
                        tool_result=result_content,
                        agent_name=self.agent_name,
                        message_id="",
                        error=str(result_content) if is_error else None,
                    )
                case ImageBlock() | TextBlock() | DocumentBlock():
                    pass
                case _ as unknown_user_block:
                    assert_never(unknown_user_block)

    def _subagent_assistant(
        self, state: _StreamState, message: AssistantMessage, parent_id: str
    ) -> Iterator[SubAgentEvent]:
        """Wrap a subagent's assistant message: its text, thinking and tool calls.

        Subagents are not streamed, so their complete messages are the only source.
        """
        from clawd_code_sdk.models import TextBlock, ThinkingBlock, ToolUseBlock

        subagent = state.subagent(parent_id, message.subagent_type)
        for index, block in enumerate(message.content):
            inner: list[RichAgentStreamEvent[Any]]
            match block:
                case TextBlock(text=text):
                    inner = [PartStartEvent.text(index=index, content=text)] if text else []
                case ThinkingBlock(thinking=thinking):
                    inner = [PartStartEvent.thinking(index=index, content=thinking)]
                case ToolUseBlock(id=tc_id, name=name, input=input_):
                    state.subagent_calls[tc_id] = block
                    inner = [_tool_start(tc_id, name, cast(dict[str, Any], input_))]
                case _:
                    inner = server_block_events(
                        block,
                        started=state.server_started,
                        calls=state.server_calls,
                        agent_name=subagent.name,
                        session_id=self.session_id,
                    )
            for event in inner:
                yield self._wrap(subagent, parent_id, event)

    def _subagent_user(
        self, state: _StreamState, message: UserMessage, parent_id: str
    ) -> Iterator[SubAgentEvent]:
        """Wrap the tool results of a subagent (its prompt text is not repeated)."""
        from clawd_code_sdk.models import DocumentBlock, ImageBlock, TextBlock, ToolResultBlock

        if isinstance(message.content, str):
            return
        subagent = state.subagent(parent_id, message.subagent_type)
        for block in message.content:
            match block:
                case ToolResultBlock(tool_use_id=tc_id, is_error=is_error):
                    tool_use = state.subagent_calls.pop(tc_id, None)
                    result = block.get_parsed_content()
                    complete = ToolCallCompleteEvent(
                        tool_name=_strip_mcp_prefix(tool_use.name) if tool_use else "unknown",
                        tool_call_id=tc_id,
                        tool_input=cast(dict[str, Any], tool_use.input) if tool_use else {},
                        tool_result=result,
                        agent_name=subagent.name,
                        message_id="",
                        error=str(result) if is_error else None,
                    )
                    yield self._wrap(subagent, parent_id, complete)
                case ImageBlock() | TextBlock() | DocumentBlock():
                    pass
                case _ as unreachable:
                    assert_never(unreachable)

    @staticmethod
    def _wrap(
        subagent: _Subagent, parent_id: str, event: RichAgentStreamEvent[Any]
    ) -> SubAgentEvent:
        return SubAgentEvent(
            source_name=subagent.name,
            source_type="agent",
            event=event,
            depth=subagent.depth,
            parent_tool_call_id=parent_id,
        )

    @property
    def model_name(self) -> str:
        """The model that replied ("" for runs without a reply, e.g. ``/clear``)."""
        return self._model_name or ""

    @property
    def timestamp(self) -> datetime:
        """Get the timestamp of the response."""
        return self._timestamp

    @property
    def stop_reason(self) -> StopReason | None:
        """Extract stop reason from result message."""
        return self._result_message.stop_reason if self._result_message else None


def handle_delta(
    index: int,
    delta: BetaRawContentBlockDelta,
    streaming_tc_id: str | None,
) -> Iterator[PartDeltaEvent]:
    from anthropic.types.beta import (
        BetaCitationsDelta as CitationsDelta,
        BetaCompactionContentBlockDelta as CompactionContentBlockDelta,
        BetaInputJSONDelta as InputJSONDelta,
        BetaSignatureDelta as SignatureDelta,
        BetaTextDelta as TextDelta,
        BetaThinkingDelta as ThinkingDelta,
    )

    match delta:
        case TextDelta(text=text):
            yield PartDeltaEvent.text(index=index, content=text)
        case ThinkingDelta(thinking=thinking):
            yield PartDeltaEvent.thinking(index=index, content=thinking)
        case InputJSONDelta(partial_json=json_) if json_ and streaming_tc_id:
            yield PartDeltaEvent.tool_call(index, content=json_, tool_call_id=streaming_tc_id)
        case CitationsDelta() | SignatureDelta() | InputJSONDelta() | CompactionContentBlockDelta():
            pass
        case _ as unreachable:
            assert_never(unreachable)
