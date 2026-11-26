"""AgentPool event helpers."""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from agentpool.agents.events import DiffContentItem, LocationContentItem


if TYPE_CHECKING:
    from clawd_code_sdk.models import ToolInput

    from agentpool.agents.events import ToolCallContentItem
    from agentpool.tools.base import ToolKind


@dataclass
class RichToolInfo:
    """Rich display information derived from tool name and input."""

    title: str
    """Human-readable title for the tool call."""
    kind: ToolKind = "other"
    """Category of tool operation."""
    locations: list[LocationContentItem] = field(default_factory=list)
    """File locations involved in the operation."""
    content: list[ToolCallContentItem] = field(default_factory=list)
    """Rich content items (diffs, text, etc.)."""


def derive_rich_tool_info(name: str, input_data: ToolInput | dict[str, Any]) -> RichToolInfo:  # noqa: PLR0911, PLR0915
    """Derive rich display info from tool name and input arguments.

    Maps MCP tool names and their inputs to human-readable titles, kinds,
    and location information for rich UI display. Handles both Claude Code
    built-in tools and MCP bridge tools.

    Args:
        name: The tool name (e.g., "Read", "mcp__server__read")
        input_data: The tool input arguments

    Returns:
        RichToolInfo with derived display information
    """
    # Extract the actual tool name if it's an MCP bridge tool
    # Format: mcp__{server_name}__{tool_name}
    actual_name = name
    if name.startswith("mcp__") and "__" in name[5:]:
        parts = name.split("__")
        if len(parts) >= 3:  # noqa: PLR2004
            actual_name = parts[-1]  # Get the last part (actual tool name)

    # Normalize to lowercase for matching
    tool_lower = actual_name.lower()
    # Read operations
    if tool_lower in ("read", "read_file"):
        path = input_data.get("file_path") or input_data.get("path", "")
        offset = input_data.get("offset") or input_data.get("line")
        assert offset is None or isinstance(offset, int)
        suffix = ""
        if limit := input_data.get("limit"):
            assert isinstance(limit, int)
            start = (offset or 0) + 1
            end = (offset or 0) + limit
            suffix = f" ({start}-{end})"
        elif offset:
            suffix = f" (from line {offset + 1})"
        title = f"Read {path}{suffix}" if path else "Read File"
        locations = [LocationContentItem(path=path, line=offset or 0)] if path else []  # type: ignore[arg-type]
        return RichToolInfo(title=title, kind="read", locations=locations)

    # Write operations
    if tool_lower in ("write", "write_file"):
        path = input_data.get("file_path") or input_data.get("path", "")
        content = input_data.get("content", "")
        assert isinstance(path, str)
        assert isinstance(content, str)
        return RichToolInfo(
            title=f"Write {path}" if path else "Write File",
            kind="edit",
            locations=[LocationContentItem(path=path)] if path else [],
            content=[DiffContentItem(path=path, old_text=None, new_text=content)] if path else [],
        )
    # Edit operations
    if tool_lower in ("edit", "edit_file"):
        path = input_data.get("file_path") or input_data.get("path", "")
        old_string = input_data.get("old_string") or input_data.get("old_text", "")
        new_string = input_data.get("new_string") or input_data.get("new_text", "")
        assert isinstance(path, str)
        assert isinstance(old_string, str)
        assert isinstance(new_string, str)
        return RichToolInfo(
            title=f"Edit {path}" if path else "Edit File",
            kind="edit",
            locations=[LocationContentItem(path=path)] if path else [],
            content=[DiffContentItem(path=path, old_text=old_string, new_text=new_string)]
            if path
            else [],
        )
    # Delete operations
    if tool_lower in ("delete", "delete_path", "delete_file"):
        path = input_data.get("file_path") or input_data.get("path", "")
        assert isinstance(path, str)
        locations = [LocationContentItem(path=path)] if path else []
        title = f"Delete {path}" if path else "Delete"
        return RichToolInfo(title=title, kind="delete", locations=locations)
    # Bash/terminal operations
    if tool_lower in ("bash", "execute", "run_command", "execute_command", "execute_code"):
        command = input_data.get("command") or input_data.get("code", "")
        # Escape backticks in command
        assert isinstance(command, str)
        escaped_cmd = command.replace("`", "\\`") if command else ""
        title = f"`{escaped_cmd}`" if escaped_cmd else "Terminal"
        return RichToolInfo(title=title, kind="execute")
    # Search operations
    if tool_lower in ("grep", "search", "glob", "find"):
        pattern = input_data.get("pattern") or input_data.get("query", "")
        path = input_data.get("path", "")
        title = f"Search for '{pattern}'" if pattern else "Search"
        if path:
            title += f" in {path}"
        assert isinstance(path, str)
        locations = [LocationContentItem(path=path)] if path else []
        return RichToolInfo(title=title, kind="search", locations=locations)
    # List directory
    if tool_lower in ("ls", "list", "list_directory"):
        path = input_data.get("path", ".")
        title = f"List {path}" if path != "." else "List current directory"
        assert isinstance(path, str)
        locations = [LocationContentItem(path=path)] if path else []
        return RichToolInfo(title=title, kind="search", locations=locations)
    # Web operations
    if tool_lower in ("webfetch", "web_fetch", "fetch"):
        url = input_data.get("url", "")
        return RichToolInfo(title=f"Fetch {url}" if url else "Web Fetch", kind="fetch")
    if tool_lower in ("websearch", "web_search", "search_web"):
        query = input_data.get("query", "")
        return RichToolInfo(title=f"Search: {query}" if query else "Web Search", kind="fetch")
    # Task/subagent operations
    if tool_lower == "task":
        description = input_data.get("description", "")
        assert isinstance(description, str)
        return RichToolInfo(title=description if description else "Task", kind="think")
    # Notebook operations
    if tool_lower in ("notebookread", "notebook_read"):
        path = input_data.get("notebook_path", "")
        title = f"Read Notebook {path}" if path else "Read Notebook"
        assert isinstance(path, str)
        locations = [LocationContentItem(path=path)] if path else []
        return RichToolInfo(title=title, kind="read", locations=locations)
    if tool_lower in ("notebookedit", "notebook_edit"):
        path = input_data.get("notebook_path", "")
        title = f"Edit Notebook {path}" if path else "Edit Notebook"
        assert isinstance(path, str)
        locations = [LocationContentItem(path=path)] if path else []
        return RichToolInfo(title=title, kind="edit", locations=locations)
    # Default: use the tool name as title
    return RichToolInfo(title=actual_name, kind="other")
