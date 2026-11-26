"""Claude storage safety: transcripts are never wiped, session ids can't escape."""

from typing import TYPE_CHECKING

import pytest

from agentpool.messaging import ChatMessage
from agentpool_config.storage import ClaudeStorageConfig
from agentpool_storage.claude_provider.provider import ClaudeStorageProvider


if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def provider(tmp_path: Path) -> ClaudeStorageProvider:
    return ClaudeStorageProvider(ClaudeStorageConfig(path=str(tmp_path / ".claude")))


async def _log(provider: ClaudeStorageProvider, session_id: str) -> None:
    msg = ChatMessage[str](content="hi", role="user", session_id=session_id)
    await provider.log_message(message=msg)


def _transcripts(root: Path) -> list[Path]:
    return sorted(root.rglob("*.jsonl"))


@pytest.mark.parametrize(
    ("agent_name", "hard"), [(None, False), (None, True), ("x", False), ("x", True)]
)
async def test_claude_reset_keeps_transcripts(
    provider: ClaudeStorageProvider, tmp_path: Path, agent_name: str | None, hard: bool
):
    await _log(provider, "/w/a:s1")
    await _log(provider, "/w/b:s2")
    assert len(_transcripts(tmp_path)) == 2
    assert await provider.reset(agent_name=agent_name, hard=hard) == (0, 0)
    assert len(_transcripts(tmp_path)) == 2


@pytest.mark.parametrize(
    "session_id",
    ["../../../../OUTSIDE", "a/b", "..", "", "a\\b", "/abs"],
)
async def test_claude_rejects_unsafe_session_ids(
    provider: ClaudeStorageProvider, tmp_path: Path, session_id: str
):
    with pytest.raises(ValueError, match="Invalid Claude session id"):
        await _log(provider, f"/w/a:{session_id}")
    assert not list(tmp_path.parent.glob("OUTSIDE*"))
    assert _transcripts(tmp_path) == []
    assert provider._find_session_path(session_id) is None


async def test_claude_find_session_path_no_traversal(
    provider: ClaudeStorageProvider, tmp_path: Path
):
    (tmp_path / "OUTSIDE.jsonl").write_text("{}\n")
    (provider.projects_path / "proj").mkdir()
    assert provider._find_session_path("../../OUTSIDE") is None


async def test_claude_fork_rejects_unsafe_new_session_id(provider: ClaudeStorageProvider):
    await _log(provider, "/w/a:src")
    with pytest.raises(ValueError, match="Invalid Claude session id"):
        await provider.fork_conversation(source_session_id="src", new_session_id="../../evil")
    fork_point = await provider.fork_conversation(source_session_id="src", new_session_id="ok")
    assert fork_point is not None
    assert provider._find_session_path("ok") is not None
