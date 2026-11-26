"""Zed IDE storage format models."""

import io
import sys
from typing import Annotated, Any, Literal

from aclientp.schema.content_blocks import ContentBlock
import anyenv
from pydantic import AliasChoices, BaseModel, BeforeValidator, ConfigDict, Field
from pydantic_ai import RunUsage


IS_DEV = "pytest" in sys.modules


class ZedBaseModel(BaseModel):
    """Base model with Zed storage."""

    model_config = ConfigDict(
        use_attribute_docstrings=True,
        extra="forbid" if IS_DEV else "ignore",
    )


class ZedFileMention(ZedBaseModel):
    """File mention."""

    abs_path: str


class ZedDirectoryMention(ZedBaseModel):
    """Directory mention."""

    abs_path: str


class ZedLineRange(ZedBaseModel):
    """Inclusive line range (0-based, matching Rust's RangeInclusive<u32>)."""

    start: int
    end: int


class ZedSymbolMention(ZedBaseModel):
    """Symbol mention with location."""

    abs_path: str
    name: str
    line_range: ZedLineRange


class ZedSelectionMention(ZedBaseModel):
    """Selection mention with optional path and line range."""

    abs_path: str | None = None
    line_range: ZedLineRange


class ZedThreadMention(ZedBaseModel):
    """Thread mention."""

    id: str
    name: str


class ZedTextThreadMention(ZedBaseModel):
    """Text thread mention."""

    path: str
    name: str


class ZedRuleMention(ZedBaseModel):
    """Rule mention."""

    id: str
    name: str


class ZedDiagnosticsMention(ZedBaseModel):
    """Diagnostics mention."""

    include_errors: bool = True
    include_warnings: bool = False


class ZedFetchMention(ZedBaseModel):
    """Fetch (URL) mention."""

    url: str


class ZedTerminalSelectionMention(ZedBaseModel):
    """Terminal selection mention."""

    line_count: int = 0


class ZedGitDiffMention(ZedBaseModel):
    """Git diff mention."""

    base_ref: str


class ZedMergeConflictMention(ZedBaseModel):
    """Merge conflict mention."""

    file_path: str


class ZedMentionUri(ZedBaseModel):
    """Mention URI - externally tagged enum matching Rust's MentionUri."""

    File: ZedFileMention | None = None
    Directory: ZedDirectoryMention | None = None
    Symbol: ZedSymbolMention | None = None
    Selection: ZedSelectionMention | None = None
    Thread: ZedThreadMention | None = None
    TextThread: ZedTextThreadMention | None = None
    Rule: ZedRuleMention | None = None
    Fetch: ZedFetchMention | None = None
    PastedImage: bool | None = None
    Diagnostics: ZedDiagnosticsMention | None = None
    TerminalSelection: ZedTerminalSelectionMention | None = None
    GitDiff: ZedGitDiffMention | None = None
    MergeConflict: ZedMergeConflictMention | None = None


class ZedMention(ZedBaseModel):
    """A file/symbol mention in Zed."""

    uri: ZedMentionUri
    content: str

    def formatted(self) -> str:  # noqa: PLR0911
        """Return a formatted string representation of the mention."""
        if self.uri.File:
            return f"[File: {self.uri.File.abs_path}]\n{self.content}"
        if self.uri.Directory:
            return f"[Directory: {self.uri.Directory.abs_path}]\n{self.content}"
        if self.uri.Symbol:
            return f"[Symbol: {self.uri.Symbol.name} in {self.uri.Symbol.abs_path}]\n{self.content}"
        if self.uri.Selection:
            return f"[Selection: {self.uri.Selection.abs_path or ''}]\n{self.content}"
        if self.uri.Fetch:
            return f"[Fetched: {self.uri.Fetch.url}]\n{self.content}"
        if self.uri.Thread:
            return f"[Thread: {self.uri.Thread.name}]\n{self.content}"
        if self.uri.TextThread:
            return f"[TextThread: {self.uri.TextThread.name}]\n{self.content}"
        if self.uri.Rule:
            return f"[Rule: {self.uri.Rule.name}]\n{self.content}"
        if self.uri.Diagnostics:
            return f"[Diagnostics]\n{self.content}"
        if self.uri.TerminalSelection:
            return f"[Terminal Selection]\n{self.content}"
        if self.uri.GitDiff:
            return f"[Git Diff: {self.uri.GitDiff.base_ref}]\n{self.content}"
        if self.uri.MergeConflict:
            return f"[Merge Conflict: {self.uri.MergeConflict.file_path}]\n{self.content}"
        if self.uri.PastedImage:
            return f"[Pasted Image]\n{self.content}"
        return self.content


class ZedImageSize(ZedBaseModel):
    """Image dimensions in device pixels."""

    width: int
    height: int


class ZedImage(ZedBaseModel):
    """An image in Zed (base64 encoded)."""

    source: str
    size: ZedImageSize | None = None


class ZedThinking(ZedBaseModel):
    """Thinking block from model."""

    text: str
    signature: str | None = None


class ZedToolUse(ZedBaseModel):
    """Tool use block."""

    id: str
    name: str
    raw_input: str
    input: dict[str, Any]
    is_input_complete: bool = True
    thought_signature: str | None = None


class ZedToolResultContent(ZedBaseModel):
    """Tool result content - externally tagged enum (Text or Image)."""

    Text: str | None = None
    Image: ZedImage | None = None


def _content_items(value: object) -> object:
    """Zed's tool result content as a list of items.

    Zed keeps a list since September 2026 (``Vec<LanguageModelToolResultContent>``), one
    item before; it reads both, and an item may be a plain string.
    """
    match value:
        case None:
            return []
        case list():
            return [{"Text": item} if isinstance(item, str) else item for item in value]
        case str():
            return [{"Text": value}]
        case _:
            return [value]


# Typed tool outputs


class ZedEditFileOutput(ZedBaseModel):
    """Output from edit_file tool."""

    input_path: str
    old_text: str = ""
    new_text: str = ""
    diff: str = ""
    edit_agent_output: str | None = None


class ZedEditFileOutputLegacy(ZedBaseModel):
    """Legacy output from edit_file tool (older format)."""

    original_path: str
    old_text: str = ""
    new_text: str = ""
    raw_output: str = ""


class ZedFindPathOutput(ZedBaseModel):
    """Output from find_path tool."""

    all_matches_len: int
    current_matches_page: list[str] = Field(default_factory=list)
    offset: int = 0


class ZedWebSearchResult(ZedBaseModel):
    """A single web search result."""

    title: str = ""
    url: str = ""
    snippet: str = ""


class ZedWebSearchOutput(ZedBaseModel):
    """Output from web_search tool."""

    results: list[ZedWebSearchResult] = Field(default_factory=list)


ZedToolOutput = (
    ZedEditFileOutput
    | ZedEditFileOutputLegacy
    | ZedFindPathOutput
    | ZedWebSearchOutput
    | ZedToolResultContent
    | dict[str, Any]
    | str
    | None
)


class ZedToolResult(ZedBaseModel):
    """Tool result."""

    tool_use_id: str
    tool_name: str
    is_error: bool = False
    content: Annotated[list[ZedToolResultContent], BeforeValidator(_content_items)] = Field(
        default_factory=list
    )
    """What the model saw (text and images)."""
    output: ZedToolOutput = None
    """The tool's raw output, kept for display and replay."""

    @property
    def text(self) -> str:
        """The text parts of :attr:`content`, joined."""
        return "".join(item.Text for item in self.content if item.Text is not None)


# User message content blocks (v0.2.0+)


class ZedTextContent(ZedBaseModel):
    """Text content block."""

    Text: str


class ZedImageContent(ZedBaseModel):
    """Image content block."""

    Image: ZedImage


class ZedMentionContent(ZedBaseModel):
    """Mention content block."""

    Mention: ZedMention


ZedUserContent = ZedTextContent | ZedImageContent | ZedMentionContent


# Agent message content blocks (v0.2.0+)


class ZedTextBlock(ZedBaseModel):
    """Text block in agent message."""

    Text: str


class ZedThinkingBlock(ZedBaseModel):
    """Thinking block in agent message."""

    Thinking: ZedThinking


class ZedRedactedThinkingBlock(ZedBaseModel):
    """Redacted thinking block in agent message."""

    RedactedThinking: str


class ZedToolUseBlock(ZedBaseModel):
    """Tool use block in agent message."""

    ToolUse: ZedToolUse


ZedAgentContent = ZedTextBlock | ZedThinkingBlock | ZedRedactedThinkingBlock | ZedToolUseBlock


# v0.2.0+ nested message format


class ZedUserMessage(ZedBaseModel):
    """User message in Zed thread (v0.2.0+ format)."""

    id: str
    content: list[ZedUserContent]


class ZedAgentMessage(ZedBaseModel):
    """Agent message in Zed thread (v0.2.0+ format)."""

    content: list[ZedAgentContent]
    tool_results: dict[str, ZedToolResult] = Field(default_factory=dict)
    reasoning_details: Any | None = None


class ZedNestedMessage(ZedBaseModel):
    """A message in Zed thread v0.2.0+ - nested under User or Agent key."""

    User: ZedUserMessage | None = Field(default=None)
    Agent: ZedAgentMessage | None = Field(default=None)


# Flat message format (v0.1.0, v0.2.0)


class ZedCrease(ZedBaseModel):
    """A foldable region in the assistant panel."""

    start: int
    end: int
    icon_path: str = ""
    label: str = ""


class ZedTextSegment(ZedBaseModel):
    """A segment in a flat message."""

    type: Literal["text"]
    text: str


class ZedThinkingSegment(ZedBaseModel):
    """A segment in a flat message."""

    type: Literal["thinking"]
    signature: str


class ZedRedactedThinkingSegment(ZedBaseModel):
    """A segment in a flat message."""

    type: Literal["RedactedThinking"] = "RedactedThinking"
    data: str


ZedSegment = Annotated[
    ZedTextSegment | ZedThinkingSegment | ZedRedactedThinkingSegment, Field(discriminator="type")
]


class ZedFlatToolUse(ZedBaseModel):
    """Tool use in flat/legacy message format."""

    id: str
    name: str
    input: dict[str, Any]


class ZedFlatToolResult(ZedBaseModel):
    """Tool result in flat/legacy message format."""

    tool_use_id: str
    is_error: bool = False
    content: dict[str, Any] | str | None = None
    output: ZedToolOutput = None


class ZedFlatMessage(ZedBaseModel):
    """A message in flat format (used in v0.1.0 and v0.2.0)."""

    id: int
    role: Literal["user", "assistant"]
    segments: list[ZedSegment] = Field(default_factory=list)
    tool_uses: list[ZedFlatToolUse] = Field(default_factory=list)
    tool_results: list[ZedFlatToolResult] = Field(default_factory=list)
    context: str = ""
    creases: list[ZedCrease] = Field(default_factory=list)
    is_hidden: bool = False

    @property
    def text_segments(self) -> list[ZedTextSegment]:
        return [segment for segment in self.segments if isinstance(segment, ZedTextSegment)]


# Union of all message formats - put ZedFlatMessage first since it's more specific
# (has required 'id' and 'role' fields that ZedNestedMessage doesn't have)
ZedMessage = ZedFlatMessage | ZedNestedMessage


class ZedLanguageModel(ZedBaseModel):
    """Model configuration."""

    provider: str
    model: str


class ZedTokenUsage(ZedBaseModel):
    """Token usage for a request or cumulative."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    def to_run_usage(self) -> RunUsage:
        return RunUsage(
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            cache_write_tokens=self.cache_creation_input_tokens,
            cache_read_tokens=self.cache_read_input_tokens,
        )


class ZedGitState(ZedBaseModel):
    """Git state for a worktree."""

    remote_url: str | None = None
    head_sha: str | None = None
    current_branch: str | None = None
    diff: str | None = None


class ZedWorktreeSnapshot(ZedBaseModel):
    """Git worktree snapshot."""

    worktree_path: str
    git_state: ZedGitState | None = None


class ZedProjectSnapshot(ZedBaseModel):
    """Project snapshot with git state."""

    worktree_snapshots: list[ZedWorktreeSnapshot] = Field(default_factory=list)
    timestamp: str


class ZedSubagentContext(ZedBaseModel):
    """Context passed to a subagent thread for lifecycle management."""

    parent_thread_id: str
    depth: int


class ZedScrollPosition(ZedBaseModel):
    """Serialized scroll position in the UI."""

    item_ix: int
    offset_in_item: float


class ZedThread(ZedBaseModel):
    """A Zed conversation thread."""

    model_config = ConfigDict(populate_by_name=True)

    # v0.3.0 uses "title", v0.2.0 uses "summary"
    title: str = Field(alias="title", validation_alias=AliasChoices("title", "summary"))
    messages: list[ZedMessage | Literal["Resume"]]  # Control messages
    updated_at: str
    version: str | None = None
    detailed_summary: str | None = None  # v0.3.0 field
    detailed_summary_state: str | dict[str, Any] | None = None  # v0.2.0 field
    initial_project_snapshot: ZedProjectSnapshot | None = None
    cumulative_token_usage: ZedTokenUsage = Field(default_factory=ZedTokenUsage)
    # v0.2.0: list of token usage per request
    # v0.3.0+: dict keyed by message ID
    request_token_usage: list[ZedTokenUsage] | dict[str, ZedTokenUsage] = Field(
        default_factory=list
    )
    model: ZedLanguageModel | None = None
    profile: str | None = None
    tool_use_limit_reached: bool = False
    imported: bool = False
    subagent_context: ZedSubagentContext | None = None
    speed: Literal["standard", "fast"] | None = None
    thinking_enabled: bool = False
    thinking_effort: str | None = None
    draft_prompt: list[ContentBlock] | None = None
    ui_scroll_position: ZedScrollPosition | None = None

    @classmethod
    def from_compressed(cls, data: bytes, data_type: Literal["zstd", "plain"]) -> ZedThread:
        """Decompress and parse thread data.

        Args:
            data: Compressed thread data
            data_type: Type of compression ("zstd" or "plain")

        Returns:
            Parsed ZedThread object
        """
        import zstandard

        if data_type == "zstd":
            dctx = zstandard.ZstdDecompressor()
            reader = dctx.stream_reader(io.BytesIO(data))
            json_data = reader.read()
        else:
            json_data = data

        thread_dict = anyenv.load_json(json_data)
        return cls.model_validate(thread_dict)
