"""Map AgentPool / Claude Code / Codex tool calls onto OpenCode's tool renderers.

OpenCode clients render tool calls by tool name (``shell``, ``read``, ``edit``, ``grep``, ...)
and read OpenCode's argument names (``path``, ``oldString``, ``include``, ...). Calls are
translated for display only: the agent's own history keeps the original names and arguments,
and the original tool name is kept in the tool metadata (``agentpoolTool``).

Unknown tools keep their name and arguments and use OpenCode's generic renderer.
"""

from dataclasses import dataclass, field
from typing import Any


ORIGINAL_TOOL_KEY = "agentpoolTool"
"""Tool metadata key holding the agent's own tool name."""


@dataclass(frozen=True)
class ToolDisplay:
    """How one tool is presented to OpenCode clients."""

    name: str
    """OpenCode tool (renderer) name."""
    rename: dict[str, str] = field(default_factory=dict)
    """Argument renames (agent argument -> OpenCode argument)."""


_EDIT = {"old_string": "oldString", "new_string": "newString"}
_FILE = {"file_path": "path"}

TOOL_DISPLAYS: dict[str, ToolDisplay] = {
    # AgentPool tools
    "bash": ToolDisplay("shell"),
    "sandbox_bash": ToolDisplay("shell"),
    "execute_code": ToolDisplay("execute"),
    "read": ToolDisplay("read", {"line": "offset"}),
    "write": ToolDisplay("write"),
    "edit": ToolDisplay("edit", _EDIT),
    "grep": ToolDisplay("grep", {"file_pattern": "include"}),
    "list_directory": ToolDisplay("list"),
    "load_skill": ToolDisplay("skill", {"skill_name": "name"}),
    "task": ToolDisplay("subagent", {"agent_or_team": "agent"}),
    # Claude Code tools
    "Bash": ToolDisplay("shell"),
    "Read": ToolDisplay("read", _FILE),
    "Write": ToolDisplay("write", _FILE),
    "Edit": ToolDisplay("edit", _FILE | _EDIT),
    "Glob": ToolDisplay("glob"),
    "Grep": ToolDisplay("grep", {"glob": "include"}),
    "LS": ToolDisplay("list"),
    "WebFetch": ToolDisplay("webfetch"),
    "WebSearch": ToolDisplay("websearch"),
    "Task": ToolDisplay("subagent", {"subagent_type": "agent"}),
    "Agent": ToolDisplay("subagent", {"subagent_type": "agent"}),
    "Skill": ToolDisplay("skill", {"skill": "name"}),
    # Codex tools
    "web_search": ToolDisplay("websearch"),
}


def display_name(tool_name: str) -> str:
    """OpenCode tool name for an agent tool."""
    display = TOOL_DISPLAYS.get(tool_name)
    return display.name if display is not None else tool_name


def display_input(tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    """Tool arguments with OpenCode argument names (unmapped arguments are kept)."""
    display = TOOL_DISPLAYS.get(tool_name)
    if display is None or not display.rename:
        return tool_input
    return {display.rename.get(key, key): value for key, value in tool_input.items()}
