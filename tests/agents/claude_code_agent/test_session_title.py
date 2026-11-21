"""Claude Code sessions are titled through the CLI's title model."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from clawd_code_sdk._errors import ControlRequestError
import pytest

from agentpool.agents.claude_code_agent import ClaudeCodeAgent
from agentpool.storage.manager import StorageManager
from agentpool_storage.memory_provider.provider import MemoryStorageProvider


if TYPE_CHECKING:
    from agentpool.storage.manager import SessionMetadataGeneratedEvent


class FakeClient:
    """The title request of ClaudeSDKClient."""

    def __init__(self, title: str | Exception | None) -> None:
        self.title = title
        self.descriptions: list[str] = []

    async def generate_session_title(
        self, description: str, *, persist: bool = False
    ) -> str | None:
        self.descriptions.append(description)
        if isinstance(self.title, Exception):
            raise self.title
        return self.title


async def _title(
    prompt: str, client: FakeClient, *, existing: str | None = None
) -> tuple[str | None, list[str]]:
    """The title the session got, and the titles announced to servers."""
    storage = StorageManager(providers=[MemoryStorageProvider()])
    await storage.log_session(session_id="s1", node_name="cc")
    if existing:
        await storage.update_session_title("s1", existing)
    announced: list[str] = []

    async def on_title(event: SessionMetadataGeneratedEvent) -> None:
        announced.append(event.metadata.title)

    storage.metadata_generated.connect(on_title)
    agent = ClaudeCodeAgent(name="cc")
    agent._storage = storage  # Normally the pool's
    await agent._title_session(client, "s1", [prompt])  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]
    return await storage.get_session_title("s1"), announced


async def test_cli_title_is_stored_and_announced() -> None:
    client = FakeClient("ACP subprocess tests hanging on Windows CI")
    prompt = "Our CI fails on Windows because the ACP subprocess tests hang. Find out why."
    title, announced = await _title(prompt, client)
    assert title == announced[0] == "ACP subprocess tests hanging on Windows CI"
    assert client.descriptions == [f"User: {prompt}"]


@pytest.mark.parametrize(
    "failure",
    [None, ControlRequestError("unknown subtype")],
    ids=["too short", "request failed"],
)
async def test_prompt_is_the_title_without_a_cli_title(failure: Any) -> None:
    title, announced = await _title("fix   the\nbuild", FakeClient(failure))
    assert title == announced[0] == "fix the build"


async def test_long_prompt_without_a_cli_title_is_cut() -> None:
    title, _ = await _title("word " * 30, FakeClient(None))
    assert title is not None
    assert len(title) == 60
    assert title.endswith("…")


async def test_existing_title_is_kept() -> None:
    client = FakeClient("Something else")
    title, announced = await _title("rename me", client, existing="My title")
    assert (title, announced, client.descriptions) == ("My title", [], [])


def test_claude_code_titles_its_own_sessions() -> None:
    assert ClaudeCodeAgent.TITLES_OWN_SESSIONS
