"""SSE event models."""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import Field

from agentpool.utils.time_utils import now_ms
from opencode_sdk.models.app import Project  # noqa: TC001
from opencode_sdk.models.base import OpenCodeBaseModel
from opencode_sdk.models.common import FileDiff  # noqa: TC001
from opencode_sdk.models.message import MessageInfo  # noqa: TC001
from opencode_sdk.models.parts import Part  # noqa: TC001
from opencode_sdk.models.pty import PtyInfo  # noqa: TC001
from opencode_sdk.models.question import (  # noqa: TC001
    QuestionInfo,
    QuestionToolInfo,
)
from opencode_sdk.models.session import (
    Session,
    SessionStatus,
    SessionStatusType,
    Todo,
)


Variant = Literal["info", "success", "warning", "error"]
FileUpdateEvent = Literal["add", "change", "unlink"]
ConnectionStatus = Literal["connected", "error"]

PermissionReply = Literal["once", "always", "reject"]
"""Permission reply type matching OpenCode's PermissionNext.Reply."""


class EmptyProperties(OpenCodeBaseModel):
    """Empty properties object."""


class ServerHeartbeatEvent(OpenCodeBaseModel):
    """Server heartbeat event - sent periodically to keep connections alive."""

    type: Literal["server.heartbeat"] = Field(default="server.heartbeat", init=False)
    properties: EmptyProperties = Field(default_factory=EmptyProperties)


class ServerConnectedEvent(OpenCodeBaseModel):
    """Server connected event."""

    type: Literal["server.connected"] = Field(default="server.connected", init=False)
    properties: EmptyProperties = Field(default_factory=EmptyProperties)


class SessionInfoProperties(OpenCodeBaseModel):
    """Session info wrapper for events."""

    session_id: str
    info: Session


class SessionCreatedEvent(OpenCodeBaseModel):
    """Session created event."""

    type: Literal["session.created"] = Field(default="session.created", init=False)
    properties: SessionInfoProperties

    @classmethod
    def create(cls, session: Session) -> Self:
        return cls(properties=SessionInfoProperties(session_id=session.id, info=session))


class SessionUpdatedEvent(OpenCodeBaseModel):
    """Session updated event."""

    type: Literal["session.updated"] = Field(default="session.updated", init=False)
    properties: SessionInfoProperties

    @classmethod
    def create(cls, session: Session) -> Self:
        return cls(properties=SessionInfoProperties(session_id=session.id, info=session))


class SessionDeletedProperties(OpenCodeBaseModel):
    """Properties for session deleted event."""

    session_id: str
    info: Session


class SessionDeletedEvent(OpenCodeBaseModel):
    """Session deleted event."""

    type: Literal["session.deleted"] = Field(default="session.deleted", init=False)
    properties: SessionDeletedProperties

    @classmethod
    def create(cls, session_id: str, info: Session) -> Self:
        return cls(properties=SessionDeletedProperties(session_id=session_id, info=info))


class SessionStatusProperties(OpenCodeBaseModel):
    """Properties for session status event."""

    session_id: str
    status: SessionStatus


class SessionStatusEvent(OpenCodeBaseModel):
    """Session status event."""

    type: Literal["session.status"] = Field(default="session.status", init=False)
    properties: SessionStatusProperties

    @classmethod
    def create(cls, session_id: str, status_type: SessionStatusType | SessionStatus) -> Self:
        status = SessionStatus(type=status_type) if isinstance(status_type, str) else status_type
        return cls(properties=SessionStatusProperties(session_id=session_id, status=status))


class SessionIdleProperties(OpenCodeBaseModel):
    """Properties for session idle event (deprecated but still used by TUI)."""

    session_id: str


class SessionIdleEvent(OpenCodeBaseModel):
    """Session idle event (deprecated but still used by TUI run command)."""

    type: Literal["session.idle"] = Field(default="session.idle", init=False)
    properties: SessionIdleProperties

    @classmethod
    def create(cls, session_id: str) -> Self:
        return cls(properties=SessionIdleProperties(session_id=session_id))


class SessionCompactedProperties(OpenCodeBaseModel):
    """Properties for session compacted event."""

    session_id: str


class SessionCompactedEvent(OpenCodeBaseModel):
    """Session compacted event - emitted when context compaction completes."""

    type: Literal["session.compacted"] = Field(default="session.compacted", init=False)
    properties: SessionCompactedProperties

    @classmethod
    def create(cls, session_id: str) -> Self:
        return cls(properties=SessionCompactedProperties(session_id=session_id))


class SessionErrorInfo(OpenCodeBaseModel):
    """Error information for session error event.

    Simplified version of OpenCode's error types (ProviderAuthError, UnknownError, etc.)
    """

    name: str
    """Error type name (e.g., 'UnknownError', 'ProviderAuthError')."""

    data: dict[str, Any] | None = None
    """Additional error data, typically contains 'message' field."""


class SessionErrorProperties(OpenCodeBaseModel):
    """Properties for session error event."""

    session_id: str | None = Field(default=None)
    error: SessionErrorInfo | None = None


class SessionErrorEvent(OpenCodeBaseModel):
    """Session error event - emitted when an error occurs during message processing."""

    type: Literal["session.error"] = Field(default="session.error", init=False)
    properties: SessionErrorProperties

    @classmethod
    def from_exception(cls, exception: Exception, session_id: str | None = None) -> Self:
        error_name = type(exception).__name__
        error_message = str(exception)
        return cls.create(session_id=session_id, error_name=error_name, error_message=error_message)

    @classmethod
    def create(
        cls,
        session_id: str | None = None,
        error_name: str = "UnknownError",
        error_message: str | None = None,
    ) -> Self:
        error_data = {"message": error_message} if error_message else None
        error = SessionErrorInfo(name=error_name, data=error_data)
        props = SessionErrorProperties(session_id=session_id, error=error)
        return cls(properties=props)


class MessageUpdatedEventProperties(OpenCodeBaseModel):
    """Properties for message updated event."""

    session_id: str
    info: MessageInfo


class MessageUpdatedEvent(OpenCodeBaseModel):
    """Message updated event."""

    type: Literal["message.updated"] = Field(default="message.updated", init=False)
    properties: MessageUpdatedEventProperties

    @classmethod
    def create(cls, message: MessageInfo) -> Self:
        return cls(
            properties=MessageUpdatedEventProperties(session_id=message.session_id, info=message)
        )


class PartUpdatedEventProperties(OpenCodeBaseModel):
    """Properties for part updated event."""

    session_id: str
    part: Part
    time: int
    delta: str | None = None


class PartUpdatedEvent(OpenCodeBaseModel):
    """Part updated event."""

    type: Literal["message.part.updated"] = Field(default="message.part.updated", init=False)
    properties: PartUpdatedEventProperties

    @classmethod
    def create(cls, part: Part, delta: str | None = None, *, time: int | None = None) -> Self:
        return cls(
            properties=PartUpdatedEventProperties(
                session_id=part.session_id,
                part=part,
                time=time or now_ms(),
                delta=delta,
            )
        )


class PartDeltaEventProperties(OpenCodeBaseModel):
    """Properties for message part delta event."""

    session_id: str
    message_id: str
    part_id: str
    delta: str


class PartDeltaEvent(OpenCodeBaseModel):
    """Message part delta event - streaming text delta for a part."""

    type: Literal["message.part.delta"] = Field(default="message.part.delta", init=False)
    properties: PartDeltaEventProperties

    @classmethod
    def create(
        cls,
        session_id: str,
        message_id: str,
        part_id: str,
        delta: str,
    ) -> Self:
        return cls(
            properties=PartDeltaEventProperties(
                session_id=session_id,
                message_id=message_id,
                part_id=part_id,
                delta=delta,
            )
        )


class MessageRemovedProperties(OpenCodeBaseModel):
    """Properties for message removed event."""

    session_id: str
    message_id: str


class MessageRemovedEvent(OpenCodeBaseModel):
    """Message removed event - emitted during revert."""

    type: Literal["message.removed"] = Field(default="message.removed", init=False)
    properties: MessageRemovedProperties

    @classmethod
    def create(cls, session_id: str, message_id: str) -> Self:
        """Create message removed event."""
        props = MessageRemovedProperties(session_id=session_id, message_id=message_id)
        return cls(properties=props)


class PartRemovedProperties(OpenCodeBaseModel):
    """Properties for part removed event."""

    session_id: str
    message_id: str
    part_id: str


class PartRemovedEvent(OpenCodeBaseModel):
    """Part removed event - emitted during revert."""

    type: Literal["message.part.removed"] = Field(default="message.part.removed", init=False)
    properties: PartRemovedProperties

    @classmethod
    def create(cls, session_id: str, message_id: str, part_id: str) -> Self:
        """Create part removed event."""
        props = PartRemovedProperties(session_id=session_id, message_id=message_id, part_id=part_id)
        return cls(properties=props)


class PermissionReplyRequest(OpenCodeBaseModel):
    """Request body for responding to a permission request."""

    reply: PermissionReply
    """Reply: 'once' | 'always' | 'reject'."""

    message: str | None = None
    """Optional message to include with the reply."""


class PermissionToolInfo(OpenCodeBaseModel):
    """Tool information for permission event."""

    message_id: str
    """Message ID."""

    call_id: str | None = None
    """Optional tool call ID."""


class PermissionAskedProperties(OpenCodeBaseModel):
    """Properties for permission.asked event.

    Matches OpenCode's PermissionNext.Event.Asked schema.
    """

    id: str
    """Permission request ID."""

    session_id: str
    """Session ID."""

    permission: str
    """Tool/permission type name."""

    patterns: list[str]
    """Patterns for matching (e.g., file paths, commands)."""

    metadata: dict[str, Any]
    """Arbitrary metadata about the tool call."""

    always: list[str]
    """Patterns that would be approved for future requests if user selects 'always'."""

    tool: PermissionToolInfo
    """Tool call information."""


class PermissionRequestEvent(OpenCodeBaseModel):
    """Permission request event - sent when a tool needs user confirmation.

    Uses 'permission.asked' event type for OpenCode TUI compatibility.
    """

    type: Literal["permission.asked"] = Field(default="permission.asked", init=False)
    properties: PermissionAskedProperties

    @classmethod
    def create(
        cls,
        session_id: str,
        permission_id: str,
        tool_name: str,
        args_preview: str,
        message: str,
        message_id: str = "",
        call_id: str | None = None,
    ) -> Self:
        # Create pattern from tool name and args
        pattern = f"{tool_name}: {args_preview}" if args_preview else tool_name

        props = PermissionAskedProperties(
            id=permission_id,
            session_id=session_id,
            permission=tool_name,
            patterns=[pattern],
            metadata={"args_preview": args_preview},
            always=[pattern],  # Same pattern for "always" approval
            tool=PermissionToolInfo(message_id=message_id, call_id=call_id),
        )
        return cls(properties=props)


class PermissionRepliedProperties(OpenCodeBaseModel):
    """Properties for permission replied event.

    Matches OpenCode's permission.replied event schema.
    """

    session_id: str
    """Session ID."""

    request_id: str
    """Request/Permission ID."""

    reply: PermissionReply
    """Reply: 'once' | 'always' | 'reject'."""


class PermissionResolvedEvent(OpenCodeBaseModel):
    """Permission resolved event - sent when a permission request is answered.

    Uses 'permission.replied' event type for OpenCode TUI compatibility.
    """

    type: Literal["permission.replied"] = Field(default="permission.replied", init=False)
    properties: PermissionRepliedProperties

    @classmethod
    def create(
        cls,
        session_id: str,
        request_id: str,
        reply: PermissionReply,
    ) -> Self:
        props = PermissionRepliedProperties(
            session_id=session_id,
            request_id=request_id,
            reply=reply,
        )
        return cls(properties=props)


class PermissionUpdatedProperties(OpenCodeBaseModel):
    """Properties for permission updated event."""

    id: str
    """Permission request ID."""

    session_id: str
    """Session ID."""

    permission: str
    """Tool/permission type name."""

    patterns: list[str]
    """Patterns for matching."""

    metadata: dict[str, Any]
    """Arbitrary metadata about the tool call."""

    always: list[str]
    """Patterns for 'always' approval."""

    tool: PermissionToolInfo
    """Tool call information."""


class PermissionUpdatedEvent(OpenCodeBaseModel):
    """Permission updated event - sent when permission status changes."""

    type: Literal["permission.updated"] = Field(default="permission.updated", init=False)
    properties: PermissionUpdatedProperties

    @classmethod
    def create(
        cls,
        session_id: str,
        permission_id: str,
        tool_name: str,
        patterns: list[str],
        metadata: dict[str, Any],
        message_id: str = "",
        call_id: str | None = None,
    ) -> Self:
        props = PermissionUpdatedProperties(
            id=permission_id,
            session_id=session_id,
            permission=tool_name,
            patterns=patterns,
            metadata=metadata,
            always=patterns,
            tool=PermissionToolInfo(message_id=message_id, call_id=call_id),
        )
        return cls(properties=props)


# =============================================================================
# TUI Events - for external control of the TUI (e.g., VSCode extension)
# =============================================================================


class TuiPromptAppendProperties(OpenCodeBaseModel):
    """Properties for TUI prompt append event."""

    text: str


class TuiPromptAppendEvent(OpenCodeBaseModel):
    """TUI prompt append event - appends text to the prompt input."""

    type: Literal["tui.prompt.append"] = Field(default="tui.prompt.append", init=False)
    properties: TuiPromptAppendProperties

    @classmethod
    def create(cls, text: str) -> Self:
        return cls(properties=TuiPromptAppendProperties(text=text))


class TuiCommandExecuteProperties(OpenCodeBaseModel):
    """Properties for TUI command execute event."""

    command: str


class TuiCommandExecuteEvent(OpenCodeBaseModel):
    """TUI command execute event - executes a TUI command.

    Commands include:
    - session.list, session.new, session.share, session.interrupt, session.compact
    - session.page.up, session.page.down, session.half.page.up, session.half.page.down
    - session.first, session.last
    - prompt.clear, prompt.submit
    - agent.cycle
    """

    type: Literal["tui.command.execute"] = Field(default="tui.command.execute", init=False)
    properties: TuiCommandExecuteProperties

    @classmethod
    def create(cls, command: str) -> Self:
        return cls(properties=TuiCommandExecuteProperties(command=command))


class TuiToastShowProperties(OpenCodeBaseModel):
    """Properties for TUI toast show event."""

    title: str | None = None
    message: str
    variant: Variant = "info"
    duration: int = 5000  # Duration in milliseconds


class TuiToastShowEvent(OpenCodeBaseModel):
    """TUI toast show event - shows a toast notification."""

    type: Literal["tui.toast.show"] = Field(default="tui.toast.show", init=False)
    properties: TuiToastShowProperties

    @classmethod
    def create(
        cls,
        message: str,
        variant: Variant = "info",
        title: str | None = None,
        duration: int = 5000,
    ) -> Self:
        props = TuiToastShowProperties(
            title=title,
            message=message,
            variant=variant,
            duration=duration,
        )
        return cls(properties=props)


class TuiSessionSelectProperties(OpenCodeBaseModel):
    """Properties for TUI session select event."""

    session_id: str


class TuiSessionSelectEvent(OpenCodeBaseModel):
    """TUI session select event - navigates TUI to a specific session."""

    type: Literal["tui.session.select"] = Field(default="tui.session.select", init=False)
    properties: TuiSessionSelectProperties

    @classmethod
    def create(cls, session_id: str) -> Self:
        return cls(properties=TuiSessionSelectProperties(session_id=session_id))


# =============================================================================
# Todo Events
# =============================================================================


class TodoUpdatedProperties(OpenCodeBaseModel):
    """Properties for todo updated event."""

    session_id: str
    todos: list[Todo]


class TodoUpdatedEvent(OpenCodeBaseModel):
    """Todo list updated event."""

    type: Literal["todo.updated"] = Field(default="todo.updated", init=False)
    properties: TodoUpdatedProperties

    @classmethod
    def create(cls, session_id: str, todos: list[Todo]) -> Self:
        return cls(properties=TodoUpdatedProperties(session_id=session_id, todos=todos))


# =============================================================================
# File Watcher Events
# =============================================================================


class FileWatcherUpdatedProperties(OpenCodeBaseModel):
    """Properties for file watcher updated event."""

    file: str
    """Absolute path to the file that changed."""

    event: FileUpdateEvent
    """Type of change: add (created), change (modified), unlink (deleted)."""


class FileWatcherUpdatedEvent(OpenCodeBaseModel):
    """File watcher updated event - sent when a project file changes."""

    type: Literal["file.watcher.updated"] = Field(default="file.watcher.updated", init=False)
    properties: FileWatcherUpdatedProperties

    @classmethod
    def create(cls, file: str, event: FileUpdateEvent) -> Self:
        return cls(properties=FileWatcherUpdatedProperties(file=file, event=event))


# =============================================================================
# PTY Events
# =============================================================================


class PtyCreatedProperties(OpenCodeBaseModel):
    """Properties for PTY created event."""

    info: PtyInfo
    """PTY session info."""


class PtyCreatedEvent(OpenCodeBaseModel):
    """PTY session created event."""

    type: Literal["pty.created"] = Field(default="pty.created", init=False)
    properties: PtyCreatedProperties

    @classmethod
    def create(cls, info: PtyInfo) -> Self:
        return cls(properties=PtyCreatedProperties(info=info))


class PtyUpdatedProperties(OpenCodeBaseModel):
    """Properties for PTY updated event."""

    info: PtyInfo
    """PTY session info."""


class PtyUpdatedEvent(OpenCodeBaseModel):
    """PTY session updated event."""

    type: Literal["pty.updated"] = Field(default="pty.updated", init=False)
    properties: PtyUpdatedProperties

    @classmethod
    def create(cls, info: PtyInfo) -> Self:
        return cls(properties=PtyUpdatedProperties(info=info))


class PtyExitedProperties(OpenCodeBaseModel):
    """Properties for PTY exited event."""

    id: str
    """PTY session ID."""

    exit_code: int
    """Process exit code."""


class PtyExitedEvent(OpenCodeBaseModel):
    """PTY process exited event."""

    type: Literal["pty.exited"] = Field(default="pty.exited", init=False)
    properties: PtyExitedProperties

    @classmethod
    def create(cls, pty_id: str, exit_code: int) -> Self:
        return cls(properties=PtyExitedProperties(id=pty_id, exit_code=exit_code))


class PtyDeletedProperties(OpenCodeBaseModel):
    """Properties for PTY deleted event."""

    id: str
    """PTY session ID."""


class PtyDeletedEvent(OpenCodeBaseModel):
    """PTY session deleted event."""

    type: Literal["pty.deleted"] = Field(default="pty.deleted", init=False)
    properties: PtyDeletedProperties

    @classmethod
    def create(cls, pty_id: str) -> Self:
        return cls(properties=PtyDeletedProperties(id=pty_id))


# =============================================================================
# LSP Events
# =============================================================================


class LspStatus(OpenCodeBaseModel):
    """LSP server status information."""

    id: str
    """Server identifier (e.g., 'pyright', 'rust-analyzer')."""

    name: str
    """Server name."""

    root: str
    """Relative workspace root path."""

    status: ConnectionStatus
    """Connection status."""


class LspUpdatedEvent(OpenCodeBaseModel):
    """LSP status updated event - sent when LSP server status changes."""

    type: Literal["lsp.updated"] = Field(default="lsp.updated", init=False)
    properties: EmptyProperties = Field(default_factory=EmptyProperties)


class LspClientDiagnosticsProperties(OpenCodeBaseModel):
    """Properties for LSP client diagnostics event."""

    server_id: str
    """LSP server ID that produced the diagnostics."""

    path: str
    """File path the diagnostics apply to."""


class LspClientDiagnosticsEvent(OpenCodeBaseModel):
    """LSP client diagnostics event - sent when diagnostics are published."""

    type: Literal["lsp.client.diagnostics"] = Field(default="lsp.client.diagnostics", init=False)
    properties: LspClientDiagnosticsProperties

    @classmethod
    def create(cls, server_id: str, path: str) -> Self:
        return cls(properties=LspClientDiagnosticsProperties(server_id=server_id, path=path))


# =============================================================================
# VCS Events
# =============================================================================


class ProjectUpdatedEvent(OpenCodeBaseModel):
    """Project metadata updated event."""

    type: Literal["project.updated"] = Field(default="project.updated", init=False)
    properties: Project

    @classmethod
    def create(cls, project: Project) -> Self:
        """Create project updated event."""
        return cls(properties=project)


class VcsBranchUpdatedProperties(OpenCodeBaseModel):
    """Properties for VCS branch updated event."""

    branch: str | None = None
    """Current branch name, or None if detached HEAD."""


# =============================================================================
# Session Diff Events
# =============================================================================


class SessionDiffProperties(OpenCodeBaseModel):
    """Properties for session diff event."""

    session_id: str
    diff: list[FileDiff]


class SessionDiffEvent(OpenCodeBaseModel):
    """Session diff event - emitted when file diffs are computed (revert, summary)."""

    type: Literal["session.diff"] = Field(default="session.diff", init=False)
    properties: SessionDiffProperties

    @classmethod
    def create(cls, session_id: str, diff: list[FileDiff]) -> Self:
        return cls(properties=SessionDiffProperties(session_id=session_id, diff=diff))


# =============================================================================
# File Events
# =============================================================================


class FileEditedProperties(OpenCodeBaseModel):
    """Properties for file edited event."""

    file: str
    """Absolute path to the edited file."""


class FileEditedEvent(OpenCodeBaseModel):
    """File edited event - emitted when a tool edits/writes/patches a file."""

    type: Literal["file.edited"] = Field(default="file.edited", init=False)
    properties: FileEditedProperties

    @classmethod
    def create(cls, file: str) -> Self:
        return cls(properties=FileEditedProperties(file=file))


# =============================================================================
# MCP Events
# =============================================================================


class McpToolsChangedProperties(OpenCodeBaseModel):
    """Properties for MCP tools changed event."""

    server: str
    """Name of the MCP server whose tools changed."""


class McpToolsChangedEvent(OpenCodeBaseModel):
    """MCP tools changed event - emitted when an MCP server's tool list changes.

    TODO: Hook into MCP SDK's ToolListChangedNotification to emit this event.
    OpenCode only emits this from the notification handler, not on connect/disconnect.
    """

    type: Literal["mcp.tools.changed"] = Field(default="mcp.tools.changed", init=False)
    properties: McpToolsChangedProperties

    @classmethod
    def create(cls, server: str) -> Self:
        return cls(properties=McpToolsChangedProperties(server=server))


# =============================================================================
# Command Events
# =============================================================================


class CommandExecutedProperties(OpenCodeBaseModel):
    """Properties for command executed event."""

    name: str
    """Command name."""

    session_id: str
    """Session ID."""

    arguments: str
    """Command arguments."""

    message_id: str
    """ID of the message that resulted from the command."""


class CommandExecutedEvent(OpenCodeBaseModel):
    """Command executed event - emitted after a slash command runs."""

    type: Literal["command.executed"] = Field(default="command.executed", init=False)
    properties: CommandExecutedProperties

    @classmethod
    def create(
        cls,
        name: str,
        session_id: str,
        arguments: str,
        message_id: str,
    ) -> Self:
        return cls(
            properties=CommandExecutedProperties(
                name=name,
                session_id=session_id,
                arguments=arguments,
                message_id=message_id,
            )
        )


class VcsBranchUpdatedEvent(OpenCodeBaseModel):
    """VCS branch updated event - sent when git branch changes."""

    type: Literal["vcs.branch.updated"] = Field(default="vcs.branch.updated", init=False)
    properties: VcsBranchUpdatedProperties

    @classmethod
    def create(cls, branch: str | None) -> Self:
        return cls(properties=VcsBranchUpdatedProperties(branch=branch))


class QuestionAskedProperties(OpenCodeBaseModel):
    """Properties for question asked event."""

    id: str
    session_id: str
    questions: list[QuestionInfo]
    tool: QuestionToolInfo | None = None


class QuestionAskedEvent(OpenCodeBaseModel):
    """Question asked event - sent when agent asks a question."""

    type: Literal["question.asked"] = Field(default="question.asked", init=False)
    properties: QuestionAskedProperties

    @classmethod
    def create(
        cls,
        request_id: str,
        session_id: str,
        questions: list[QuestionInfo],
        tool: QuestionToolInfo | None = None,
    ) -> Self:
        props = QuestionAskedProperties(
            id=request_id,
            session_id=session_id,
            questions=questions,
            tool=tool,
        )
        return cls(properties=props)


class QuestionRepliedProperties(OpenCodeBaseModel):
    """Properties for question replied event."""

    session_id: str
    request_id: str
    answers: list[list[str]]


class QuestionRepliedEvent(OpenCodeBaseModel):
    """Question replied event - sent when user answers a question."""

    type: Literal["question.replied"] = Field(default="question.replied", init=False)
    properties: QuestionRepliedProperties

    @classmethod
    def create(
        cls,
        session_id: str,
        request_id: str,
        answers: list[list[str]],
    ) -> Self:
        props = QuestionRepliedProperties(
            session_id=session_id,
            request_id=request_id,
            answers=answers,
        )
        return cls(properties=props)


class QuestionRejectedProperties(OpenCodeBaseModel):
    """Properties for question rejected event."""

    session_id: str
    request_id: str


class QuestionRejectedEvent(OpenCodeBaseModel):
    """Question rejected event - sent when user dismisses a question."""

    type: Literal["question.rejected"] = Field(default="question.rejected", init=False)
    properties: QuestionRejectedProperties

    @classmethod
    def create(
        cls,
        session_id: str,
        request_id: str,
    ) -> Self:
        props = QuestionRejectedProperties(session_id=session_id, request_id=request_id)
        return cls(properties=props)


Event = (
    ServerConnectedEvent
    | ServerHeartbeatEvent
    | SessionCreatedEvent
    | SessionUpdatedEvent
    | SessionDeletedEvent
    | SessionStatusEvent
    | SessionErrorEvent
    | SessionIdleEvent
    | SessionDiffEvent
    | SessionCompactedEvent
    | MessageUpdatedEvent
    | MessageRemovedEvent
    | PartUpdatedEvent
    | PartDeltaEvent
    | PartRemovedEvent
    | PermissionRequestEvent
    | PermissionResolvedEvent
    | PermissionUpdatedEvent
    | QuestionAskedEvent
    | QuestionRepliedEvent
    | QuestionRejectedEvent
    | TodoUpdatedEvent
    | FileWatcherUpdatedEvent
    | FileEditedEvent
    | McpToolsChangedEvent
    | CommandExecutedEvent
    | PtyCreatedEvent
    | PtyUpdatedEvent
    | PtyExitedEvent
    | PtyDeletedEvent
    | LspUpdatedEvent
    | LspClientDiagnosticsEvent
    | ProjectUpdatedEvent
    | VcsBranchUpdatedEvent
    | TuiPromptAppendEvent
    | TuiCommandExecuteEvent
    | TuiToastShowEvent
    | TuiSessionSelectEvent
)
