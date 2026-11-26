"""Connection stats keep the last N messages (totals count all); file changes are bounded."""

from __future__ import annotations

from agentpool import Agent
from agentpool.messaging import ChatMessage
from agentpool.talk.stats import TalkStats
from agentpool.utils.file_ops_tracker import FileOpsTracker
from agentpool_config.conditions import MessageCountCondition


def _msg(i: int, name: str = "a") -> ChatMessage[str]:
    return ChatMessage[str](content=f"m{i}", role="assistant", name=name)


def test_talk_stats_keep_the_last_messages_and_count_all():
    stats = TalkStats(source_name="a", target_names={"b"}, history=3)
    for i in range(10):
        stats.record(_msg(i, name="a" if i % 2 else "b"))
    assert [m.content for m in stats.messages] == ["m7", "m8", "m9"]
    assert stats.message_count == 10
    assert stats.byte_count == 20
    assert stats.count_from("a") == 5


async def test_connection_stats_bounded():
    async with Agent[None](model="test", name="src") as source, Agent[None](model="test") as dst:
        talk = source.connect_to(dst)
        talk._stats = TalkStats(source_name="src", target_names={dst.name}, history=2)
        for i in range(4):
            await source.run(f"q{i}")
    assert len(talk.stats.messages) == 2
    assert talk.stats.message_count == 4


async def test_message_count_condition_counts_beyond_the_kept_history():
    stats = TalkStats(source_name="a", target_names={"b"}, history=1)
    for i in range(5):
        stats.record(_msg(i))

    class _Ctx:
        def __init__(self) -> None:
            self.stats = stats
            self.message = _msg(99)

    condition = MessageCountCondition(max_messages=5, count_mode="per_agent")
    assert await condition.check(_Ctx())  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]


def test_file_ops_tracker_is_bounded():
    tracker = FileOpsTracker(max_changes=3)
    for i in range(5):
        tracker.record_change(f"f{i}.txt", None, "x", "create", session_id="s")
    assert [c.path for c in tracker.changes] == ["f2.txt", "f3.txt", "f4.txt"]


def test_file_ops_tracker_drops_a_closed_session():
    tracker = FileOpsTracker()
    tracker.record_change("a.txt", None, "x", "create", session_id="s1", message_id="m1")
    tracker.record_change("b.txt", None, "x", "create", session_id="s2", message_id="m2")
    tracker.record_change("c.txt", None, "x", "create", session_id="s1", message_id="m3")
    tracker.remove_changes_since("m3")  # A reverted change of s1
    assert tracker.drop_session("s1") == 2
    assert [c.path for c in tracker.changes] == ["b.txt"]
    assert tracker.reverted_changes == []
