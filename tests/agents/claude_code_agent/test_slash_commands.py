"""The CLI's slash commands, including the commands of MCP server prompts."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from clawd_code_sdk.models import UserTextPrompt, message_adapter
from clawd_code_sdk.models.server_info import ClaudeCodeCommandInfo
import pytest

from agentpool.agents.claude_code_agent import ClaudeCodeAgent
from agentpool.agents.claude_code_agent.converters import to_prompt_input
from agentpool.agents.claude_code_agent.slash_commands import (
    command_name,
    create_claude_code_command,
    to_cli_command_text,
)
from agentpool.agents.claude_code_agent.stream_adapter import ClaudeCodeStreamedResponse


if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def _info(name: str) -> ClaudeCodeCommandInfo:
    return ClaudeCodeCommandInfo(name=name, description=f"{name} command", argument_hint="")


@pytest.mark.parametrize(
    ("cli_name", "name"),
    [("compact", "compact"), ("testsrv:dynamic_prompt (MCP)", "mcp:testsrv:dynamic_prompt")],
)
def test_command_name(cli_name: str, name: str) -> None:
    assert command_name(cli_name) == name
    assert create_claude_code_command(_info(cli_name)).name == name


@pytest.mark.parametrize(
    ("text", "cli_text"),
    [
        ("/mcp:testsrv:static_prompt", "/testsrv:static_prompt (MCP)"),
        (
            "/mcp:testsrv:dynamic_prompt hello\\nworld",
            "/testsrv:dynamic_prompt (MCP) hello\\nworld",
        ),
        ("/compact", "/compact"),
        ("explain /mcp:testsrv:static_prompt", "explain /mcp:testsrv:static_prompt"),
    ],
)
def test_cli_command_text(text: str, cli_text: str) -> None:
    assert to_cli_command_text(text) == cli_text


def test_prompt_rewrites_only_the_first_text() -> None:
    prompts = list(to_prompt_input(["/mcp:testsrv:static_prompt", "/mcp:testsrv:static_prompt"]))
    assert prompts == [
        UserTextPrompt(text="/testsrv:static_prompt (MCP)"),
        UserTextPrompt(text="/mcp:testsrv:static_prompt"),
    ]


def test_cli_commands_follow_the_cli() -> None:
    agent = ClaudeCodeAgent(name="cc")
    agent._register_cli_commands([_info("compact"), _info("login")])
    # An MCP server connected: its prompts are offered; one CLI command went away
    agent._register_cli_commands([_info("testsrv:static_prompt (MCP)")])

    names = {command.name for command in agent.command_store.list_commands()}
    assert "mcp:testsrv:static_prompt" in names
    assert "compact" not in names
    assert "login" not in names  # Never offered: it does not work through the SDK


async def test_adapter_reports_changed_commands() -> None:
    frame = message_adapter.validate_python({
        "type": "system",
        "subtype": "commands_changed",
        "uuid": "u",
        "session_id": "s",
        "commands": [
            {"name": "testsrv:static_prompt (MCP)", "description": "", "argumentHint": ""}
        ],
    })
    seen: list[list[str]] = []

    async def source() -> AsyncIterator[Any]:
        yield frame

    response = ClaudeCodeStreamedResponse(
        provider_name="claude_code",
        stream=source(),
        tool_metadata={},
        agent_name="cc",
        session_id="local",
        on_commands_changed=lambda commands: seen.append([c.name for c in commands]),
    )
    assert [event async for event in response._get_event_iterator()] == []
    assert seen == [["testsrv:static_prompt (MCP)"]]


async def test_agent_follows_the_cli_to_a_new_session() -> None:
    # /clear moves the CLI to a new session; a reconnect must not resume the cleared one
    from agentpool.storage.manager import StorageManager
    from agentpool_storage.memory_provider.provider import MemoryStorageProvider

    storage = StorageManager(providers=[MemoryStorageProvider()])
    await storage.log_session(session_id="s1", node_name="cc")
    agent = ClaudeCodeAgent(name="cc", session_id="c3a5972f")
    agent._storage = storage  # Normally the pool's
    agent.session_id = "s1"

    await agent._follow_sdk_session("51c42cb1")

    assert agent._sdk_session_id == "51c42cb1"
    session = await storage.load_session("s1")
    assert session is not None
    assert session.sdk_session_id == "51c42cb1"
