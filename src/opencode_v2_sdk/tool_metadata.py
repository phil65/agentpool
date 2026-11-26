"""Typed tool metadata shapes for the OpenCode protocol.

These TypedDicts describe the exact metadata shapes that each OpenCode tool
returns and that the OpenCode TUI/Desktop UI consumes. They are derived from
the actual tool implementations in the ``sst/opencode`` TypeScript codebase
(``packages/opencode/src/tool/*.ts``).

The metadata is stored in ``ToolStateCompleted.metadata`` /
``ToolStateRunning.metadata`` and rendered by the UI components in
``packages/ui/src/components/message-part.tsx``.

All tool metadata dicts inherit from :class:`TruncationFields` which provides
the ``truncated`` and ``output_path`` fields that the tool framework
(``Tool.define`` in ``tool.ts``) conditionally injects after each tool
execution.
"""

from typing import Any, Literal, NotRequired, TypedDict


FileDiffStatus = Literal["added", "deleted", "modified"]
ChangeType = Literal["add", "update", "delete", "move"]
# ---------------------------------------------------------------------------
# Common / shared sub-types
# ---------------------------------------------------------------------------


class LSPPosition(TypedDict):
    """LSP position in a text document (0-based)."""

    line: int
    character: int


class LSPRange(TypedDict):
    """LSP range in a text document."""

    start: LSPPosition
    end: LSPPosition


class LSPDiagnostic(TypedDict):
    """A single LSP diagnostic.

    The UI filters by ``severity == 1`` (error) and shows the first 3 per file.
    """

    range: LSPRange
    message: str
    severity: NotRequired[int]


LSPDiagnosticsMap = dict[str, list[LSPDiagnostic]]
"""Mapping from normalized file path to list of LSP diagnostics."""


class FileDiff(TypedDict):
    """A file diff entry (mirrors ``Snapshot.FileDiff`` in opencode).

    Used by edit and apply_patch tools.
    """

    file: str
    before: str
    after: str
    additions: int
    deletions: int
    status: NotRequired[FileDiffStatus]


class TruncationFields(TypedDict, total=False):
    """Fields injected by the tool framework's automatic truncation logic.

    ``Tool.define`` in ``tool.ts`` wraps every tool's ``execute`` and
    conditionally adds these fields to the returned metadata when the
    output exceeds size limits.

    All tool metadata TypedDicts inherit from this base so that these
    framework-level fields are available on every metadata dict without
    repetition.
    """

    truncated: bool
    """Whether the tool output was truncated by the framework."""
    output_path: str
    """Path to the full output file when truncation occurred."""


# ---------------------------------------------------------------------------
# bash
# ---------------------------------------------------------------------------


class BashMetadata(TruncationFields):
    """Metadata returned by the ``bash`` tool.

    The UI reads ``output`` for display and ``description`` for the subtitle.
    """

    output: str
    """Command output (stdout + stderr combined, truncated to 30KB for metadata)."""
    exit: int | None
    """Process exit code (``None`` when the process was killed/interrupted)."""
    description: str
    """Short human-readable description of the command."""
    command: NotRequired[str]
    """The command that was executed (fallback used by UI if not in input)."""


# ---------------------------------------------------------------------------
# edit
# ---------------------------------------------------------------------------


class EditMetadata(TruncationFields):
    """Metadata returned by the ``edit`` tool.

    The UI reads ``filediff`` for the diff viewer and ``diagnostics``
    for inline LSP error display.
    """

    diff: str
    """Unified diff string."""
    filediff: FileDiff
    """Structured before/after content with change counts."""
    diagnostics: LSPDiagnosticsMap
    """LSP diagnostics keyed by normalized file path."""


# ---------------------------------------------------------------------------
# multiedit
# ---------------------------------------------------------------------------


class MultiEditMetadata(TruncationFields):
    """Metadata returned by the ``multiedit`` tool.

    Contains a list of individual edit metadata results.
    """

    results: list[EditMetadata]
    """Metadata from each individual edit operation."""


# ---------------------------------------------------------------------------
# write
# ---------------------------------------------------------------------------


class WriteMetadata(TruncationFields):
    """Metadata returned by the ``write`` tool.

    The UI reads ``diagnostics`` for inline LSP error display.
    """

    diagnostics: LSPDiagnosticsMap
    """LSP diagnostics keyed by normalized file path."""
    filepath: str
    """Absolute path to the written file."""
    exists: bool
    """Whether the file existed before the write."""


# ---------------------------------------------------------------------------
# read
# ---------------------------------------------------------------------------


class ReadMetadata(TypedDict):
    """Metadata returned by the ``read`` tool.

    The UI reads ``loaded`` for showing instruction file references.

    Note: ``truncated`` is always set by the tool itself — the framework
    skips its own truncation when it sees ``truncated`` already present.
    """

    preview: str
    """First ~20 lines of content as a preview."""
    truncated: bool
    """Whether the file content was truncated."""
    loaded: list[str]
    """Paths to instruction files that were loaded alongside the read."""
    output_path: NotRequired[str]
    """Path to the full output file when truncation occurred."""


# ---------------------------------------------------------------------------
# glob
# ---------------------------------------------------------------------------


class GlobMetadata(TypedDict):
    """Metadata returned by the ``glob`` tool.

    Note: ``truncated`` is always set by the tool itself.
    """

    count: int
    """Number of files matched."""
    truncated: bool
    """Whether results were truncated (limited to 100 files)."""
    output_path: NotRequired[str]
    """Path to the full output file when truncation occurred."""


# ---------------------------------------------------------------------------
# grep
# ---------------------------------------------------------------------------


class GrepMetadata(TypedDict):
    """Metadata returned by the ``grep`` tool.

    Note: ``truncated`` is always set by the tool itself.
    """

    matches: int
    """Number of matching lines found."""
    truncated: bool
    """Whether results were truncated (limited to 100 matches)."""
    output_path: NotRequired[str]
    """Path to the full output file when truncation occurred."""


# ---------------------------------------------------------------------------
# list (ls)
# ---------------------------------------------------------------------------


class ListMetadata(TypedDict):
    """Metadata returned by the ``list`` tool.

    Note: ``truncated`` is always set by the tool itself.
    """

    count: int
    """Number of files listed."""
    truncated: bool
    """Whether results were truncated (limited to 100 entries)."""
    output_path: NotRequired[str]
    """Path to the full output file when truncation occurred."""


# ---------------------------------------------------------------------------
# task (sub-agent)
# ---------------------------------------------------------------------------


class TaskModelRef(TypedDict):
    """Model reference for the task tool."""

    modelID: str
    providerID: str


class TaskMetadata(TruncationFields):
    """Metadata returned by the ``task`` tool.

    The UI reads ``sessionId`` to link to the sub-agent's session.
    """

    sessionId: str
    """Session ID of the spawned sub-agent session."""
    model: TaskModelRef
    """The model used for the sub-agent."""


# ---------------------------------------------------------------------------
# todowrite / todoread
# ---------------------------------------------------------------------------


class TodoInfo(TypedDict):
    """A single todo item.

    Mirrors ``Todo.Info`` in ``packages/opencode/src/session/todo.ts``.
    """

    content: str
    """Brief description of the task."""
    status: str
    """Current status: ``pending``, ``in_progress``, ``completed``, ``cancelled``."""
    priority: str
    """Priority level: ``high``, ``medium``, ``low``."""


class TodoMetadata(TruncationFields):
    """Metadata returned by the ``todowrite`` and ``todoread`` tools.

    The UI reads ``todos`` to render the todo checklist.
    """

    todos: list[TodoInfo]
    """The current todo list."""


# ---------------------------------------------------------------------------
# question
# ---------------------------------------------------------------------------


class QuestionMetadata(TruncationFields):
    """Metadata returned by the ``question`` tool.

    The UI reads ``answers`` to show completed question/answer pairs.
    """

    answers: list[list[str]]
    """User answers in order of questions.

    Each answer is a list of selected labels (multiple selections).
    """


# ---------------------------------------------------------------------------
# apply_patch
# ---------------------------------------------------------------------------


class ApplyPatchFileInfo(TypedDict):
    """Per-file information in an apply_patch result.

    Used by the UI to render individual file diffs.
    """

    filePath: str
    """Absolute path to the file."""
    relativePath: str
    """Path relative to the worktree root."""
    type: ChangeType
    """The type of change applied."""
    diff: str
    """Unified diff string for this file."""
    before: str
    """File content before the patch."""
    after: str
    """File content after the patch."""
    additions: int
    """Number of lines added."""
    deletions: int
    """Number of lines removed."""
    movePath: NotRequired[str]
    """Target path when the file was moved/renamed."""


class ApplyPatchMetadata(TruncationFields):
    """Metadata returned by the ``apply_patch`` tool.

    The UI reads ``files`` to render per-file diffs.
    """

    diff: str
    """Combined unified diff for all files."""
    files: list[ApplyPatchFileInfo]
    """Per-file change information."""
    diagnostics: LSPDiagnosticsMap
    """LSP diagnostics keyed by normalized file path."""


# ---------------------------------------------------------------------------
# batch
# ---------------------------------------------------------------------------


class BatchCallDetail(TypedDict):
    """Result detail for a single call within a batch."""

    tool: str
    """Name of the tool that was called."""
    success: bool
    """Whether the call succeeded."""


class BatchMetadata(TruncationFields):
    """Metadata returned by the ``batch`` tool."""

    totalCalls: int
    """Total number of tool calls in the batch."""
    successful: int
    """Number of successful calls."""
    failed: int
    """Number of failed calls."""
    tools: list[str]
    """Names of tools that were called."""
    details: list[BatchCallDetail]
    """Per-call success/failure details."""


# ---------------------------------------------------------------------------
# websearch / webfetch / codesearch
#
# These tools return empty metadata ``{}``. Only framework-injected
# truncation fields may be present.
# ---------------------------------------------------------------------------


class WebSearchMetadata(TruncationFields):
    """Metadata returned by the ``websearch`` tool (empty by default)."""


class WebFetchMetadata(TruncationFields):
    """Metadata returned by the ``webfetch`` tool (empty by default)."""


class CodeSearchMetadata(TruncationFields):
    """Metadata returned by the ``codesearch`` tool (empty by default)."""


# ---------------------------------------------------------------------------
# skill
# ---------------------------------------------------------------------------


class SkillMetadata(TruncationFields):
    """Metadata returned by the ``skill`` tool."""

    name: str
    """Name of the loaded skill."""
    dir: str
    """Directory path containing the skill files."""


# ---------------------------------------------------------------------------
# lsp
# ---------------------------------------------------------------------------


class LspMetadata(TruncationFields):
    """Metadata returned by the ``lsp`` tool."""

    result: list[Any]
    """Raw LSP results (definitions, references, hover info, etc.)."""


# ---------------------------------------------------------------------------
# plan_exit / plan_enter
#
# These tools return empty metadata ``{}``. Only framework-injected
# truncation fields may be present.
# ---------------------------------------------------------------------------


class PlanExitMetadata(TruncationFields):
    """Metadata returned by the ``plan_exit`` tool (empty by default)."""


class PlanEnterMetadata(TruncationFields):
    """Metadata returned by the ``plan_enter`` tool (empty by default)."""


# ---------------------------------------------------------------------------
# Union of all tool metadata types
# ---------------------------------------------------------------------------

ToolMetadata = (
    BashMetadata
    | EditMetadata
    | MultiEditMetadata
    | WriteMetadata
    | ReadMetadata
    | GlobMetadata
    | GrepMetadata
    | ListMetadata
    | TaskMetadata
    | TodoMetadata
    | QuestionMetadata
    | ApplyPatchMetadata
    | BatchMetadata
    | WebSearchMetadata
    | WebFetchMetadata
    | CodeSearchMetadata
    | SkillMetadata
    | LspMetadata
    | PlanExitMetadata
    | PlanEnterMetadata
)


#: Mapping from tool name to its metadata TypedDict type.
TOOL_METADATA_TYPES: dict[str, type[ToolMetadata]] = {
    "bash": BashMetadata,
    "edit": EditMetadata,
    "multiedit": MultiEditMetadata,
    "write": WriteMetadata,
    "read": ReadMetadata,
    "glob": GlobMetadata,
    "grep": GrepMetadata,
    "list": ListMetadata,
    "task": TaskMetadata,
    "todowrite": TodoMetadata,
    "todoread": TodoMetadata,
    "question": QuestionMetadata,
    "apply_patch": ApplyPatchMetadata,
    "batch": BatchMetadata,
    "websearch": WebSearchMetadata,
    "webfetch": WebFetchMetadata,
    "codesearch": CodeSearchMetadata,
    "skill": SkillMetadata,
    "lsp": LspMetadata,
    "plan_exit": PlanExitMetadata,
    "plan_enter": PlanEnterMetadata,
}
