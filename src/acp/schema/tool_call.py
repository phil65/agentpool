"""Tool call schema definitions."""

from __future__ import annotations

import base64
from collections.abc import Sequence  # noqa: TC003
from typing import TYPE_CHECKING, Any, Literal, Self

from pydantic import Field

from acp.schema.base import AnnotatedObject, Schema
from acp.schema.content_blocks import (
    Annotations,
    Audience,
    AudioContentBlock,
    BlobResourceContents,
    ContentBlock,
    EmbeddedResourceContentBlock,
    ImageContentBlock,
    ResourceContentBlock,
    TextContentBlock,
    TextResourceContents,
)


if TYPE_CHECKING:
    from datetime import datetime


ToolCallKind = Literal[
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
ToolCallStatus = Literal["pending", "in_progress", "completed", "failed"]
PermissionKind = Literal["allow_once", "allow_always", "reject_once", "reject_always"]


class ToolCallUpdate(AnnotatedObject):
    """Details about the tool call requiring permission."""

    content: Sequence[ToolCallContent] | None = None
    """Replace the content collection."""

    kind: ToolCallKind | None = None
    """Update the tool kind."""

    locations: Sequence[ToolCallLocation] | None = None
    """Replace the locations collection."""

    raw_input: Any | None = None
    """Update the raw input."""

    raw_output: Any | None = None
    """Update the raw output."""

    status: ToolCallStatus | None = None
    """Update the execution status."""

    title: str | None = None
    """Update the human-readable title."""

    name: str | None = None
    """Programmatic name of the tool being invoked."""

    tool_call_id: str
    """The ID of the tool call being updated."""


class FileEditToolCallContent(AnnotatedObject):
    """File modification shown as a diff."""

    type: Literal["diff"] = Field(default="diff", init=False)
    """File modification shown as a diff."""

    new_text: str
    """The new content after modification."""

    old_text: str | None = None
    """The original content (None for new files)."""

    path: str
    """The file path being modified."""


class TerminalToolCallContent(AnnotatedObject):
    """Embed a terminal created with `terminal/create` by its id.

    The terminal must be added before calling `terminal/release`.
    See protocol docs: [Terminal](https://agentclientprotocol.com/protocol/terminal)
    """

    type: Literal["terminal"] = Field(default="terminal", init=False)
    """Terminal tool call content."""

    terminal_id: str
    """The ID of the terminal being embedded."""


class ContentToolCallContent(AnnotatedObject):
    """Standard content block (text, images, resources)."""

    type: Literal["content"] = Field(default="content", init=False)
    """Standard content block (text, images, resources)."""

    content: ContentBlock
    """The actual content block."""

    @classmethod
    def text(
        cls,
        text: str,
        *,
        audience: Audience | None = None,
        last_modified: datetime | str | None = None,
        priority: float | None = None,
    ) -> Self:
        """Create a ToolCallContent containing text.

        Args:
            text: The text content.
            audience: The audience for the text.
            last_modified: The last modified date of the text.
            priority: The priority of the text.
        """
        annotations = Annotations.optionally_create(
            audience=audience,
            last_modified=last_modified,
            priority=priority,
        )
        return cls(content=TextContentBlock(text=text, annotations=annotations))

    @classmethod
    def image(
        cls,
        data: str | bytes,
        mime_type: str,
        uri: str | None = None,
        audience: Audience | None = None,
        last_modified: datetime | str | None = None,
        priority: float | None = None,
    ) -> Self:
        """Create a ToolCallContent containing an embedded image resource.

        Args:
            data: The image data.
            mime_type: The MIME type of the image.
            uri: The URI of the image.
            audience: The audience for the image.
            last_modified: The last modified date of the image.
            priority: The priority of the image.
        """
        if isinstance(data, bytes):
            data = base64.b64encode(data).decode()
        annotations = Annotations.optionally_create(
            audience=audience,
            last_modified=last_modified,
            priority=priority,
        )
        image = ImageContentBlock(data=data, mime_type=mime_type, uri=uri, annotations=annotations)
        return cls(content=image)

    @classmethod
    def audio(
        cls,
        data: str | bytes,
        mime_type: str,
        audience: Audience | None = None,
        last_modified: datetime | str | None = None,
        priority: float | None = None,
    ) -> Self:
        """Create a ToolCallContent containing audio content.

        Args:
            data: The audio data.
            mime_type: The MIME type of the audio data.
            audience: The audience for the audio chunk.
            last_modified: The last modified date of the audio chunk.
            priority: The priority of the audio chunk.
        """
        if isinstance(data, bytes):
            data = base64.b64encode(data).decode()
        annotations = Annotations.optionally_create(
            audience=audience,
            last_modified=last_modified,
            priority=priority,
        )
        content = AudioContentBlock(data=data, mime_type=mime_type, annotations=annotations)
        return cls(content=content)

    @classmethod
    def resource(
        cls,
        name: str,
        uri: str,
        description: str | None = None,
        mime_type: str | None = None,
        size: int | None = None,
        title: str | None = None,
        audience: Audience | None = None,
        last_modified: datetime | str | None = None,
        priority: float | None = None,
    ) -> Self:
        """Create a ToolCallContent containing a resource content block.

        Args:
            name: The name of the resource.
            uri: The URI of the resource.
            description: The description of the resource.
            mime_type: The MIME type of the resource.
            size: The size of the resource.
            title: The title of the resource.
            audience: The audience of the resource.
            last_modified: The last modified date of the resource.
            priority: The priority of the resource.
        """
        annotations = Annotations.optionally_create(
            audience=audience,
            last_modified=last_modified,
            priority=priority,
        )
        block = ResourceContentBlock(
            name=name,
            uri=uri,
            description=description,
            mime_type=mime_type,
            size=size,
            title=title,
            annotations=annotations,
        )
        return cls(content=block)

    @classmethod
    def embedded_text_resource(
        cls,
        text: str,
        uri: str,
        mime_type: str | None = None,
        audience: Audience | None = None,
        last_modified: datetime | str | None = None,
        priority: float | None = None,
    ) -> Self:
        """Create a ToolCallContent containing an embedded text resource.

        Args:
            text: The text to embed.
            uri: The URI of the resource.
            mime_type: The MIME type of the resource.
            audience: The audience to apply to the resource.
            last_modified: The last modified date of the resource.
            priority: The priority of the resource.
        """
        annotations = Annotations.optionally_create(
            audience=audience,
            last_modified=last_modified,
            priority=priority,
        )
        contents = TextResourceContents(text=text, mime_type=mime_type, uri=uri)
        content = EmbeddedResourceContentBlock(annotations=annotations, resource=contents)
        return cls(content=content)

    @classmethod
    def embedded_blob_resource(
        cls,
        data: bytes | str,
        uri: str,
        mime_type: str | None = None,
        audience: Audience | None = None,
        last_modified: datetime | str | None = None,
        priority: float | None = None,
    ) -> Self:
        """Create a ToolCallContent containing an embedded blob resource.

        Args:
            data: The data to embed.
            uri: The URI of the resource.
            mime_type: The MIME type of the resource.
            audience: The audience to apply to the resource.
            last_modified: The last modified date of the resource.
            priority: The priority of the resource.
        """
        if isinstance(data, bytes):
            data = base64.b64encode(data).decode()
        annotations = Annotations.optionally_create(
            audience=audience,
            last_modified=last_modified,
            priority=priority,
        )
        resource = BlobResourceContents(blob=data, mime_type=mime_type, uri=uri)
        content = EmbeddedResourceContentBlock(annotations=annotations, resource=resource)
        return cls(content=content)


class ToolCallLocation(AnnotatedObject):
    """A file location being accessed or modified by a tool.

    Enables clients to implement "follow-along" features that track
    which files the agent is working with in real-time.
    See protocol docs: [Following the Agent](https://agentclientprotocol.com/protocol/tool-calls#following-the-agent)
    """

    line: int | None = Field(default=0, ge=0)
    """Optional 1-based line number within the file.

    Defaults to 0 (unspecified) rather than None: some clients only render
    clickable paths when the field is present.
    """

    path: str
    """The absolute file path being accessed or modified."""


class DeniedOutcome(Schema):
    """The prompt turn was cancelled before the user responded.

    When a client sends a `session/cancel` notification to cancel an ongoing
    prompt turn, it MUST respond to all pending `session/request_permission`
    requests with this `Cancelled` outcome.
    See protocol docs: [Cancellation](https://agentclientprotocol.com/protocol/prompt-turn#cancellation)
    """

    outcome: Literal["cancelled"] = Field(default="cancelled", init=False)


class AllowedOutcome(AnnotatedObject):
    """The user selected one of the provided options."""

    option_id: str
    """The ID of the option the user selected."""

    outcome: Literal["selected"] = Field(default="selected", init=False)


class PermissionOption(AnnotatedObject):
    """An option presented to the user when requesting permission."""

    kind: PermissionKind
    """Hint about the nature of this permission option."""

    name: str
    """Human-readable label to display to the user."""

    option_id: str
    """Unique identifier for this permission option."""


class SessionReferenceToolCallContent(AnnotatedObject):
    """**UNSTABLE**: Reference to another session, e.g. a subagent spawned by this tool call."""

    type: Literal["session"] = Field(default="session", init=False)
    """Session reference tool call content."""

    session_id: str
    """The referenced session."""


ToolCallContent = (
    ContentToolCallContent
    | FileEditToolCallContent
    | TerminalToolCallContent
    | SessionReferenceToolCallContent
)
