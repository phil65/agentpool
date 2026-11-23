"""V2 turns are stored under their prompt's ID: revert, fork and diffs find them by it."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentpool.utils.file_ops_tracker import FileOpsTracker
from agentpool_server.opencode_v2_server.session_ops import changes_of_turns, history_before

from .conftest import create_session, messages, prompt_and_wait


if TYPE_CHECKING:
    from httpx2 import AsyncClient

    from agentpool_server.opencode_v2_server.state import ServerState


async def _user_ids(client: AsyncClient, session_id: str) -> list[str]:
    return [m["id"] for m in await messages(client, session_id) if m["type"] == "user"]


async def test_turns_are_stored_under_their_prompts_id(
    client: AsyncClient, state: ServerState
) -> None:
    sid = await create_session(client, agent="helper")
    await prompt_and_wait(client, sid, "first")
    await prompt_and_wait(client, sid, "second")
    stored = await state.storage.get_session_messages(sid)
    assert [m.parent_id for m in stored] == await _user_ids(client, sid)


async def test_revert_truncates_the_stored_history(client: AsyncClient, state: ServerState) -> None:
    """The agent forgets the reverted turns (before, the stored history was kept whole)."""
    sid = await create_session(client, agent="helper")
    await prompt_and_wait(client, sid, "first")
    await prompt_and_wait(client, sid, "second")
    first, second = await _user_ids(client, sid)
    await client.post(f"/api/session/{sid}/revert/stage", json={"messageID": second})
    assert (await client.post(f"/api/session/{sid}/revert/commit")).status_code == 204
    stored = await state.storage.get_session_messages(sid)
    assert [m.parent_id for m in stored] == [first]


async def test_fork_copies_the_stored_history_of_the_kept_turns(
    client: AsyncClient, state: ServerState
) -> None:
    sid = await create_session(client, agent="helper")
    await prompt_and_wait(client, sid, "first")
    await prompt_and_wait(client, sid, "second")
    _first, second = await _user_ids(client, sid)
    response = await client.post(f"/api/session/{sid}/fork", json={"before": second})
    fork_id = response.json()["data"]["id"]
    assert len(await state.storage.get_session_messages(fork_id)) == 1


def test_a_history_stored_without_prompt_ids_is_cut_by_turns() -> None:
    from agentpool.messaging import ChatMessage

    answers: list[ChatMessage[Any]] = [
        ChatMessage(content=f"answer {i}", role="assistant", parent_id=f"legacy-{i}")
        for i in range(3)
    ]
    assert history_before(answers, ["msg_a", "msg_b", "msg_c"], keep=1) == answers[:1]


def test_file_changes_are_picked_by_session_and_turn() -> None:
    """The tracker is the pool's: another session's later changes stay out of a revert."""
    tracker = FileOpsTracker()
    tracker.record_change("a.py", "1", "2", "write", message_id="msg_a1", session_id="ses_a")
    tracker.record_change("b.py", "1", "2", "write", message_id="msg_b1", session_id="ses_b")
    tracker.record_change("a.py", "2", "3", "write", message_id="msg_a2", session_id="ses_a")
    picked = changes_of_turns(tracker, "ses_a", ["msg_a2"], start_ms=0)
    assert [(c.path, c.new_content) for c in picked] == [("a.py", "3")]
