"""Data models for diagnostics and LSP operations."""

from dataclasses import dataclass, field
from typing import Any, Literal, TypedDict


@dataclass
class Diagnostic:
    """A single diagnostic message from an LSP server or CLI tool."""

    file: str
    """File path the diagnostic applies to."""

    line: int
    """1-based line number."""

    column: int
    """1-based column number."""

    severity: Literal["error", "warning", "info", "hint"]
    """Severity level."""

    message: str
    """Human-readable diagnostic message."""

    source: str
    """Tool/server that produced this diagnostic (e.g., 'pyright', 'mypy')."""

    code: str | None = None
    """Optional error code (e.g., 'reportGeneralTypeIssues')."""

    end_line: int | None = None
    """End line for range diagnostics."""

    end_column: int | None = None
    """End column for range diagnostics."""


@dataclass
class DiagnosticsResult:
    """Result of running diagnostics on files."""

    diagnostics: list[Diagnostic] = field(default_factory=list)
    """List of diagnostics found."""

    success: bool = True
    """Whether the diagnostic run completed without errors."""

    duration: float = 0.0
    """Time taken in seconds."""

    error: str | None = None
    """Error message if success is False."""

    server_id: str | None = None
    """ID of the server that produced these results."""

    @property
    def error_count(self) -> int:
        """Count of error-level diagnostics."""
        return sum(1 for d in self.diagnostics if d.severity == "error")

    @property
    def warning_count(self) -> int:
        """Count of warning-level diagnostics."""
        return sum(1 for d in self.diagnostics if d.severity == "warning")


@dataclass
class LSPServerState:
    """State of a running LSP server."""

    server_id: str
    """Identifier for this server (e.g., 'pyright', 'pylsp')."""

    process_id: str
    """Process ID from ProcessManager."""

    port: int
    """TCP port for communication."""

    language: str
    """Primary language this server handles."""

    root_uri: str | None = None
    """Workspace root URI."""

    initialized: bool = False
    """Whether LSP initialize handshake completed."""

    capabilities: dict[str, Any] = field(default_factory=dict)
    """Server capabilities from initialize response."""


@dataclass
class Position:
    """A position in a text document (0-based line and character)."""

    line: int
    """0-based line number."""

    character: int
    """0-based character offset."""


@dataclass
class Range:
    """A range in a text document."""

    start: Position
    """Start position (inclusive)."""

    end: Position
    """End position (exclusive)."""


@dataclass
class Location:
    """A location in a resource (file + range)."""

    uri: str
    """File URI."""

    range: Range
    """Range within the file."""


@dataclass
class SymbolInfo:
    """Information about a symbol (function, class, variable, etc.)."""

    name: str
    """Symbol name."""

    kind: str
    """Symbol kind (function, class, variable, etc.)."""

    location: Location
    """Where the symbol is defined."""

    container_name: str | None = None
    """Name of the containing symbol (e.g., class name for a method)."""

    detail: str | None = None
    """Additional detail (e.g., type signature)."""

    children: list[SymbolInfo] = field(default_factory=list)
    """Child symbols (for document symbols with hierarchy)."""


@dataclass
class HoverInfo:
    """Hover information for a position."""

    contents: str
    """Hover content (may be markdown)."""

    range: Range | None = None
    """Range the hover applies to."""


@dataclass
class CompletionItem:
    """A completion suggestion."""

    label: str
    """The label shown in completion list."""

    kind: str | None = None
    """Kind of completion (function, variable, class, etc.)."""

    detail: str | None = None
    """Detail shown next to label."""

    documentation: str | None = None
    """Documentation string."""

    insert_text: str | None = None
    """Text to insert (if different from label)."""

    sort_text: str | None = None
    """Sort order text."""


@dataclass
class CodeAction:
    """A code action (quick fix, refactoring, etc.)."""

    title: str
    """Title of the action."""

    kind: str | None = None
    """Kind of action (quickfix, refactor, etc.)."""

    diagnostics: list[Diagnostic] = field(default_factory=list)
    """Diagnostics this action resolves."""

    is_preferred: bool = False
    """Whether this is the preferred action."""

    edit: dict[str, Any] | None = None
    """Workspace edit to apply."""

    command: dict[str, Any] | None = None
    """Command to execute."""


@dataclass
class CallHierarchyItem:
    """An item in a call hierarchy."""

    name: str
    """Name of the callable."""

    kind: str
    """Symbol kind."""

    uri: str
    """File URI."""

    range: Range
    """Range of the callable."""

    selection_range: Range
    """Range to select (usually the name)."""

    detail: str | None = None
    """Additional detail."""

    data: Any = None
    """Server-specific data for resolving."""


@dataclass
class CallHierarchyCall:
    """A call in a call hierarchy."""

    item: CallHierarchyItem
    """The calling/called item."""

    from_ranges: list[Range] = field(default_factory=list)
    """Ranges where the call occurs."""


@dataclass
class TypeHierarchyItem:
    """An item in a type hierarchy."""

    name: str
    """Name of the type."""

    kind: str
    """Symbol kind."""

    uri: str
    """File URI."""

    range: Range
    """Range of the type."""

    selection_range: Range
    """Range to select (usually the name)."""

    detail: str | None = None
    """Additional detail."""

    data: Any = None
    """Server-specific data for resolving."""


@dataclass
class SignatureInfo:
    """Signature help information."""

    label: str
    """The signature label."""

    documentation: str | None = None
    """Documentation string."""

    parameters: list[dict[str, Any]] = field(default_factory=list)
    """Parameter information."""

    active_parameter: int | None = None
    """Index of the active parameter."""


@dataclass
class RenameResult:
    """Result of a rename operation."""

    changes: dict[str, list[dict[str, Any]]]
    """Map of file URI to list of text edits."""

    success: bool = True
    """Whether rename preparation succeeded."""

    error: str | None = None
    """Error message if failed."""


# LSP Hover content types (from LSP spec)


class MarkedStringDict(TypedDict):
    """MarkedString with language identifier (deprecated in LSP 3.0+)."""

    language: str
    value: str


class MarkupContent(TypedDict):
    """MarkupContent from LSP spec."""

    kind: str  # "plaintext" or "markdown"
    value: str


# Union type for hover contents
type HoverContents = str | MarkupContent | MarkedStringDict | list[str | MarkedStringDict]


# Symbol kind mapping from LSP numeric values
SYMBOL_KIND_MAP: dict[int, str] = {
    1: "file",
    2: "module",
    3: "namespace",
    4: "package",
    5: "class",
    6: "method",
    7: "property",
    8: "field",
    9: "constructor",
    10: "enum",
    11: "interface",
    12: "function",
    13: "variable",
    14: "constant",
    15: "string",
    16: "number",
    17: "boolean",
    18: "array",
    19: "object",
    20: "key",
    21: "null",
    22: "enum_member",
    23: "struct",
    24: "event",
    25: "operator",
    26: "type_parameter",
}


# Completion item kind mapping
COMPLETION_KIND_MAP: dict[int, str] = {
    1: "text",
    2: "method",
    3: "function",
    4: "constructor",
    5: "field",
    6: "variable",
    7: "class",
    8: "interface",
    9: "module",
    10: "property",
    11: "unit",
    12: "value",
    13: "enum",
    14: "keyword",
    15: "snippet",
    16: "color",
    17: "file",
    18: "reference",
    19: "folder",
    20: "enum_member",
    21: "constant",
    22: "struct",
    23: "event",
    24: "operator",
    25: "type_parameter",
}


# Code action kind mapping
CODE_ACTION_KIND_MAP: dict[str, str] = {
    "": "quickfix",
    "quickfix": "quickfix",
    "refactor": "refactor",
    "refactor.extract": "refactor.extract",
    "refactor.inline": "refactor.inline",
    "refactor.rewrite": "refactor.rewrite",
    "source": "source",
    "source.organizeImports": "source.organize_imports",
    "source.fixAll": "source.fix_all",
}
