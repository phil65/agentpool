"""Message lookups in the Claude storage provider without a session hint."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import anyenv
import pytest

from agentpool_config.storage import ClaudeStorageConfig
from agentpool_storage.claude_provider.provider import ClaudeStorageProvider


if TYPE_CHECKING:
    from pathlib import Path


def _entry(kind: str, uuid: str, parent: str | None, session_id: str, text: str) -> dict[str, Any]:
    return {
        "type": kind,
        "uuid": uuid,
        "parentUuid": parent,
        "sessionId": session_id,
        "timestamp": "2026-01-01T00:00:00.000Z",
        "cwd": "/tmp/project",
        "version": "2.0.0",
        "isSidechain": False,
        "userType": "external",
        "message": (
            {"role": "user", "content": text}
            if kind == "user"
            else {
                "id": f"msg_{uuid}",
                "type": "message",
                "role": "assistant",
                "model": "claude-opus-5-5",
                "content": [{"type": "text", "text": text}],
            }
        ),
    }


def _write(project_dir: Path, session_id: str, entries: list[dict[str, Any]]) -> None:
    lines = [anyenv.dump_json(e) for e in entries]
    (project_dir / f"{session_id}.jsonl").write_text("\n".join(lines) + "\n")


@pytest.fixture
def provider(tmp_path: Path) -> ClaudeStorageProvider:
    project = tmp_path / "projects" / "-tmp-project"
    project.mkdir(parents=True)
    # Session A: u1 -> a1. Session B continues from a1: u2 -> a2.
    _write(
        project,
        "session-a",
        [
            _entry("user", "u1", None, "session-a", "first question"),
            _entry("assistant", "a1", "u1", "session-a", "first answer"),
        ],
    )
    _write(
        project,
        "session-b",
        [
            _entry("user", "u2", "a1", "session-b", "follow-up"),
            _entry("assistant", "a2", "u2", "session-b", "second answer"),
        ],
    )
    # An unrelated session and an empty file must not break the search.
    _write(project, "session-c", [_entry("user", "u9", None, "session-c", "unrelated")])
    (project / "empty.jsonl").write_text("")
    return ClaudeStorageProvider(ClaudeStorageConfig(path=str(tmp_path)))


async def test_get_message_without_session_hint(provider: ClaudeStorageProvider) -> None:
    msg = await provider.get_message("a2")
    assert msg is not None
    assert msg.message_id == "a2"
    assert await provider.get_message("does-not-exist") is None


async def test_ancestry_across_sessions(provider: ClaudeStorageProvider) -> None:
    ancestry = await provider.get_message_ancestry("a2")
    assert [m.message_id for m in ancestry] == ["u1", "a1", "u2", "a2"]


async def test_ancestry_stops_on_cycle(tmp_path: Path) -> None:
    project = tmp_path / "projects" / "-tmp-project"
    project.mkdir(parents=True)
    _write(
        project,
        "cyclic",
        [
            _entry("user", "x1", "x2", "cyclic", "hi"),
            _entry("assistant", "x2", "x1", "cyclic", "hello"),
        ],
    )
    provider = ClaudeStorageProvider(ClaudeStorageConfig(path=str(tmp_path)))
    ancestry = await provider.get_message_ancestry("x2")
    assert [m.message_id for m in ancestry] == ["x1", "x2"]


@pytest.mark.parametrize("with_session_hint", [True, False])
async def test_ancestry_runs_through_attachments(tmp_path: Path, with_session_hint: bool) -> None:
    """Real parent chains pass attachment entries; the walk must not stop there."""
    project = tmp_path / "projects" / "-tmp-project"
    project.mkdir(parents=True)
    attachment = {
        "type": "attachment",
        "uuid": "at1",
        "parentUuid": "u1",
        "sessionId": "with-attachments",
        "timestamp": "2026-01-01T00:00:00.000Z",
        "cwd": "/tmp/project",
        "version": "2.0.0",
        "isSidechain": False,
        "userType": "external",
        "attachment": {"type": "date", "date": "2026-01-01"},
    }
    _write(
        project,
        "with-attachments",
        [
            _entry("user", "u1", None, "with-attachments", "question"),
            attachment,
            _entry("assistant", "a1", "at1", "with-attachments", "answer"),
        ],
    )
    provider = ClaudeStorageProvider(ClaudeStorageConfig(path=str(tmp_path)))
    session_id = "with-attachments" if with_session_hint else None
    ancestry = await provider.get_message_ancestry("a1", session_id=session_id)
    assert [m.message_id for m in ancestry] == ["u1", "a1"]


async def test_session_files_are_parsed_off_the_event_loop(
    provider: ClaudeStorageProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Parsing every session file must not block the server's other clients."""
    import threading

    from agentpool_config.session import SessionQuery
    from agentpool_storage.claude_provider import provider as module
    from agentpool_storage.models import QueryFilters

    parse = module._parse_session_full
    threads: set[int] = set()

    def recording(*args: Any) -> Any:
        threads.add(threading.get_ident())
        return parse(*args)

    monkeypatch.setattr(module, "_parse_session_full", recording)
    assert await provider.filter_messages(SessionQuery())
    assert await provider.get_sessions(QueryFilters())
    assert threads
    assert threading.get_ident() not in threads
