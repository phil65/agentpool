"""Message related models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from agentpool.utils import identifiers as identifier
from opencode_sdk.models.base import OpenCodeBaseModel
from opencode_sdk.models.common import (
    APIError,
    APIErrorData,
    FileDiff,
    ModelRef,
    TextSpan,
    TimeCreated,
    Tokens,
)
from opencode_sdk.models.inputs import PartInput  # noqa: TC001
from opencode_sdk.models.parts import (
    AgentPart,
    FilePart,
    FilePartSource,
    Part,
    RetryPart,
    StepFinishPart,
    StepStartPart,
    SubtaskPart,
    TextPart,
    TimeStartEndOptional,
    ToolPart,
    ToolState,
)


FinishReason = Literal["stop", "length", "content-filter", "tool-calls", "error", "unknown"]


class MessageSummary(OpenCodeBaseModel):
    """Summary information for a message."""

    title: str | None = None
    body: str | None = None
    diffs: list[FileDiff] = Field(default_factory=list)


class MessagePath(OpenCodeBaseModel):
    """Path context for a message."""

    cwd: str
    root: str


class MessageTime(OpenCodeBaseModel):
    """Time information for a message (milliseconds)."""

    created: int
    completed: int | None = None


class OutputFormatText(OpenCodeBaseModel):
    """Text output format."""

    type: Literal["text"] = Field(default="text", init=False)


class OutputFormatJsonSchema(OpenCodeBaseModel):
    """JSON schema output format."""

    type: Literal["json_schema"] = Field(default="json_schema", init=False)
    schema_: dict[str, Any] = Field(alias="schema")
    retry_count: int = 2


OutputFormat = OutputFormatText | OutputFormatJsonSchema


class BaseMessage(OpenCodeBaseModel):
    """Base class for messages."""

    id: str
    session_id: str
    agent: str = "default"
    variant: str | None = None


class UserMessage(BaseMessage):
    """User message."""

    role: Literal["user"] = "user"
    time: TimeCreated
    model: ModelRef
    format: OutputFormat | None = None
    summary: MessageSummary | None = None
    system: str | None = None
    tools: dict[str, bool] | None = None


class AssistantMessage(BaseMessage):
    """Assistant message."""

    role: Literal["assistant"] = "assistant"
    parent_id: str  # Required - links to user message
    model_id: str
    provider_id: str
    mode: str = "default"
    path: MessagePath
    time: MessageTime
    tokens: Tokens = Field(default_factory=Tokens)
    """Context window usage from the latest step.

    Replaced (not accumulated) on each step. The TUI shows this from the
    last assistant message as the session "Context" indicator.
    """
    cost: float = 0.0
    """Per-message cost in USD.

    The TUI sums this across all assistant messages for the session total,
    so this must be per-message, not cumulative.
    """
    error: MessageError | None = None
    summary: bool | None = None
    # Known values from AI SDK's LanguageModelV2FinishReason; schema allows any string
    finish: FinishReason | str | None = None
    structured: Any | None = None


# --- Assistant message error types ---


class ProviderAuthErrorData(OpenCodeBaseModel):
    """Data for provider authentication errors."""

    provider_id: str
    message: str


class ProviderAuthError(OpenCodeBaseModel):
    """Provider authentication error."""

    name: Literal["ProviderAuthError"] = Field(default="ProviderAuthError", init=False)
    data: ProviderAuthErrorData


class UnknownErrorData(OpenCodeBaseModel):
    """Data for unknown errors."""

    message: str


class UnknownError(OpenCodeBaseModel):
    """Unknown error."""

    name: Literal["UnknownError"] = Field(default="UnknownError", init=False)
    data: UnknownErrorData


class MessageOutputLengthErrorData(OpenCodeBaseModel):
    """Data for output length errors (empty)."""


class MessageOutputLengthError(OpenCodeBaseModel):
    """Message output length exceeded error."""

    name: Literal["MessageOutputLengthError"] = Field(
        default="MessageOutputLengthError", init=False
    )
    data: MessageOutputLengthErrorData = Field(default_factory=MessageOutputLengthErrorData)


class MessageAbortedErrorData(OpenCodeBaseModel):
    """Data for aborted message errors."""

    message: str


class MessageAbortedError(OpenCodeBaseModel):
    """Message was aborted."""

    name: Literal["MessageAbortedError"] = Field(default="MessageAbortedError", init=False)
    data: MessageAbortedErrorData


class StructuredOutputErrorData(OpenCodeBaseModel):
    """Data for structured output errors."""

    message: str
    retries: int


class StructuredOutputError(OpenCodeBaseModel):
    """Structured output validation error."""

    name: Literal["StructuredOutputError"] = Field(default="StructuredOutputError", init=False)
    data: StructuredOutputErrorData


class ContextOverflowErrorData(OpenCodeBaseModel):
    """Data for context overflow errors."""

    message: str
    response_body: str | None = None


class ContextOverflowError(OpenCodeBaseModel):
    """Context window overflow error."""

    name: Literal["ContextOverflowError"] = Field(default="ContextOverflowError", init=False)
    data: ContextOverflowErrorData


MessageError = (
    ProviderAuthError
    | UnknownError
    | MessageOutputLengthError
    | MessageAbortedError
    | APIError
    | StructuredOutputError
    | ContextOverflowError
)


MessageInfo = UserMessage | AssistantMessage


class MessageWithParts[InfoT: MessageInfo = MessageInfo](OpenCodeBaseModel):
    """Message with its parts, generic over the info type."""

    info: InfoT
    parts: list[Part] = Field(default_factory=list)

    @classmethod
    def user(
        cls,
        message_id: str,
        session_id: str,
        time: TimeCreated,
        agent_name: str,
        model: ModelRef,
    ) -> MessageWithParts[UserMessage]:
        user_msg = UserMessage(
            id=message_id,
            session_id=session_id,
            time=time,
            agent=agent_name,
            model=model,
        )
        return MessageWithParts(info=user_msg)

    @classmethod
    def assistant(
        cls,
        message_id: str,
        session_id: str,
        time: MessageTime,
        agent_name: str,
        model_id: str,
        parent_id: str,
        provider_id: str,
        path: MessagePath,
        mode: str = "default",
        cost: float = 0.0,
        summary: bool | None = None,
        finish: FinishReason | str | None = None,
        error: MessageError | None = None,
        tokens: Tokens | None = None,
    ) -> MessageWithParts[AssistantMessage]:
        user_msg = AssistantMessage(
            id=message_id,
            session_id=session_id,
            time=time,
            agent=agent_name,
            model_id=model_id,
            parent_id=parent_id,
            provider_id=provider_id,
            path=path,
            mode=mode,
            cost=cost,
            error=error,
            summary=summary,
            finish=finish,
            tokens=tokens or Tokens(),
        )
        return MessageWithParts(info=user_msg)

    def update_part(self, updated: Part) -> None:
        """Replace a part in the assistant message's parts list by ID."""
        for i, p in enumerate(self.parts):
            if isinstance(p, type(updated)) and p.id == updated.id:
                self.parts[i] = updated
                break

    def add_text_part(
        self,
        text: str,
        synthetic: bool | None = None,
        ignored: bool | None = None,
        time: TimeStartEndOptional | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TextPart:
        """Create and append a text part."""
        part = TextPart(
            id=identifier.ascending("part"),
            message_id=self.info.id,
            session_id=self.info.session_id,
            text=text,
            synthetic=synthetic,
            ignored=ignored,
            time=time,
            metadata=metadata,
        )
        self.parts.append(part)
        return part

    def add_file_part(
        self,
        mime: str,
        url: str,
        filename: str | None = None,
        source: FilePartSource | None = None,
    ) -> FilePart:
        """Create and append a file part."""
        part = FilePart(
            id=identifier.ascending("part"),
            message_id=self.info.id,
            session_id=self.info.session_id,
            mime=mime,
            url=url,
            filename=filename,
            source=source,
        )
        self.parts.append(part)
        return part

    def add_agent_part(self, name: str, source: TextSpan | None = None) -> AgentPart:
        """Create and append an agent mention part."""
        part = AgentPart(
            id=identifier.ascending("part"),
            message_id=self.info.id,
            session_id=self.info.session_id,
            name=name,
            source=source,
        )
        self.parts.append(part)
        return part

    def add_subtask_part(
        self,
        prompt: str,
        description: str,
        agent: str,
        model: ModelRef | None = None,
    ) -> SubtaskPart:
        """Create and append a subtask part."""
        part = SubtaskPart(
            id=identifier.ascending("part"),
            message_id=self.info.id,
            session_id=self.info.session_id,
            prompt=prompt,
            description=description,
            agent=agent,
            model=model,
        )
        self.parts.append(part)
        return part

    def add_step_start_part(self, snapshot: str | None = None) -> StepStartPart:
        """Create and append a step start marker."""
        part = StepStartPart(
            id=identifier.ascending("part"),
            message_id=self.info.id,
            session_id=self.info.session_id,
            snapshot=snapshot,
        )
        self.parts.append(part)
        return part

    def add_step_finish_part(
        self,
        reason: str = "stop",
        cost: float = 0.0,
        tokens: Tokens | None = None,
        snapshot: str | None = None,
    ) -> StepFinishPart:
        """Create and append a step finish marker."""
        part = StepFinishPart(
            id=identifier.ascending("part"),
            message_id=self.info.id,
            session_id=self.info.session_id,
            reason=reason,
            cost=cost,
            tokens=tokens or Tokens(),
            snapshot=snapshot,
        )
        self.parts.append(part)
        return part

    def add_tool_part(self, tool: str, call_id: str, state: ToolState) -> ToolPart:
        """Create and append a tool call part."""
        part = ToolPart(
            id=identifier.ascending("part"),
            message_id=self.info.id,
            session_id=self.info.session_id,
            tool=tool,
            call_id=call_id,
            state=state,
        )
        self.parts.append(part)
        return part

    def add_retry_part(
        self,
        attempt: int,
        message: str,
        created: int,
        is_retryable: bool = True,
        metadata: dict[str, str] | None = None,
    ) -> RetryPart:
        """Create and append a retry part."""
        error = APIErrorData(message=message, is_retryable=is_retryable, metadata=metadata)
        part = RetryPart(
            id=identifier.ascending("part"),
            message_id=self.info.id,
            session_id=self.info.session_id,
            attempt=attempt,
            error=APIError(data=error),
            time=TimeCreated(created=created),
        )
        self.parts.append(part)
        return part


class MessageRequest(OpenCodeBaseModel):
    """Request body for sending a message."""

    parts: list[PartInput]
    message_id: str | None = None
    model: ModelRef | None = None
    agent: str | None = None
    no_reply: bool | None = None
    system: str | None = None
    tools: dict[str, bool] | None = None
    variant: str | None = None
    """Reasoning/thinking variant for this message.

    Maps to the model's variants (e.g., 'low', 'medium', 'high', 'max').
    When set, the agent will use this thinking effort level for the response.
    """


class ShellRequest(OpenCodeBaseModel):
    """Request body for running a shell command."""

    agent: str
    command: str
    model: ModelRef | None = None


class CommandRequest(OpenCodeBaseModel):
    """Request body for executing a slash command."""

    command: str
    arguments: str | None = None
    agent: str | None = None
    model: str | None = None  # Format: "providerID/modelID"
    message_id: str | None = None


# Type unions

AnyMessageWithParts = MessageWithParts[UserMessage] | MessageWithParts[AssistantMessage]
