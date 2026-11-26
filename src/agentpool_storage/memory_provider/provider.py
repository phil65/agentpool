"""In-memory storage provider for testing."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from pydantic_ai import RunUsage

from agentpool.utils.time_utils import get_now
from agentpool_config.storage import MemoryStorageConfig
from agentpool_storage.base import StorageProvider
from agentpool_storage.models import ConversationData


if TYPE_CHECKING:
    from collections.abc import Sequence

    from agentpool.common_types import JsonValue
    from agentpool.messaging import ChatMessage
    from agentpool.sessions.models import ProjectData, SessionData
    from agentpool_config.session import SessionQuery
    from agentpool_storage.models import QueryFilters, StatsFilters


class MemoryStorageProvider(StorageProvider):
    """In-memory storage provider for testing."""

    can_load_history = True

    def __init__(self, config: MemoryStorageConfig | None = None) -> None:
        super().__init__(config or MemoryStorageConfig())
        self.messages: list[ChatMessage[str]] = []
        self.conversations: list[dict[str, Any]] = []
        self.commands: list[dict[str, Any]] = []
        self.projects: dict[str, ProjectData] = {}

    def cleanup(self) -> None:
        """Clear all stored data."""
        self.messages.clear()
        self.conversations.clear()
        self.commands.clear()
        self.projects.clear()

    async def filter_messages(self, query: SessionQuery) -> list[ChatMessage[str]]:
        """Filter messages from memory."""
        filtered = []
        for msg in self.messages:
            # Skip if conversation ID doesn't match
            if query.name and msg.session_id != query.name:
                continue

            # Skip if agent name doesn't match
            if query.agents and msg.name not in query.agents:
                continue

            # Skip if before cutoff time
            if query.since and (cutoff := query.get_time_cutoff()):  # noqa: SIM102
                if msg.timestamp and msg.timestamp < cutoff:
                    continue

            # Skip if after until time
            if (
                query.until
                and msg.timestamp
                and msg.timestamp > datetime.fromisoformat(query.until)
            ):
                continue

            # Skip if content doesn't match search
            if query.contains and query.contains not in msg.content:
                continue

            # Skip if role doesn't match
            if query.roles and msg.role not in query.roles:
                continue

            filtered.append(msg)

            # Apply limit if specified
            if query.limit and len(filtered) >= query.limit:
                break

        return filtered

    async def log_message(self, *, message: ChatMessage[str]) -> None:
        """Store message in memory."""
        if any(m.message_id == message.message_id for m in self.messages):
            msg = f"Duplicate message ID: {message.message_id}"
            raise ValueError(msg)
        self.messages.append(message)

    async def log_session(
        self,
        *,
        session_id: str,
        node_name: str,
        start_time: datetime | None = None,
        model: str | None = None,
        agent_type: str | None = None,
        definition_version: str | None = None,
    ) -> None:
        """Store conversation in memory (idempotent)."""
        if any(c["id"] == session_id for c in self.conversations):
            return
        self.conversations.append({
            "id": session_id,
            "agent_name": node_name,
            "title": None,
            "start_time": start_time or get_now(),
            "agent_type": agent_type,
            "definition_version": definition_version,
        })

    async def update_session_title(self, session_id: str, title: str) -> None:
        """Update the title of a conversation."""
        for conv in self.conversations:
            if conv["id"] == session_id:
                conv["title"] = title
                return

    async def get_session_title(self, session_id: str) -> str | None:
        """Get the title of a conversation."""
        for conv in self.conversations:
            if conv["id"] == session_id:
                return conv.get("title")
        return None

    async def get_session_messages(
        self,
        session_id: str,
        *,
        include_ancestors: bool = False,
    ) -> list[ChatMessage[str]]:
        """Get all messages for a session."""
        messages = [msg for msg in self.messages if msg.session_id == session_id]

        # Sort by timestamp
        now = get_now()
        messages.sort(key=lambda m: m.timestamp or now)

        if not include_ancestors or not messages:
            return messages

        # Get ancestor chain if first message has parent_id
        first_msg = messages[0]
        if first_msg.parent_id:
            ancestors = await self.get_message_ancestry(first_msg.parent_id, session_id=session_id)
            return ancestors + messages

        return messages

    async def get_message(
        self,
        message_id: str,
        *,
        session_id: str | None = None,
    ) -> ChatMessage[str] | None:
        """Get a single message by ID."""
        return next((msg for msg in self.messages if msg.message_id == message_id), None)

    async def get_message_ancestry(
        self,
        message_id: str,
        *,
        session_id: str | None = None,
    ) -> list[ChatMessage[str]]:
        """Get the ancestry chain of a message. Stops at a cycle."""
        ancestors: list[ChatMessage[str]] = []
        current_id: str | None = message_id
        seen: set[str] = set()

        while current_id and current_id not in seen:
            seen.add(current_id)
            msg = await self.get_message(current_id, session_id=session_id)
            if not msg:
                break
            ancestors.append(msg)
            current_id = msg.parent_id

        # Reverse to get oldest first
        ancestors.reverse()
        return ancestors

    async def fork_conversation(
        self,
        *,
        source_session_id: str,
        new_session_id: str,
        fork_from_message_id: str | None = None,
        new_agent_name: str | None = None,
    ) -> str | None:
        """Fork a conversation at a specific point."""
        # Find source conversation
        source_conv = next((c for c in self.conversations if c["id"] == source_session_id), None)
        if not source_conv:
            msg = f"Source conversation not found: {source_session_id}"
            raise ValueError(msg)

        # Determine fork point
        fork_point_id: str | None = None
        if fork_from_message_id:
            # Verify message exists in source conversation
            msg_exists = any(
                m.message_id == fork_from_message_id and m.session_id == source_session_id
                for m in self.messages
            )
            if not msg_exists:
                err = f"Message {fork_from_message_id} not found in conversation"
                raise ValueError(err)
            fork_point_id = fork_from_message_id
        else:
            # Find last message in source conversation
            conv_messages = [m for m in self.messages if m.session_id == source_session_id]
            if conv_messages:
                now = get_now()
                conv_messages.sort(key=lambda m: m.timestamp or now)
                fork_point_id = conv_messages[-1].message_id

        # Create new conversation
        agent_name = new_agent_name or source_conv["agent_name"]
        title = (
            f"{source_conv.get('title') or 'Conversation'} (fork)"
            if source_conv.get("title")
            else None
        )
        self.conversations.append({
            "id": new_session_id,
            "agent_name": agent_name,
            "title": title,
            "start_time": get_now(),
        })

        return fork_point_id

    async def log_command(
        self,
        *,
        agent_name: str,
        session_id: str,
        command: str,
        context_type: type | None = None,
        metadata: dict[str, JsonValue] | None = None,
    ) -> None:
        """Store command in memory."""
        self.commands.append({
            "agent_name": agent_name,
            "session_id": session_id,
            "command": command,
            "timestamp": get_now(),
            "context_type": context_type.__name__ if context_type else None,
            "metadata": metadata or {},
        })

    async def get_commands(
        self,
        agent_name: str,
        session_id: str,
        *,
        limit: int | None = None,
        current_session_only: bool = False,
    ) -> list[str]:
        """Get commands from memory."""
        filtered = []
        for cmd in reversed(self.commands):  # newest first
            if current_session_only and cmd["session_id"] != session_id:
                continue
            if not current_session_only and cmd["agent_name"] != agent_name:
                continue
            filtered.append(cmd["command"])
            if limit and len(filtered) >= limit:
                break
        return filtered

    async def get_sessions(self, filters: QueryFilters) -> list[ConversationData]:
        """Get filtered conversations from memory."""
        results: list[ConversationData] = []
        # First get matching conversations
        convs = {}
        for conv in self.conversations:
            if filters.agent_name and conv["agent_name"] != filters.agent_name:
                continue
            if filters.since and conv["start_time"] < filters.since:
                continue
            convs[conv["id"]] = conv

        # Then get messages for each conversation
        for conv_id, conv in convs.items():
            conv_messages = [
                msg
                for msg in self.messages
                if msg.session_id == conv_id
                and (not filters.query or filters.query in msg.content)
                and (not filters.model or msg.model_name == filters.model)
            ]

            # Skip if no matching messages for content filter
            if filters.query and not conv_messages:
                continue

            conv_data = ConversationData(
                id=conv_id,
                agent=conv["agent_name"],
                title=conv.get("title"),
                start_time=conv["start_time"].isoformat(),
                messages=conv_messages,
                token_usage=_aggregate_token_usage(conv_messages),
            )
            results.append(conv_data)
            if filters.limit and len(results) >= filters.limit:
                break

        return results

    async def get_session_stats(self, filters: StatsFilters) -> dict[str, dict[str, Any]]:
        """Get statistics from memory."""
        # Collect raw data
        rows = []
        for msg in self.messages:
            if msg.timestamp and msg.timestamp <= filters.cutoff:
                continue
            if filters.agent_name and msg.name != filters.agent_name:
                continue
            rows.append((msg.model_name, msg.name, msg.timestamp, msg.usage))

        # Use base class aggregation
        return self.aggregate_stats(rows, filters.group_by)

    async def reset(self, *, agent_name: str | None = None, hard: bool = False) -> tuple[int, int]:
        """Reset stored data."""
        # Get counts first
        conv_count, msg_count = await self.get_session_counts(agent_name=agent_name)

        if hard:
            if agent_name:
                msg = "Hard reset cannot be used with agent_name"
                raise ValueError(msg)
            # Clear everything
            self.cleanup()
            return conv_count, msg_count

        if agent_name:
            # Get conversation IDs for this agent
            agent_conv_ids = {c["id"] for c in self.conversations if c["agent_name"] == agent_name}
            # Filter out data for specific agent
            self.conversations = [c for c in self.conversations if c["agent_name"] != agent_name]
            self.messages = [m for m in self.messages if m.session_id not in agent_conv_ids]
        else:
            # Clear all
            self.messages.clear()
            self.conversations.clear()
            self.commands.clear()

        return conv_count, msg_count

    async def get_session_counts(self, *, agent_name: str | None = None) -> tuple[int, int]:
        """Get conversation and message counts."""
        if agent_name:
            agent_conv_ids = {c["id"] for c in self.conversations if c["agent_name"] == agent_name}
            conv_count = len(agent_conv_ids)
            msg_count = sum(1 for m in self.messages if m.session_id in agent_conv_ids)
        else:
            conv_count = len(self.conversations)
            msg_count = len(self.messages)

        return conv_count, msg_count

    async def delete_session_messages(self, session_id: str) -> int:
        """Delete all messages for a session."""
        original_count = len(self.messages)
        self.messages = [m for m in self.messages if m.session_id != session_id]
        return original_count - len(self.messages)

    # Project methods

    async def save_project(self, project: ProjectData) -> None:
        """Save or update a project."""
        self.projects[project.project_id] = project

    async def get_project(self, project_id: str) -> ProjectData | None:
        """Get a project by ID."""
        return self.projects.get(project_id)

    async def get_project_by_worktree(self, worktree: str) -> ProjectData | None:
        """Get a project by worktree path."""
        for project in self.projects.values():
            if project.worktree == worktree:
                return project
        return None

    async def get_project_by_name(self, name: str) -> ProjectData | None:
        """Get a project by friendly name."""
        for project in self.projects.values():
            if project.name == name:
                return project
        return None

    async def list_projects(self, limit: int | None = None) -> list[ProjectData]:
        """List all projects, ordered by last_active descending."""
        projects = sorted(
            self.projects.values(),
            key=lambda p: p.last_active,
            reverse=True,
        )
        if limit is not None:
            projects = projects[:limit]
        return list(projects)

    async def delete_project(self, project_id: str) -> bool:
        """Delete a project."""
        if project_id in self.projects:
            del self.projects[project_id]
            return True
        return False

    async def touch_project(self, project_id: str) -> None:
        """Update project's last_active timestamp."""
        if project_id in self.projects:
            project = self.projects[project_id]
            self.projects[project_id] = project.touch()

    # Session data methods

    async def save_session(self, data: SessionData) -> None:
        """Save or update session data in memory.

        Updating an existing session only touches the fields owned by ``SessionData``:
        the creation time and other conversation data are kept, and a missing title
        never clears a stored one.
        """
        conv: dict[str, Any] | None = next(
            (c for c in self.conversations if c["id"] == data.session_id), None
        )
        if conv is None:
            conv = {"id": data.session_id, "start_time": data.created_at, "title": None}
            self.conversations.append(conv)
        conv.update({
            "agent_name": data.agent_name,
            "pool_id": data.pool_id,
            "project_id": data.project_id,
            "parent_id": data.parent_id,
            "version": data.version,
            "cwd": data.cwd,
            "agent_type": data.agent_type,
            "sdk_session_id": data.sdk_session_id,
            "definition_version": data.definition_version,
            "last_active": data.last_active,
            # The title field is canonical; `load_session` re-adds it to the metadata.
            "metadata": {k: v for k, v in data.metadata.items() if k != "title"},
        })
        if data.title is not None:
            conv["title"] = data.title

    async def load_session(self, session_id: str) -> SessionData | None:
        """Load session data by ID from memory."""
        from agentpool.sessions import models as session_models

        for conv in self.conversations:
            if conv["id"] == session_id:
                metadata = dict(conv.get("metadata", {}))
                if (title := conv.get("title")) is not None:
                    metadata["title"] = title
                return session_models.SessionData(
                    session_id=conv["id"],
                    agent_name=conv["agent_name"],
                    pool_id=conv.get("pool_id"),
                    project_id=conv.get("project_id"),
                    parent_id=conv.get("parent_id"),
                    version=conv.get("version", "1"),
                    cwd=conv.get("cwd"),
                    agent_type=conv.get("agent_type"),
                    sdk_session_id=conv.get("sdk_session_id"),
                    definition_version=conv.get("definition_version"),
                    created_at=conv["start_time"],
                    last_active=conv.get("last_active", conv["start_time"]),
                    metadata=metadata,
                )
        return None

    async def delete_session(self, session_id: str) -> bool:
        """Delete a session from memory."""
        original_count = len(self.conversations)
        self.conversations = [c for c in self.conversations if c["id"] != session_id]
        return len(self.conversations) < original_count

    async def list_session_ids(
        self,
        pool_id: str | None = None,
        agent_name: str | None = None,
    ) -> list[str]:
        """List session IDs from memory."""
        result = []
        for conv in self.conversations:
            if pool_id is not None and conv.get("pool_id") != pool_id:
                continue
            if agent_name is not None and conv["agent_name"] != agent_name:
                continue
            result.append(conv["id"])
        return result

    async def update_sdk_session_id(self, session_id: str, sdk_session_id: str) -> None:
        """Update the external SDK session ID in memory."""
        for conv in self.conversations:
            if conv["id"] == session_id:
                conv["sdk_session_id"] = sdk_session_id
                return


def _aggregate_token_usage(messages: Sequence[ChatMessage[Any]]) -> RunUsage:
    """Sum up tokens from a sequence of messages."""
    usage = RunUsage()
    for msg in messages:
        usage += msg.usage
    return usage
