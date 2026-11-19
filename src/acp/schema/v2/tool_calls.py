"""v2 tool calls: upserts, streamed content, diffs and cancellation.

``tool_call_update`` is one upsert keyed by ``toolCallId`` (v1 had separate
``tool_call`` and ``tool_call_update``). Its fields are patches: omitted leaves a
value unchanged, ``None`` clears it. ``tool_call_content_chunk`` appends one content
item to a tool call.
"""

from __future__ import annotations

from collections.abc import Sequence  # noqa: TC003
from typing import Annotated, Any, Literal

from pydantic import MISSING, Discriminator, Field, Tag

from acp.schema.v2.base import OTHER_TAG, AnnotatedObject, Unknown, open_discriminator
from acp.schema.v2.common import (  # noqa: TC001
    AbsolutePath,
    MediaType,
    SessionId,
    TerminalId,
    ToolCallId,
)
from acp.schema.v2.content import ContentBlock  # noqa: TC001


ToolKind = (
    Literal[
        "read",
        "edit",
        "delete",
        "move",
        "search",
        "execute",
        "think",
        "fetch",
        "switch_mode",
        "other",
    ]
    | str
)
"""Category of a tool, for icons and display. Values starting with ``_`` are extensions."""

ToolCallStatus = (
    Literal["pending", "in_progress", "paused", "completed", "failed", "cancelled"] | str
)
"""Execution status of a tool call. Values starting with ``_`` are extensions."""


# --- Diffs --------------------------------------------------------------------------

DiffFileType = Literal["text", "binary", "directory", "symlink"] | str
"""Kind of file content of a diff change. Values starting with ``_`` are extensions."""

DiffPatchFormat = Literal["git_patch"] | str
"""Format of a diff patch; ``git_patch`` is ``diff --git`` sections in Git's ``--patch`` format."""


class _DiffChangeBase(AnnotatedObject):
    file_type: DiffFileType | MISSING | None = MISSING
    """Kind of file content after the operation (for ``delete``: of the deleted file)."""

    mime_type: MediaType | MISSING | None = MISSING
    """MIME type of the contents after the operation (for ``delete``: of the deleted file)."""


class _PathChange(_DiffChangeBase):
    path: AbsolutePath
    """Absolute path of the file."""


class _PathPairChange(_DiffChangeBase):
    old_path: AbsolutePath
    """Absolute path before the operation."""

    path: AbsolutePath
    """Absolute path after the operation."""


class AddDiffChange(_PathChange):
    """A file was added."""

    operation: Literal["add"] = Field(default="add", init=False)


class DeleteDiffChange(_PathChange):
    """A file was deleted."""

    operation: Literal["delete"] = Field(default="delete", init=False)


class ModifyDiffChange(_PathChange):
    """A file was modified in place."""

    operation: Literal["modify"] = Field(default="modify", init=False)


class MoveDiffChange(_PathPairChange):
    """A file was moved or renamed."""

    operation: Literal["move"] = Field(default="move", init=False)


class CopyDiffChange(_PathPairChange):
    """A file was copied."""

    operation: Literal["copy"] = Field(default="copy", init=False)


class UnknownDiffChange(Unknown, _DiffChangeBase):
    """A custom or future file operation; values starting with ``_`` are extensions."""

    operation: str
    """The operation."""


DiffChange = Annotated[
    Annotated[AddDiffChange, Tag("add")]
    | Annotated[DeleteDiffChange, Tag("delete")]
    | Annotated[ModifyDiffChange, Tag("modify")]
    | Annotated[MoveDiffChange, Tag("move")]
    | Annotated[CopyDiffChange, Tag("copy")]
    | Annotated[UnknownDiffChange, Tag(OTHER_TAG)],
    Discriminator(
        open_discriminator("operation", frozenset({"add", "delete", "modify", "move", "copy"}))
    ),
]
"""One file-level change of a :class:`Diff`."""


class DiffPatch(AnnotatedObject):
    """Renderable patch text and its format."""

    format: DiffPatchFormat
    """The patch format; ACP defines only ``git_patch``."""

    text: str
    """The patch text."""


# --- Tool call content --------------------------------------------------------------


class Content(AnnotatedObject):
    """A standard content block (text, image, resource) produced by a tool call."""

    type: Literal["content"] = Field(default="content", init=False)

    content: ContentBlock
    """The content block."""


class Diff(AnnotatedObject):
    """File changes produced by a tool call.

    ``changes`` is authoritative for affected paths and operations; ``patch`` optionally
    carries renderable text for some or all of them.
    """

    type: Literal["diff"] = Field(default="diff", init=False)

    changes: Sequence[DiffChange] = Field(min_length=1)
    """The structured file changes; at least one."""

    patch: DiffPatch | MISSING | None = MISSING
    """Renderable patch text; agents SHOULD provide it whenever feasible."""


class Terminal(AnnotatedObject):
    """A display-only reference to an agent-owned terminal.

    State and output arrive separately as ``terminal_update`` and
    ``terminal_output_chunk``.
    """

    type: Literal["terminal"] = Field(default="terminal", init=False)

    terminal_id: TerminalId
    """The terminal to display."""


class SessionReference(AnnotatedObject):
    """A display-only reference to another session the tool call operates on, e.g. a subagent."""

    type: Literal["session"] = Field(default="session", init=False)

    session_id: SessionId
    """The referenced session."""


class UnknownToolCallContent(Unknown):
    """Custom or future tool call content; ``type`` values starting with ``_`` are extensions."""

    type: str
    """The content kind."""


ToolCallContent = Annotated[
    Annotated[Content, Tag("content")]
    | Annotated[Diff, Tag("diff")]
    | Annotated[Terminal, Tag("terminal")]
    | Annotated[SessionReference, Tag("session")]
    | Annotated[UnknownToolCallContent, Tag(OTHER_TAG)],
    Discriminator(
        open_discriminator("type", frozenset({"content", "diff", "terminal", "session"}))
    ),
]
"""Content produced by a tool call."""


class ToolCallLocation(AnnotatedObject):
    """A file location a tool reads or modifies, for "follow-along" features."""

    path: AbsolutePath
    """The absolute file path."""

    line: int | MISSING | None = Field(default=MISSING, ge=1)
    """1-based line number in the file; ``0`` is invalid."""


# --- Capabilities and upserts -------------------------------------------------------


class ToolCallCancelCapabilities(AnnotatedObject):
    """The tool call can be cancelled with ``tool_call/cancel``."""


class ToolCallCapabilities(AnnotatedObject):
    """Client-initiated controls a tool call currently supports.

    A malformed value counts as no controls.
    """

    cancel: ToolCallCancelCapabilities | MISSING | None = MISSING
    """Permits ``tool_call/cancel`` for this tool call."""


class ToolCallUpdate(AnnotatedObject):
    """A tool call upsert, keyed by ``tool_call_id``.

    Sent as the ``tool_call_update`` session update (:class:`ToolCallSessionUpdate` in
    :mod:`acp.schema.v2.session_updates`), and embedded in tool call permission requests.
    """

    tool_call_id: ToolCallId
    """Unique identifier of the tool call in the session."""

    name: str | MISSING | None = MISSING
    """Programmatic name of the tool."""

    title: str | MISSING | None = MISSING
    """Human-readable title of what the tool is doing."""

    kind: ToolKind | MISSING | None = MISSING
    """Category of the tool."""

    status: ToolCallStatus | MISSING | None = MISSING
    """Execution status; ``None`` resets it to ``pending``."""

    content: Sequence[ToolCallContent] | MISSING | None = MISSING
    """Content produced by the tool call; replaces earlier content."""

    locations: Sequence[ToolCallLocation] | MISSING | None = MISSING
    """File locations affected by the tool call."""

    raw_input: Any | MISSING = MISSING
    """Raw input parameters of the tool."""

    raw_output: Any | MISSING = MISSING
    """Raw output of the tool."""

    capabilities: ToolCallCapabilities | MISSING | None = MISSING
    """Controls the tool call currently supports, such as cancellation."""


class ToolCallContentChunk(AnnotatedObject):
    """A content item appended to a tool call's content."""

    session_update: Literal["tool_call_content_chunk"] = Field(
        default="tool_call_content_chunk", init=False
    )

    tool_call_id: ToolCallId
    """The tool call the content belongs to."""

    content: ToolCallContent
    """The appended content item."""


class CancelToolCallRequest(AnnotatedObject):
    """Parameters of ``tool_call/cancel``: cancel one tool call.

    Only for tool calls that currently advertise ``capabilities.cancel``.
    """

    session_id: SessionId
    """The session that owns the tool call."""

    tool_call_id: ToolCallId
    """The tool call to cancel."""


class CancelToolCallResponse(AnnotatedObject):
    """Response to ``tool_call/cancel``."""
