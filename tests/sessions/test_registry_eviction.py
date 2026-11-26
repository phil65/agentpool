"""The session registry never evicts a session whose turn runs."""

from agentpool.sessions.activity import ActivityFeed
from agentpool.sessions.session import SessionRegistry


def test_running_session_is_not_evicted():
    feed = ActivityFeed()
    registry = SessionRegistry(max_sessions=2, activity=feed)
    registry.create("agent", session_id="busy")
    feed.state("busy", "agent", "running")
    registry.create("agent", session_id="s2")
    registry.create("agent", session_id="s3")  # Over the bound: the oldest idle goes
    assert "busy" in registry
    assert "s2" not in registry
    assert "s3" in registry


def test_registry_grows_while_all_run_and_shrinks_when_idle():
    feed = ActivityFeed()
    registry = SessionRegistry(max_sessions=1, activity=feed)
    for sid in ("a", "b"):
        registry.create("agent", session_id=sid)
        feed.state(sid, "agent", "requires_action")
    assert len(registry) == 2  # Both wait for the user: neither is dropped
    feed.state("a", "agent", "idle", stop_reason="end_turn")
    registry.create("agent", session_id="c")
    assert "a" not in registry
    assert {"b", "c"} <= {s.session_id for s in registry.list_sessions()}


def test_activity_feed_idle_api():
    feed = ActivityFeed()
    assert feed.is_idle("x")
    assert feed.current_state("x") == "idle"
    feed.state("x", "agent", "running")
    assert not feed.is_idle("x")
    assert feed.current_state("x") == "running"
    feed.state("x", "agent", "idle", stop_reason="end_turn")
    assert feed.is_idle("x")
