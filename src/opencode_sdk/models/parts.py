"""Message part models."""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import Field

from agentpool.utils.time_utils import now_ms
from opencode_sdk.models.base import OpenCodeBaseModel
from opencode_sdk.models.common import (
    APIError,
    ModelRef,
    TextSpan,
    TimeCreated,
    Tokens,
)


class TimeStart(OpenCodeBaseModel):
    """Time with only start (milliseconds).

    Used by: ToolStateRunning
    """

    start: int

    @classmethod
    def now(cls) -> Self:
        return cls(start=now_ms())


class TimeStartEnd(OpenCodeBaseModel):
    """Time with start and end, both required (milliseconds).

    Used by: ToolStateError
    """

    start: int
    end: int


class TimeStartEndOptional(OpenCodeBaseModel):
    """Time with start required and end optional (milliseconds).

    Used by: TextPart
    """

    start: int
    end: int | None = None

    @classmethod
    def now(cls) -> Self:
        return cls(start=now_ms())


class TimeStartEndCompacted(OpenCodeBaseModel):
    """Time with start, end required, and optional compacted (milliseconds).

    Used by: ToolStateCompleted
    """

    start: int
    end: int
    compacted: int | None = None


class PartBase(OpenCodeBaseModel):
    """Base fields shared by all message parts."""

    id: str
    message_id: str
    session_id: str


class TextPart(PartBase):
    """Text content part."""

    type: Literal["text"] = Field(default="text", init=False)
    text: str
    synthetic: bool | None = None
    ignored: bool | None = None
    time: TimeStartEndOptional | None = None
    metadata: dict[str, Any] | None = None


class ToolStatePending(OpenCodeBaseModel):
    """Pending tool state."""

    status: Literal["pending"] = Field(default="pending", init=False)
    input: dict[str, Any] = Field(default_factory=dict)
    raw: str = ""


class ToolStateRunning(OpenCodeBaseModel):
    """Running tool state."""

    status: Literal["running"] = Field(default="running", init=False)
    time: TimeStart
    input: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] | None = None
    title: str | None = None


class ToolStateCompleted(OpenCodeBaseModel):
    """Completed tool state."""

    status: Literal["completed"] = Field(default="completed", init=False)
    input: dict[str, Any] = Field(default_factory=dict)
    output: str = ""
    title: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    time: TimeStartEndCompacted
    attachments: list[FilePart] | None = None


class ToolStateError(OpenCodeBaseModel):
    """Error tool state."""

    status: Literal["error"] = Field(default="error", init=False)
    input: dict[str, Any] = Field(default_factory=dict)
    error: str = ""
    metadata: dict[str, Any] | None = None
    time: TimeStartEnd


ToolState = ToolStatePending | ToolStateRunning | ToolStateCompleted | ToolStateError


class ToolPart(PartBase):
    """Tool call part."""

    type: Literal["tool"] = Field(default="tool", init=False)
    call_id: str
    tool: str
    state: ToolState
    metadata: dict[str, Any] | None = None


class LSPPosition(OpenCodeBaseModel):
    """LSP position in a text document."""

    line: int
    character: int


class LSPRange(OpenCodeBaseModel):
    """LSP range in a text document."""

    start: LSPPosition
    end: LSPPosition


class FileSource(OpenCodeBaseModel):
    """File source - references a file path."""

    text: TextSpan
    type: Literal["file"] = Field(default="file", init=False)
    path: str

    @classmethod
    def create(cls, value: str, start: int, end: int, path: str) -> FileSource:
        return cls(text=TextSpan(value=value, start=start, end=end), path=path)


class SymbolSource(OpenCodeBaseModel):
    """Symbol source - references an LSP symbol."""

    text: TextSpan
    type: Literal["symbol"] = Field(default="symbol", init=False)
    path: str
    range: LSPRange
    name: str
    kind: int


class ResourceSource(OpenCodeBaseModel):
    """Resource source - references an MCP resource."""

    text: TextSpan
    type: Literal["resource"] = Field(default="resource", init=False)
    client_name: str
    uri: str


FilePartSource = FileSource | SymbolSource | ResourceSource


class FilePart(PartBase):
    """File content part."""

    type: Literal["file"] = Field(default="file", init=False)
    mime: str
    filename: str | None = None
    url: str
    source: FilePartSource | None = None


class AgentPart(PartBase):
    """Agent mention part - references a sub-agent to delegate to.

    When a user types @agent-name in the prompt, this part is created.
    The server should inject a synthetic instruction to call the task tool
    with the specified agent.
    """

    type: Literal["agent"] = Field(default="agent", init=False)
    name: str
    """Name of the agent to delegate to."""
    source: TextSpan | None = None
    """Source location in the original prompt text."""


class SnapshotPart(PartBase):
    """File system snapshot reference."""

    type: Literal["snapshot"] = Field(default="snapshot", init=False)
    snapshot: str
    """Snapshot identifier."""


class PatchPart(PartBase):
    """Diff/patch content part."""

    type: Literal["patch"] = Field(default="patch", init=False)
    hash: str
    """Hash of the patch."""
    files: list[str] = Field(default_factory=list)
    """List of files affected by this patch."""


class ReasoningPart(PartBase):
    """Extended thinking/reasoning content part.

    Used for models that support extended thinking (e.g., Claude with thinking tokens).
    """

    type: Literal["reasoning"] = Field(default="reasoning", init=False)
    text: str
    """The reasoning/thinking content."""
    metadata: dict[str, Any] | None = None
    time: TimeStartEndOptional


class CompactionPart(PartBase):
    """Marks where conversation was compacted/summarized."""

    type: Literal["compaction"] = Field(default="compaction", init=False)
    auto: bool = False
    """Whether this was an automatic compaction."""
    overflow: bool | None = None
    """Whether this compaction was triggered by context overflow."""


class SubtaskPart(PartBase):
    """References a spawned subtask."""

    type: Literal["subtask"] = Field(default="subtask", init=False)
    prompt: str
    """The prompt for the subtask."""
    description: str
    """Description of what the subtask does."""
    agent: str
    """The agent handling this subtask."""
    command: str | None = None
    """Optional command associated with the subtask."""
    model: ModelRef | None = None
    """The model used for the subtask."""


class RetryPart(PartBase):
    """Marks a retry of a failed operation."""

    type: Literal["retry"] = Field(default="retry", init=False)
    attempt: int
    """Which retry attempt this is."""
    error: APIError
    """Error information from the failed attempt."""
    time: TimeCreated


class StepStartPart(PartBase):
    """Step start marker."""

    type: Literal["step-start"] = Field(default="step-start", init=False)
    snapshot: str | None = None


class StepFinishPart(PartBase):
    """Step finish marker."""

    type: Literal["step-finish"] = Field(default="step-finish", init=False)
    reason: str = "stop"
    snapshot: str | None = None
    cost: float = 0.0
    tokens: Tokens = Field(default_factory=Tokens)


Part = (
    TextPart
    | ToolPart
    | FilePart
    | AgentPart
    | SnapshotPart
    | PatchPart
    | ReasoningPart
    | CompactionPart
    | SubtaskPart
    | RetryPart
    | StepStartPart
    | StepFinishPart
)
