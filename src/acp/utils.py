"""Utility functions for ACP (Agent Client Protocol)."""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING, Any

from pydantic_ai import (
    AudioUrl,
    BinaryContent,
    DocumentUrl,
    ImageUrl,
    TextContent,
    ToolReturn,
    VideoUrl,
)

from acp.schema import (
    AudioContentBlock,
    BlobResourceContents,
    EmbeddedResourceContentBlock,
    ImageContentBlock,
    PermissionOption,
    ResourceContentBlock,
    TextContentBlock,
)


if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from pydantic_ai import UserContent
    from pydantic_ai.messages import ToolReturnContent

    from acp.schema import ContentBlock, ToolCallKind


DEFAULT_PERMISSION_OPTIONS = [
    PermissionOption(option_id="allow_once", name="Allow Once", kind="allow_once"),
    PermissionOption(option_id="deny_once", name="Deny Once", kind="reject_once"),
    PermissionOption(option_id="allow_always", name="Always Allow", kind="allow_always"),
    PermissionOption(option_id="deny_always", name="Always Deny", kind="reject_always"),
]


def to_acp_content_blocks(  # noqa: PLR0911
    tool_output: (
        ToolReturn
        | list[ToolReturn]
        | ToolReturnContent
        | UserContent
        | Sequence[UserContent]
        | Mapping[str, Any]
        | None
    ),
) -> list[ContentBlock]:
    """Convert pydantic-ai tool output to raw ACP content blocks.

    Returns unwrapped content blocks that can be used directly or wrapped
    in ContentToolCallContent as needed.

    Args:
        tool_output: Output from pydantic-ai tool execution

    Returns:
        List of ContentBlock objects
    """
    match tool_output:
        case None:
            return []

        case ToolReturn(return_value=return_value, content=content):
            result_blocks: list[ContentBlock] = []
            if return_value is not None:
                result_blocks.append(TextContentBlock(text=str(return_value)))
            if content:
                content_list = content if isinstance(content, list) else [content]
                for content_item in content_list:
                    result_blocks.extend(to_acp_content_blocks(content_item))
            return result_blocks

        case list() as items:
            list_blocks: list[ContentBlock] = []
            for item in items:
                list_blocks.extend(to_acp_content_blocks(item))
            return list_blocks

        case BinaryContent(data=data, media_type=media_type) if tool_output.is_image:
            image_data = base64.b64encode(data).decode()
            return [ImageContentBlock(data=image_data, mime_type=media_type)]

        case BinaryContent(data=data, media_type=media_type) if tool_output.is_audio:
            audio_data = base64.b64encode(data).decode()
            return [AudioContentBlock(data=audio_data, mime_type=media_type)]

        case BinaryContent(data=data, media_type=media_type):
            blob_data = base64.b64encode(data).decode()
            uri = f"data:{media_type};base64,{blob_data[:50]}..."
            blob_resource = BlobResourceContents(blob=blob_data, mime_type=media_type, uri=uri)
            return [EmbeddedResourceContentBlock(resource=blob_resource)]

        case ImageUrl() | AudioUrl() | VideoUrl() | DocumentUrl() as file_url:
            from urllib.parse import urlparse

            parsed = urlparse(str(file_url.url))
            resource_type = file_url.kind.replace("-url", "")
            name = parsed.path.split("/")[-1] if parsed.path else resource_type
            if not name or name == "/":
                name = f"{resource_type}_{parsed.netloc}" if parsed.netloc else resource_type
            return [
                ResourceContentBlock(
                    uri=file_url.url,
                    name=name,
                    description=f"{resource_type.title()} resource",
                    mime_type=file_url.media_type,
                )
            ]

        case str(content) | TextContent(content=content):
            return [TextContentBlock(text=content)]
        # case UploadedFile() | CachePoint():
        case _:
            return [TextContentBlock(text=str(tool_output))]


def generate_tool_title(tool_name: str, tool_input: dict[str, Any]) -> str:  # noqa: PLR0911
    """Generate a descriptive title for a tool call based on name and inputs.

    Args:
        tool_name: Name of the tool being called
        tool_input: Input parameters passed to the tool

    Returns:
        Human-readable title describing what the tool is doing
    """
    name_lower = tool_name.lower()

    # Exact matches for common tool names (OpenCode compatibility)
    if name_lower == "read" and (path := tool_input.get("path") or tool_input.get("file_path")):
        return f"Reading: {path}"
    if name_lower in ("write", "edit") and (
        path := tool_input.get("path") or tool_input.get("file_path")
    ):
        return f"Editing: {path}"

    # Command/script execution - show the command
    if any(k in name_lower for k in ["command", "execute", "run", "shell", "script", "bash"]) and (
        cmd := tool_input.get("command")
    ):
        # Truncate long commands
        cmd_str = str(cmd)
        if len(cmd_str) > 60:  # noqa: PLR2004
            cmd_str = cmd_str[:57] + "..."
        return f"Running: {cmd_str}"

    # File reading
    if (
        any(k in name_lower for k in ["read", "load", "get"])
        and "file" in name_lower
        and (path := tool_input.get("path") or tool_input.get("file_path"))
    ):
        return f"Reading: {path}"

    # File writing/editing
    if any(k in name_lower for k in ["write", "save", "edit", "modify"]) and (
        path := tool_input.get("path") or tool_input.get("file_path")
    ):
        return f"Editing: {path}"

    # File deletion
    if any(k in name_lower for k in ["delete", "remove"]) and (
        path := tool_input.get("path") or tool_input.get("file_path")
    ):
        return f"Deleting: {path}"

    # Directory listing
    if (
        "list" in name_lower
        and any(k in name_lower for k in ["dir", "folder", "path"])
        and (path := tool_input.get("path") or tool_input.get("directory"))
    ):
        return f"Listing: {path}"

    # Search operations
    if any(k in name_lower for k in ["search", "find", "grep", "query"]) and (
        query := tool_input.get("query") or tool_input.get("pattern") or tool_input.get("regex")
    ):
        query_str = str(query)
        if len(query_str) > 40:  # noqa: PLR2004
            query_str = query_str[:37] + "..."
        return f"Searching: {query_str}"

    # Fetch/download
    if any(k in name_lower for k in ["fetch", "download", "request"]) and (
        url := tool_input.get("url")
    ):
        url_str = str(url)
        if len(url_str) > 50:  # noqa: PLR2004
            url_str = url_str[:47] + "..."
        return f"Fetching: {url_str}"

    # Process management
    if "process" in name_lower:
        if "start" in name_lower and (cmd := tool_input.get("command")):
            return f"Starting process: {cmd}"
        if "kill" in name_lower and (pid := tool_input.get("process_id")):
            return f"Killing process: {pid}"
        if "output" in name_lower and (pid := tool_input.get("process_id")):
            return f"Getting output: {pid}"

    # Code execution
    if "code" in name_lower and any(k in name_lower for k in ["execute", "run", "eval"]):
        if lang := tool_input.get("language"):
            return f"Executing {lang} code"
        return "Executing code"

    # Default: try to find any path-like or meaningful parameter
    for key in ["path", "file_path", "filepath", "filename", "name", "url", "command"]:
        if value := tool_input.get(key):
            value_str = str(value)
            if len(value_str) > 50:  # noqa: PLR2004
                value_str = value_str[:47] + "..."
            return f"{tool_name}: {value_str}"

    # Final fallback
    return f"Calling {tool_name}"


def infer_tool_kind(tool_name: str) -> ToolCallKind:  # noqa: PLR0911
    """Determine the appropriate tool kind based on name.

    Simple substring matching for tool kind inference.

    Args:
        tool_name: Name of the tool

    Returns:
        Tool kind string for ACP protocol
    """
    name_lower = tool_name.lower()

    # Exact matches for common tool names (OpenCode compatibility)
    if name_lower in ("read", "write", "edit"):
        return "read" if name_lower == "read" else "edit"

    if any(i in name_lower for i in ["read", "load", "get"]) and any(
        i in name_lower for i in ["file", "path", "content"]
    ):
        return "read"
    if any(i in name_lower for i in ["write", "save", "edit", "modify", "update"]) and any(
        i in name_lower for i in ["file", "path", "content"]
    ):
        return "edit"
    if any(i in name_lower for i in ["delete", "remove", "rm"]):
        return "delete"
    if any(i in name_lower for i in ["move", "rename", "mv"]):
        return "move"
    if any(i in name_lower for i in ["search", "find", "query", "lookup"]):
        return "search"
    if any(i in name_lower for i in ["execute", "run", "exec", "command", "shell", "bash"]):
        return "execute"
    if any(i in name_lower for i in ["think", "plan", "reason", "analyze"]):
        return "think"
    if any(i in name_lower for i in ["fetch", "download", "request"]):
        return "fetch"
    return "other"  # Default to other
