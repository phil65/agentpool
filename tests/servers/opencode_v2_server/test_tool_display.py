"""Display mapping of tool calls onto OpenCode's tool renderers."""

from typing import TYPE_CHECKING, Any

from agentpool_server.opencode_v2_server.tool_display import (
    ORIGINAL_TOOL_KEY,
    display_input,
    display_name,
)

from .conftest import create_session, messages, prompt_and_wait


if TYPE_CHECKING:
    from httpx2 import AsyncClient


def test_known_tools_map_to_opencode_renderers() -> None:
    assert display_name("bash") == "shell"
    assert display_name("Bash") == "shell"
    assert display_name("list_directory") == "list"
    assert display_input("Edit", {"file_path": "a.py", "old_string": "x", "new_string": "y"}) == {
        "path": "a.py",
        "oldString": "x",
        "newString": "y",
    }
    # Unmapped arguments are kept.
    assert display_input("read", {"path": "a.py", "line": 3, "encoding": "utf-8"}) == {
        "path": "a.py",
        "offset": 3,
        "encoding": "utf-8",
    }


def test_unknown_tools_are_unchanged() -> None:
    assert display_name("my_tool") == "my_tool"
    args = {"a": 1}
    assert display_input("my_tool", args) is args


async def test_tool_call_is_shown_with_opencode_name(client: AsyncClient, pool: Any) -> None:
    def list_directory(path: str) -> str:
        """List a directory."""
        return f"listing of {path}"

    pool.all_agents["other"].tools.register_tool(list_directory)
    sid = await create_session(client, agent="other")
    await prompt_and_wait(client, sid, "list")
    assistant = next(m for m in await messages(client, sid) if m["type"] == "assistant")
    tool = assistant["content"][0]
    assert tool["name"] == "list"
    assert set(tool["state"]["input"]) == {"path"}
    assert tool["state"]["metadata"][ORIGINAL_TOOL_KEY] == "list_directory"
