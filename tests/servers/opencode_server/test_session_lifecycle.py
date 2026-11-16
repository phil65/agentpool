"""Session lifecycle tests.

Ported from OpenCode's test/session/session.test.ts

Tests session creation, events, and lifecycle management.

Note: The OpenCode API uses camelCase field names with "ID" suffix:
- projectID (not project_id)
- parentID (not parent_id)
- Session IDs use "ses_" prefix (not "session_")
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from agentpool.sessions.models import SessionData
from opencode_sdk.models import SessionCreatedEvent, SessionStatus


if TYPE_CHECKING:
    from pathlib import Path

    from httpx import AsyncClient

    from agentpool_server.opencode_server.state import ServerState
    from tests.servers.opencode_server.conftest import EventCapture


class TestSessionCreatedEvent:
    """Tests for session.created event emission."""

    async def test_should_emit_session_created_event_when_session_is_created(
        self,
        async_client: AsyncClient,
        server_state: ServerState,
        event_capture: EventCapture,
    ):
        """Session creation should emit session.created event.

        Ported from: "should emit session.started event when session is created"
        """
        # Create a session via the API
        response = await async_client.post("/session", json={"title": "Test Session"})
        assert response.status_code == 200
        session_data = response.json()
        # Verify the session was created correctly
        assert "id" in session_data
        assert session_data["title"] == "Test Session"
        assert session_data["projectID"] == "default"  # camelCase with ID suffix
        # Verify the session.created event was emitted
        created_events = event_capture.get_events_by_type("session.created")
        assert len(created_events) == 1
        event = created_events[0]
        assert isinstance(event, SessionCreatedEvent)
        assert event.properties.info.id == session_data["id"]
        assert event.properties.info.title == session_data["title"]
        assert event.properties.info.project_id == "default"  # Python attr is snake_case

    async def test_session_created_event_should_be_emitted_before_session_updated(
        self,
        async_client: AsyncClient,
        server_state: ServerState,
        event_capture: EventCapture,
    ):
        """Session.created event should be emitted before session.updated.

        Ported from: "session.started event should be emitted before session.updated"

        When a session is created and then updated, the created event must come first.
        """
        # Create a session
        create_response = await async_client.post("/session", json={"title": "Original Title"})
        assert create_response.status_code == 200
        session_id = create_response.json()["id"]
        # Update the session title
        update_response = await async_client.patch(
            f"/session/{session_id}",
            json={"title": "Updated Title"},
        )
        assert update_response.status_code == 200
        # Verify event order: created should come before updated
        event_types = [e.type for e in event_capture.events]
        assert "session.created" in event_types
        assert "session.updated" in event_types
        created_index = event_types.index("session.created")
        updated_index = event_types.index("session.updated")
        assert created_index < updated_index, (
            f"session.created (index {created_index}) should come before "
            f"session.updated (index {updated_index})"
        )


class TestSessionCRUD:
    """Tests for session CRUD operations."""

    async def test_create_session_returns_valid_session(
        self,
        async_client: AsyncClient,
        tmp_project_dir: Path,
    ):
        """Creating a session should return a valid session object."""
        response = await async_client.post("/session", json={"title": "My Session"})
        assert response.status_code == 200
        session = response.json()
        # Verify required fields (using camelCase API format)
        assert "id" in session
        assert session["id"].startswith("ses_")  # Session IDs use "ses_" prefix
        assert session["title"] == "My Session"
        assert session["projectID"] == "default"  # camelCase with ID suffix
        assert session["directory"] == str(tmp_project_dir)
        assert session["version"] == "1"
        assert "time" in session
        assert "created" in session["time"]
        assert "updated" in session["time"]

    async def test_create_session_with_parent_id(self, async_client: AsyncClient):
        """Creating a session with parent_id should set the parent."""
        # Create parent session
        parent_response = await async_client.post("/session", json={"title": "Parent"})
        parent_id = parent_response.json()["id"]
        # Create child session (API accepts snake_case due to populate_by_name)
        child_response = await async_client.post(
            "/session",
            json={"title": "Child", "parent_id": parent_id},
        )

        assert child_response.status_code == 200
        child = child_response.json()
        assert child["parentID"] == parent_id  # Response uses camelCase

    async def test_create_session_with_default_title(self, async_client: AsyncClient):
        """Creating a session without title should use default."""
        response = await async_client.post("/session", json={})
        assert response.status_code == 200
        session = response.json()
        assert session["title"] == "New Session"

    async def test_get_session_returns_created_session(self, async_client: AsyncClient):
        """Getting a session should return the correct session."""
        # Create a session
        create_response = await async_client.post("/session", json={"title": "Get Test"})
        session_id = create_response.json()["id"]
        # Get the session
        get_response = await async_client.get(f"/session/{session_id}")
        assert get_response.status_code == 200
        session = get_response.json()
        assert session["id"] == session_id
        assert session["title"] == "Get Test"

    async def test_get_nonexistent_session_returns_404(self, async_client: AsyncClient):
        """Getting a non-existent session should return 404."""
        response = await async_client.get("/session/nonexistent-session-id")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    async def test_update_session_title(
        self,
        async_client: AsyncClient,
        event_capture: EventCapture,
    ):
        """Updating a session title should persist and emit event."""
        # Create a session
        create_response = await async_client.post("/session", json={"title": "Original"})
        session_id = create_response.json()["id"]
        original_created = create_response.json()["time"]["created"]
        # Update the title
        update_response = await async_client.patch(
            f"/session/{session_id}",
            json={"title": "Updated Title"},
        )

        assert update_response.status_code == 200
        updated = update_response.json()
        assert updated["title"] == "Updated Title"
        assert updated["time"]["created"] == original_created
        assert updated["time"]["updated"] >= original_created
        # Verify session.updated event was emitted
        updated_events = event_capture.get_events_by_type("session.updated")
        assert len(updated_events) >= 1
        last_update = updated_events[-1]
        assert last_update.properties.info.title == "Updated Title"

    async def test_update_nonexistent_session_returns_404(self, async_client: AsyncClient):
        """Updating a non-existent session should return 404."""
        response = await async_client.patch("/session/nonexistent-id", json={"title": "New Title"})
        assert response.status_code == 404

    async def test_delete_session(self, async_client: AsyncClient, event_capture: EventCapture):
        """Deleting a session should remove it and emit event."""
        # Create a session
        create_response = await async_client.post("/session", json={"title": "To Delete"})
        session_id = create_response.json()["id"]
        # Delete the session
        delete_response = await async_client.delete(f"/session/{session_id}")
        assert delete_response.status_code == 200
        assert delete_response.json() is True
        # Verify session is gone
        get_response = await async_client.get(f"/session/{session_id}")
        assert get_response.status_code == 404
        # Verify session.deleted event was emitted
        deleted_events = event_capture.get_events_by_type("session.deleted")
        assert len(deleted_events) == 1
        assert deleted_events[0].properties.session_id == session_id

    async def test_delete_nonexistent_session_returns_404(self, async_client: AsyncClient):
        """Deleting a non-existent session should return 404."""
        response = await async_client.delete("/session/nonexistent-id")

        assert response.status_code == 404

    async def test_list_sessions_empty(self, async_client: AsyncClient):
        """Listing sessions when none exist should return empty list."""
        response = await async_client.get("/session")

        assert response.status_code == 200
        assert response.json() == []

    async def test_list_sessions_returns_created_sessions(
        self,
        async_client: AsyncClient,
        server_state: ServerState,
    ):
        """Listing sessions should return all created sessions."""
        # Create multiple sessions
        session_ids = []
        for i in range(3):
            response = await async_client.post("/session", json={"title": f"Session {i}"})
            session_ids.append(response.json()["id"])

        # Mock agent.list_sessions to return SessionData objects
        now = datetime.now(UTC)
        session_data_list = [
            SessionData(
                session_id=sid,
                agent_name="test-agent",
                cwd=server_state.working_dir,
                created_at=now,
                last_active=now,
                metadata={"title": f"Session {i}"},
            )
            for i, sid in enumerate(session_ids)
        ]
        server_state.agent.list_sessions.return_value = session_data_list  # type: ignore[attr-defined]  # ty:ignore[missing-attribute]
        # List sessions
        response = await async_client.get("/session")
        assert response.status_code == 200
        sessions = response.json()
        assert len(sessions) == 3
        returned_ids = {s["id"] for s in sessions}
        assert returned_ids == set(session_ids)


class TestSessionStatus:
    """Tests for session status management."""

    async def test_get_session_status_empty_when_all_idle(self, async_client: AsyncClient):
        """Getting status should return empty when all sessions are idle."""
        # Create a session (it starts as idle)
        await async_client.post("/session", json={"title": "Idle Session"})
        # Get status
        response = await async_client.get("/session/status")
        assert response.status_code == 200
        # Only non-idle sessions are returned
        assert response.json() == {}

    async def test_session_status_is_idle_by_default(
        self,
        async_client: AsyncClient,
        server_state: ServerState,
    ):
        """Newly created sessions should have idle status."""
        # Create a session
        response = await async_client.post("/session", json={"title": "New Session"})
        session_id = response.json()["id"]
        # Check internal state
        assert session_id in server_state.session_status
        assert server_state.session_status[session_id].type == "idle"

    async def test_abort_session(self, async_client: AsyncClient, server_state: ServerState):
        """Aborting a session should set status to idle."""
        # Create a session
        response = await async_client.post("/session", json={"title": "Running Session"})
        session_id = response.json()["id"]
        # Set status to busy (simulating running operation)
        server_state.session_status[session_id] = SessionStatus(type="busy")
        # Abort the session
        abort_response = await async_client.post(f"/session/{session_id}/abort")
        assert abort_response.status_code == 200
        assert abort_response.json() is True
        assert server_state.session_status[session_id].type == "idle"

    async def test_abort_nonexistent_session_returns_404(self, async_client: AsyncClient):
        """Aborting a non-existent session should return 404."""
        response = await async_client.post("/session/nonexistent-id/abort")
        assert response.status_code == 404


class TestSessionFork:
    """Tests for session forking functionality."""

    async def test_fork_session_creates_new_session_with_parent(
        self,
        async_client: AsyncClient,
        event_capture: EventCapture,
    ):
        """Forking a session should create a new session with parent_id set."""
        # Create original session
        original_response = await async_client.post("/session", json={"title": "Original Session"})
        original_id = original_response.json()["id"]
        # Fork the session
        fork_response = await async_client.post(f"/session/{original_id}/fork")
        assert fork_response.status_code == 200
        forked = fork_response.json()
        assert forked["id"] != original_id
        assert forked["parentID"] == original_id  # camelCase in response
        assert forked["title"] == "Original Session (fork)"
        # Verify session.created event was emitted for the fork
        created_events = event_capture.get_events_by_type("session.created")
        # Should have 2: original + fork
        assert len(created_events) == 2
        fork_event = created_events[-1]
        assert fork_event.properties.info.id == forked["id"]
        assert fork_event.properties.info.parent_id == original_id  # Python attr

    async def test_fork_nonexistent_session_returns_404(self, async_client: AsyncClient):
        """Forking a non-existent session should return 404."""
        response = await async_client.post("/session/nonexistent-id/fork")
        assert response.status_code == 404


class TestSessionTodos:
    """Tests for session todo management."""

    async def test_get_session_todos_empty_initially(self, async_client: AsyncClient):
        """Getting todos for a new session should return empty list."""
        # Create a session
        response = await async_client.post("/session", json={"title": "Todo Session"})
        session_id = response.json()["id"]
        # Get todos
        todos_response = await async_client.get(f"/session/{session_id}/todo")
        assert todos_response.status_code == 200
        assert todos_response.json() == []

    async def test_get_todos_for_nonexistent_session_returns_404(self, async_client: AsyncClient):
        """Getting todos for a non-existent session should return 404."""
        response = await async_client.get("/session/nonexistent-id/todo")
        assert response.status_code == 404


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
