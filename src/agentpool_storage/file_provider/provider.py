"""File provider implementation."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, TypedDict, cast

from pydantic import TypeAdapter
from pydantic_ai import FinishReason, RunUsage
from upathtools import to_upath

from agentpool.common_types import JsonValue, MessageRole
from agentpool.log import get_logger
from agentpool.messaging import ChatMessage, TokenCost
from agentpool.storage import deserialize_messages
from agentpool.utils.time_utils import get_now
from agentpool_config.storage import FileStorageConfig
from agentpool_storage.base import StorageProvider


if TYPE_CHECKING:
    from yamling import FormatType

    from agentpool.sessions.models import ProjectData
    from agentpool_config.session import SessionQuery

logger = get_logger(__name__)


class MessageData(TypedDict):
    """Data structure for storing message information."""

    message_id: str
    session_id: str
    content: str
    role: str
    timestamp: str
    name: str | None
    model: str | None
    cost: Decimal | None
    token_usage: RunUsage | None
    response_time: float | None
    provider_name: str | None
    provider_response_id: str | None
    messages: str | None
    finish_reason: FinishReason | None
    parent_id: str | None


class ConversationData(TypedDict):
    """Data structure for storing conversation information."""

    id: str
    agent_name: str
    title: str | None
    start_time: str


class CommandData(TypedDict):
    """Data structure for storing command information."""

    agent_name: str
    session_id: str
    command: str
    timestamp: str
    context_type: str | None
    metadata: dict[str, JsonValue]


class ProjectDataDict(TypedDict):
    """Data structure for storing project information."""

    project_id: str
    worktree: str
    name: str | None
    vcs: str | None
    config_path: str | None
    created_at: str
    last_active: str
    settings: dict[str, JsonValue]


class StorageData(TypedDict):
    """Data structure for storing storage information."""

    messages: list[MessageData]
    conversations: list[ConversationData]
    commands: list[CommandData]
    projects: list[ProjectDataDict]


class FileProvider(StorageProvider):
    """File-based storage using various formats.

    Automatically detects format from file extension or uses specified format.
    Supported formats: YAML (.yml, .yaml), JSON (.json), TOML (.toml)
    """

    can_load_history = True

    def __init__(self, config: FileStorageConfig | None = None) -> None:
        """Initialize file provider.

        Args:
            config: Configuration for provider
        """
        config = config or FileStorageConfig(path=".")
        super().__init__(config)
        self.path = to_upath(config.path)
        self.format: FormatType = config.format
        self.encoding = config.encoding
        self._data: StorageData = {
            "messages": [],
            "conversations": [],
            "commands": [],
            "projects": [],
        }
        self._load()

    def _load(self) -> None:
        """Load data from file if it exists."""
        import yamling

        if self.path.exists():
            self._data = yamling.load_file(self.path, self.format, verify_type=StorageData)
        self._save()

    def _save(self) -> None:
        """Save current data to file."""
        import yamling

        self.path.parent.mkdir(parents=True, exist_ok=True)
        yamling.dump_file(self._data, self.path, mode=self.format, overwrite=True)

    def cleanup(self) -> None:
        """Save final state."""
        self._save()

    async def filter_messages(self, query: SessionQuery) -> list[ChatMessage[str]]:
        """Filter messages based on query."""
        messages = []
        for msg in self._data["messages"]:
            # Apply filters
            if query.name and msg["session_id"] != query.name:
                continue
            if query.agents and msg["name"] not in query.agents:
                continue
            cutoff = query.get_time_cutoff()
            timestamp = datetime.fromisoformat(msg["timestamp"])
            if query.since and cutoff and (timestamp < cutoff):
                continue
            if query.until and datetime.fromisoformat(msg["timestamp"]) > datetime.fromisoformat(
                query.until
            ):
                continue
            if query.contains and query.contains not in msg["content"]:
                continue
            if query.roles and msg["role"] not in query.roles:
                continue
            messages.append(_to_chat_message(msg))

            if query.limit and len(messages) >= query.limit:
                break

        return messages

    async def log_message(self, *, message: ChatMessage[Any]) -> None:
        """Log a new message."""
        from agentpool.storage.serialization import serialize_messages

        self._data["messages"].append({
            "session_id": message.session_id or "",
            "message_id": message.message_id,
            "content": str(message.content),
            "role": message.role,
            "timestamp": get_now().isoformat(),
            "name": message.name,
            "model": message.model_name,
            "cost": Decimal(info.total_cost) if (info := message.cost_info) else None,
            "token_usage": message.usage,
            "response_time": message.response_time,
            "provider_name": message.provider_name,
            "provider_response_id": message.provider_response_id,
            "messages": serialize_messages(message.messages),
            "finish_reason": message.finish_reason,
            "parent_id": message.parent_id,
        })
        self._save()

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
        """Log a new conversation."""
        conversation = ConversationData(
            id=session_id,
            agent_name=node_name,
            title=None,
            start_time=(start_time or get_now()).isoformat(),
        )
        self._data["conversations"].append(conversation)
        self._save()

    async def update_session_title(self, session_id: str, title: str) -> None:
        """Update the title of a conversation."""
        for conv in self._data["conversations"]:
            if conv["id"] == session_id:
                conv["title"] = title
                self._save()
                return

    async def get_session_title(self, session_id: str) -> str | None:
        """Get the title of a conversation."""
        conv = next((c for c in self._data["conversations"] if c["id"] == session_id), None)
        return conv.get("title") if conv else None

    async def get_session_messages(
        self,
        session_id: str,
        *,
        include_ancestors: bool = False,
    ) -> list[ChatMessage[str]]:
        """Get all messages for a session."""
        messages = [
            _to_chat_message(msg)
            for msg in self._data["messages"]
            if msg["session_id"] == session_id
        ]
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
        return next(
            (
                _to_chat_message(m)
                for m in self._data["messages"]
                if m.get("message_id") == message_id
            ),
            None,
        )

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
        source_conv = next(
            (c for c in self._data["conversations"] if c["id"] == source_session_id),
            None,
        )
        if not source_conv:
            raise ValueError(f"Source conversation not found: {source_session_id}")

        # Determine fork point
        fork_point_id: str | None = None
        if fork_from_message_id:
            # Verify message exists in source conversation
            msg_exists = any(
                m.get("message_id") == fork_from_message_id and m["session_id"] == source_session_id
                for m in self._data["messages"]
            )
            if not msg_exists:
                err = f"Message {fork_from_message_id} not found in conversation"
                raise ValueError(err)
            fork_point_id = fork_from_message_id
        else:
            # Find last message in source conversation
            conv_messages = [
                m for m in self._data["messages"] if m["session_id"] == source_session_id
            ]
            if conv_messages:
                conv_messages.sort(
                    key=lambda m: (
                        datetime.fromisoformat(m["timestamp"]) if m.get("timestamp") else get_now()
                    )
                )
                fork_point_id = conv_messages[-1].get("message_id")

        # Create new conversation
        agent_name = new_agent_name or source_conv["agent_name"]
        title = (
            f"{source_conv.get('title') or 'Conversation'} (fork)"
            if source_conv.get("title")
            else None
        )
        new_conv = ConversationData(
            id=new_session_id,
            agent_name=agent_name,
            title=title,
            start_time=get_now().isoformat(),
        )
        self._data["conversations"].append(new_conv)
        self._save()
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
        """Log a command execution."""
        cmd = CommandData(
            agent_name=agent_name,
            session_id=session_id,
            command=command,
            context_type=context_type.__name__ if context_type else None,
            metadata=metadata or {},
            timestamp=get_now().isoformat(),
        )
        self._data["commands"].append(cmd)
        self._save()

    async def get_commands(
        self,
        agent_name: str,
        session_id: str,
        *,
        limit: int | None = None,
        current_session_only: bool = False,
    ) -> list[str]:
        """Get command history."""
        commands = []
        for cmd in reversed(self._data["commands"]):  # newest first
            if current_session_only and cmd["session_id"] != session_id:
                continue
            if not current_session_only and cmd["agent_name"] != agent_name:
                continue
            commands.append(cmd["command"])
            if limit and len(commands) >= limit:
                break
        return commands

    async def reset(self, *, agent_name: str | None = None, hard: bool = False) -> tuple[int, int]:
        """Reset stored data."""
        # Get counts first
        conv_count, msg_count = await self.get_session_counts(agent_name=agent_name)

        if hard:
            if agent_name:
                raise ValueError("Hard reset cannot be used with agent_name")
            # Clear everything
            self._data = {
                "messages": [],
                "conversations": [],
                "commands": [],
                "projects": [],
            }
            self._save()
            return conv_count, msg_count

        if agent_name:
            # Filter out data for specific agent (collect its session ids first)
            ids = {c["id"] for c in self._data["conversations"] if c["agent_name"] == agent_name}
            self._data["conversations"] = [
                c for c in self._data["conversations"] if c["agent_name"] != agent_name
            ]
            self._data["messages"] = [
                m for m in self._data["messages"] if m["session_id"] not in ids
            ]
        else:
            # Clear all
            self._data["messages"].clear()
            self._data["conversations"].clear()
            self._data["commands"].clear()

        self._save()
        return conv_count, msg_count

    async def get_session_counts(self, *, agent_name: str | None = None) -> tuple[int, int]:
        """Get conversation and message counts."""
        if agent_name:
            conv_count = sum(
                1 for c in self._data["conversations"] if c["agent_name"] == agent_name
            )
            msg_count = sum(
                1
                for m in self._data["messages"]
                if m["session_id"]
                in {c["id"] for c in self._data["conversations"] if c["agent_name"] == agent_name}
            )
        else:
            conv_count = len(self._data["conversations"])
            msg_count = len(self._data["messages"])

        return conv_count, msg_count

    async def delete_session_messages(self, session_id: str) -> int:
        """Delete all messages for a session."""
        original_count = len(self._data["messages"])
        self._data["messages"] = [
            m for m in self._data["messages"] if m["session_id"] != session_id
        ]
        deleted = original_count - len(self._data["messages"])
        if deleted > 0:
            self._save()
        return deleted

    # Project methods

    def _to_project_data(self, data: ProjectDataDict) -> ProjectData:
        """Convert dict to ProjectData."""
        from agentpool.sessions.models import ProjectData

        return ProjectData(
            project_id=data["project_id"],
            worktree=data["worktree"],
            name=data["name"],
            vcs=data["vcs"],
            config_path=data["config_path"],
            created_at=datetime.fromisoformat(data["created_at"]),
            last_active=datetime.fromisoformat(data["last_active"]),
            settings=data["settings"],
        )

    def _to_project_dict(self, project: ProjectData) -> ProjectDataDict:
        """Convert ProjectData to dict."""
        return ProjectDataDict(
            project_id=project.project_id,
            worktree=project.worktree,
            name=project.name,
            vcs=project.vcs,
            config_path=project.config_path,
            created_at=project.created_at.isoformat(),
            last_active=project.last_active.isoformat(),
            settings=project.settings,
        )

    async def save_project(self, project: ProjectData) -> None:
        """Save or update a project."""
        # Remove existing if present
        self._data["projects"] = [
            p for p in self._data.get("projects", []) if p["project_id"] != project.project_id
        ]
        # Add new/updated
        self._data["projects"].append(self._to_project_dict(project))
        self._save()
        logger.debug("Saved project", project_id=project.project_id)

    async def get_project(self, project_id: str) -> ProjectData | None:
        """Get a project by ID."""
        for p in self._data.get("projects", []):
            if p["project_id"] == project_id:
                return self._to_project_data(p)
        return None

    async def get_project_by_worktree(self, worktree: str) -> ProjectData | None:
        """Get a project by worktree path."""
        for p in self._data.get("projects", []):
            if p["worktree"] == worktree:
                return self._to_project_data(p)
        return None

    async def get_project_by_name(self, name: str) -> ProjectData | None:
        """Get a project by friendly name."""
        for p in self._data.get("projects", []):
            if p["name"] == name:
                return self._to_project_data(p)
        return None

    async def list_projects(self, limit: int | None = None) -> list[ProjectData]:
        """List all projects, ordered by last_active descending."""
        projects = sorted(
            self._data.get("projects", []),
            key=lambda p: p["last_active"],
            reverse=True,
        )
        if limit is not None:
            projects = projects[:limit]
        return [self._to_project_data(p) for p in projects]

    async def delete_project(self, project_id: str) -> bool:
        """Delete a project."""
        original_count = len(self._data.get("projects", []))
        self._data["projects"] = [
            p for p in self._data.get("projects", []) if p["project_id"] != project_id
        ]
        deleted = original_count > len(self._data["projects"])
        if deleted:
            self._save()
            logger.debug("Deleted project", project_id=project_id)
        return deleted

    async def touch_project(self, project_id: str) -> None:
        """Update project's last_active timestamp."""
        for p in self._data.get("projects", []):
            if p["project_id"] == project_id:
                p["last_active"] = get_now().isoformat()
                self._save()
                return


def _to_chat_message(msg: MessageData) -> ChatMessage[str]:
    """Convert stored message data to ChatMessage."""
    cost_info = TokenCost(total_cost=Decimal(str(msg.get("cost") or 0)))
    # Build kwargs, only including timestamp/message_id if they have values
    kwargs: dict[str, Any] = {
        "content": msg["content"],
        "role": cast(MessageRole, msg["role"]),
        "name": msg.get("name"),
        "model_name": msg.get("model"),
        "cost_info": cost_info,
        "usage": TypeAdapter(RunUsage).validate_python(msg["token_usage"])
        if msg["token_usage"]
        else RunUsage(),
        "response_time": msg.get("response_time"),
        "provider_name": msg.get("provider_name"),
        "provider_response_id": msg.get("provider_response_id"),
        "parent_id": msg.get("parent_id"),
        "session_id": msg.get("session_id"),
        "messages": deserialize_messages(msg.get("messages")),
        "finish_reason": msg.get("finish_reason"),
    }
    if msg.get("timestamp"):
        kwargs["timestamp"] = datetime.fromisoformat(msg["timestamp"])
    if msg.get("message_id"):
        kwargs["message_id"] = msg["message_id"]
    return ChatMessage[str](**kwargs)
