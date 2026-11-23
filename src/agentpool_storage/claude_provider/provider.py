"""Claude Code storage provider.

This module implements storage compatible with Claude Code's filesystem format,
enabling interoperability between agentpool and Claude Code.

Key features:
- JSONL-based conversation logs per project
- Multi-agent support (main + sub-agents)
- Message ancestry tracking
- Conversation forking and branching

See ARCHITECTURE.md for detailed documentation of the storage format.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
import mmap
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

import anyenv
from clawd_code_sdk.models import ToolUseBlock as ClaudeToolUseBlock
from clawd_code_sdk.storage.chain import index_chain_entries, walk_ancestry
from clawd_code_sdk.storage.helpers import (
    count_session_messages,
    encode_project_path,
    extract_title,
    read_session,
    write_entry,
)
from clawd_code_sdk.storage.models import (
    ClaudeAssistantEntry,
    ClaudeChainEntry,
    ClaudeEntry,
)
from pydantic_ai import RunUsage

from agentpool.log import get_logger
from agentpool.utils.thread_helpers import parallel_map
from agentpool.utils.time_utils import get_now, parse_iso_timestamp
from agentpool_config.storage import ClaudeStorageConfig
from agentpool_storage.base import StorageProvider
from agentpool_storage.claude_provider.converters import (
    chat_message_to_entry,
    entry_to_chat_message,
    normalize_model_name,
)


if TYPE_CHECKING:
    from collections.abc import Iterator

    from clawd_code_sdk.storage.models import ClaudeJSONLEntry

    from agentpool.messaging import ChatMessage
    from agentpool.sessions.models import SessionData
    from agentpool_config.session import SessionQuery
    from agentpool_storage.models import ConversationData, QueryFilters, StatsFilters


logger = get_logger(__name__)


def _file_contains(path: Path, needle: bytes) -> bool:
    """Check whether a file's raw bytes contain ``needle``, without parsing it.

    The file is memory-mapped, so even very large transcripts are searched at memory
    speed without being read into Python objects.
    """
    try:
        with path.open("rb") as f:
            if os.fstat(f.fileno()).st_size == 0:
                return False
            with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                return mm.find(needle) != -1
    except OSError:
        return False


def _build_tool_id_mapping(entries: list[ClaudeJSONLEntry]) -> dict[str, str]:
    """Build a mapping from tool_call_id to tool_name from assistant entries."""
    mapping: dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, ClaudeAssistantEntry):
            continue
        content = entry.message.content
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, ClaudeToolUseBlock) and block.id and block.name:
                mapping[block.id] = block.name
    return mapping


@dataclass(slots=True)
class SessionMetadata:
    """Lightweight session metadata without full message parsing."""

    session_id: str
    path: Path
    first_timestamp: str | None
    last_timestamp: str | None
    cwd: str | None
    message_count: int
    title: str | None


@dataclass(slots=True)
class ParsedSession:
    """Fully parsed session with entries, messages, and metadata."""

    session_id: str
    path: Path
    entries: list[ClaudeJSONLEntry]
    tool_mapping: dict[str, str]
    messages: list[ChatMessage[str]]
    first_timestamp: datetime | None
    usage: RunUsage


def _read_session_metadata(
    session_path: Path,
    max_title_chars: int = 60,
) -> SessionMetadata | None:
    """Read minimal metadata from a session file without full parsing.

    Extracts timestamps, message count, cwd, and title in a single pass.
    This is much faster than validating all entries with Pydantic.

    Args:
        session_path: Path to the JSONL session file
        max_title_chars: Maximum characters for title (truncates with '...')

    Returns:
        SessionMetadata with timestamps, message count, and title,
        or None if file is empty/invalid
    """
    if not session_path.exists():
        return None

    first_timestamp: str | None = None
    last_timestamp: str | None = None
    cwd: str | None = None
    title: str | None = None
    message_count = 0

    with session_path.open("r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                data = anyenv.load_json(stripped, return_type=dict)
                entry_type = data.get("type")

                # Summary entries provide the best title
                if entry_type == "summary" and title is None and (summary := data.get("summary")):
                    title = str(summary)

                # Count user/assistant messages and extract metadata
                if entry_type in ("user", "assistant"):
                    message_count += 1
                    timestamp = data.get("timestamp")
                    # Get timestamp and cwd from first message entry
                    if first_timestamp is None:
                        first_timestamp = timestamp
                        cwd = cwd or data.get("cwd")

                    # First user message as fallback title
                    if entry_type == "user" and title is None:
                        msg = data.get("message", {})
                        content = msg.get("content", "")
                        if isinstance(content, str) and content:
                            first_line = content.split("\n")[0].strip()
                            if first_line:
                                if len(first_line) > max_title_chars:
                                    title = first_line[:max_title_chars] + "..."
                                else:
                                    title = first_line

                    # Always update last_timestamp for user/assistant messages
                    last_timestamp = timestamp
            except anyenv.JsonLoadError:
                continue

    if message_count == 0:
        return None

    return SessionMetadata(
        session_id=session_path.stem,
        path=session_path,
        first_timestamp=first_timestamp,
        last_timestamp=last_timestamp,
        cwd=cwd,
        message_count=message_count,
        title=title,
    )


def _parse_session_full(session_id: str, session_path: Path) -> ParsedSession | None:
    """Parse a session file fully, including message conversion.

    This is the CPU-intensive operation that benefits from parallelization
    on free-threaded Python. It combines:
    - File I/O (reading JSONL)
    - JSON parsing
    - Pydantic validation
    - Message conversion

    Args:
        session_id: Session identifier
        session_path: Path to the JSONL session file

    Returns:
        ParsedSession with all data, or None if session is empty
    """
    entries = read_session(session_path)
    if not entries:
        return None

    tool_mapping = _build_tool_id_mapping(entries)
    messages: list[ChatMessage[str]] = []
    first_timestamp: datetime | None = None
    usage = RunUsage()

    for entry in entries:
        msg = entry_to_chat_message(entry, session_id, tool_mapping)
        if msg is None:
            continue
        messages.append(msg)
        if first_timestamp is None and msg.timestamp:
            first_timestamp = msg.timestamp
        usage.incr(msg.usage)
    if not messages:
        return None

    return ParsedSession(
        session_id=session_id,
        path=session_path,
        entries=entries,
        tool_mapping=tool_mapping,
        messages=messages,
        first_timestamp=first_timestamp,
        usage=usage,
    )


class ClaudeStorageProvider(StorageProvider):
    """Storage provider that reads/writes Claude Code's native format.

    Claude stores conversations as JSONL files in:
    - ~/.claude/projects/{path-encoded-project-name}/{session-id}.jsonl

    Each line is a JSON object representing a message in the conversation.

    ## Fields NOT currently used from Claude format:
    - `isSidechain`: Whether message is on a side branch
    - `userType`: Type of user ("external", etc.)
    - `cwd`: Working directory at time of message
    - `gitBranch`: Git branch at time of message
    - `version`: Claude CLI version
    - `requestId`: API request ID
    - `agentId`: Agent identifier for subagents
    - `toolUseResult`: Detailed tool result content (we extract text only)
    - `parentUuid`: Parent message for threading (we use flat history)

    ## Additional Claude data not handled:
    - `~/.claude/todos/`: Todo lists per session
    - `~/.claude/plans/`: Markdown plan files
    - `~/.claude/skills/`: Custom skills
    - `~/.claude/history.jsonl`: Command/prompt history
    """

    can_load_history = True

    def __init__(self, config: ClaudeStorageConfig | None = None) -> None:
        """Initialize Claude storage provider.

        Args:
            config: Configuration for the provider
        """
        config = config or ClaudeStorageConfig()
        super().__init__(config)
        self.base_path = Path(config.path).expanduser()
        self.projects_path = self.base_path / "projects"
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        """Ensure required directories exist."""
        self.projects_path.mkdir(parents=True, exist_ok=True)

    def _get_project_dir(self, project_path: str) -> Path:
        """Get the directory for a project's conversations."""
        return self.projects_path / encode_project_path(project_path)

    def _find_session_path(self, session_id: str) -> Path | None:
        """Find the file path for a session by ID.

        Searches across all project directories.

        Args:
            session_id: Session ID (file stem)

        Returns:
            Path to the session file, or None if not found
        """
        for project_dir in self.projects_path.iterdir():
            if project_dir.is_dir():
                candidate = project_dir / f"{session_id}.jsonl"
                if candidate.exists():
                    return candidate
        return None

    def _list_sessions(self, project_path: str | None = None) -> list[tuple[str, Path]]:
        """List all sessions, optionally filtered by project.

        Returns:
            List of (session_id, file_path) tuples
        """
        if project_path and (project_dir := self._get_project_dir(project_path)).exists():
            return [(f.stem, f) for f in project_dir.glob("*.jsonl")]
        sessions = []
        for project_dir in self.projects_path.iterdir():
            if project_dir.is_dir():
                for f in project_dir.glob("*.jsonl"):
                    session_id = f.stem
                    sessions.append((session_id, f))
        return sessions

    def _sessions_containing(self, needle: str) -> Iterator[tuple[str, Path]]:
        """Yield the sessions whose transcript contains ``needle`` (e.g. a message UUID).

        A raw byte search, so only matching transcripts need to be parsed. A match is a
        candidate, not a guarantee: the text may also appear in another field.
        """
        key = needle.encode()
        for session_id, path in self._list_sessions():
            if _file_contains(path, key):
                yield session_id, path

    def list_session_metadata(self, project_path: str | None = None) -> list[SessionMetadata]:
        """List all sessions with lightweight metadata (no full message parsing).

        This is much faster than get_sessions() as it only reads timestamps
        and message counts without validating all message content.

        Args:
            project_path: Optional project directory to filter by

        Returns:
            List of SessionMetadata objects with timestamps and counts
        """
        # return [
        #     m
        #     for _, session_path in self._list_sessions(project_path=project_path)
        #     if (m := _read_session_metadata(session_path))
        # ]
        result: list[SessionMetadata] = []
        for _, session_path in self._list_sessions(project_path=project_path):
            if metadata := _read_session_metadata(session_path):
                result.append(metadata)
        return result

    async def list_session_ids(
        self,
        pool_id: str | None = None,
        agent_name: str | None = None,
    ) -> list[str]:
        """List session IDs from Claude storage."""
        return [sid for sid, _ in self._list_sessions()]

    async def load_session(self, session_id: str) -> SessionData | None:
        """Load session data from Claude storage metadata."""
        from agentpool.sessions.models import SessionData

        session_path = self._find_session_path(session_id)
        if not session_path:
            return None
        meta = _read_session_metadata(session_path)
        if not meta:
            return None
        now = get_now()
        created_at = (
            parse_iso_timestamp(meta.first_timestamp, fallback=now) if meta.first_timestamp else now
        )
        last_active = (
            parse_iso_timestamp(meta.last_timestamp, fallback=created_at)
            if meta.last_timestamp
            else created_at
        )
        return SessionData(
            session_id=meta.session_id,
            agent_name="claude_code",
            cwd=meta.cwd or "",
            created_at=created_at,
            last_active=last_active,
            metadata={"title": meta.title, "message_count": meta.message_count}
            if meta.title
            else {"message_count": meta.message_count},
        )

    async def filter_messages(self, query: SessionQuery) -> list[ChatMessage[str]]:
        """Filter messages based on query.

        Uses parallel parsing on free-threaded Python when querying
        across multiple sessions.
        """
        # Narrow session list when a specific session is requested
        if query.name:
            path = self._find_session_path(query.name)
            sessions = [(query.name, path)] if path else []
        else:
            sessions = self._list_sessions()

        if not sessions:
            return []

        # Parse sessions in parallel (CPU-intensive)
        parsed_sessions = parallel_map(
            lambda item: _parse_session_full(item[0], item[1]),
            sessions,
        )

        # Filter messages (fast, sequential)
        messages: list[ChatMessage[str]] = []
        cutoff = query.get_time_cutoff()
        until_dt = datetime.fromisoformat(query.until) if query.until else None

        for parsed in parsed_sessions:
            if parsed is None:
                continue

            for msg in parsed.messages:
                # Apply filters
                if query.agents and msg.name not in query.agents:
                    continue

                if query.since and cutoff and msg.timestamp and msg.timestamp < cutoff:
                    continue

                if until_dt and msg.timestamp and msg.timestamp > until_dt:
                    continue

                if query.contains and query.contains not in msg.content:
                    continue

                if query.roles and msg.role not in query.roles:
                    continue

                messages.append(msg)

                if query.limit and len(messages) >= query.limit:
                    return messages

        return messages

    async def log_message(self, *, message: ChatMessage[Any]) -> None:
        """Log a message to Claude format.

        Note: session_id should be in format "project_path:session_id"
        or just "session_id" (will use default project).
        """
        raw_session_id = message.session_id or ""
        # Parse session_id to extract project path
        if ":" in raw_session_id:
            project_path, session_id = raw_session_id.split(":", 1)
        else:
            project_path = "/tmp"
            session_id = raw_session_id

        # Convert to entry and write
        entry = chat_message_to_entry(message, session_id=session_id, cwd=project_path)
        session_path = self._get_project_dir(project_path) / f"{session_id}.jsonl"
        write_entry(session_path, entry)

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
        """Log a conversation start.

        In Claude format, conversations are implicit (created when first message is written).
        This is a no-op but could be extended to create an initial entry.
        """

    async def get_sessions(self, filters: QueryFilters) -> list[ConversationData]:
        """Get filtered conversations with their messages.

        Uses parallel parsing on free-threaded Python for better performance
        when processing many session files.
        """
        from agentpool_storage.models import ConversationData

        # Collect session list (lightweight - no parsing)
        sessions = self._list_sessions(project_path=filters.cwd)
        if not sessions:
            return []

        # Parse sessions in parallel (CPU-intensive Pydantic validation)
        # parallel_map automatically falls back to sequential on GIL-enabled Python
        parsed_sessions = parallel_map(
            lambda item: _parse_session_full(item[0], item[1]),
            sessions,
        )

        # Filter and build results (fast, sequential)
        result: list[ConversationData] = []
        for parsed in parsed_sessions:
            if parsed is None:
                continue

            # Apply filters
            if filters.agent_name and not any(
                m.name == filters.agent_name for m in parsed.messages
            ):
                continue
            if filters.since and parsed.first_timestamp and parsed.first_timestamp < filters.since:
                continue
            if filters.query and not any(filters.query in m.content for m in parsed.messages):
                continue

            conv_data = ConversationData(
                id=parsed.session_id,
                agent=parsed.messages[0].name or "claude",
                title=extract_title(parsed.path),
                start_time=(parsed.first_timestamp or get_now()).isoformat(),
                messages=parsed.messages,
                token_usage=parsed.usage,
            )

            result.append(conv_data)
            if filters.limit and len(result) >= filters.limit:
                break

        return result

    async def get_session_stats(self, filters: StatsFilters) -> dict[str, dict[str, Any]]:
        """Get conversation statistics."""
        stats: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"usage": RunUsage(), "messages": 0, "models": set()}
        )
        for _session_id, session_path in self._list_sessions():
            with session_path.open("r", encoding="utf-8") as f:
                for line in f:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        data = anyenv.load_json(stripped, return_type=dict)
                    except anyenv.JsonLoadError:
                        continue
                    if data.get("type") != "assistant":
                        continue
                    msg = data.get("message")
                    if not isinstance(msg, dict) or msg.get("type") != "message":
                        continue
                    model = normalize_model_name(msg.get("model", "unknown"))
                    raw_usage = msg.get("usage", {})
                    run_usage = RunUsage(
                        input_tokens=raw_usage.get("input_tokens", 0) or 0,
                        output_tokens=raw_usage.get("output_tokens", 0) or 0,
                        cache_read_tokens=raw_usage.get("cache_read_input_tokens", 0) or 0,
                        cache_write_tokens=raw_usage.get("cache_creation_input_tokens", 0) or 0,
                    )
                    timestamp_str = data.get("timestamp", "")
                    if not timestamp_str:
                        continue
                    timestamp = parse_iso_timestamp(timestamp_str)
                    if timestamp < filters.cutoff:
                        continue
                    # Group by specified criterion
                    match filters.group_by:
                        case "model":
                            key = model or "unknown"
                        case "hour":
                            key = timestamp.strftime("%Y-%m-%d %H:00")
                        case "day":
                            key = timestamp.strftime("%Y-%m-%d")
                        case _:
                            key = "claude"  # Default agent grouping

                    stats[key]["messages"] += 1
                    stats[key]["usage"] += run_usage
                    stats[key]["models"].add(model)

        # Convert sets to lists for JSON serialization
        for value in stats.values():
            value["models"] = list(value["models"])

        return dict(stats)

    async def reset(self, *, agent_name: str | None = None, hard: bool = False) -> tuple[int, int]:
        """Reset storage.

        Warning: This will delete Claude conversation files!
        """
        conv_count = 0
        msg_count = 0
        for _session_id, session_path in self._list_sessions():
            count = count_session_messages(session_path)
            msg_count += count
            conv_count += 1

            if hard or not agent_name:
                session_path.unlink(missing_ok=True)

        return conv_count, msg_count

    async def get_session_counts(self, *, agent_name: str | None = None) -> tuple[int, int]:
        """Get counts of conversations and messages."""
        conv_count = 0
        msg_count = 0
        for _session_id, session_path in self._list_sessions():
            if count := count_session_messages(session_path):
                conv_count += 1
                msg_count += count
        return conv_count, msg_count

    async def get_session_messages(
        self,
        session_id: str,
        *,
        include_ancestors: bool = False,
    ) -> list[ChatMessage[str]]:
        """Get all messages for a session.

        Args:
            session_id: Session ID (conversation ID in Claude format)
            include_ancestors: If True, traverse parent_uuid chain to include
                messages from ancestor conversations

        Returns:
            List of messages ordered by timestamp
        """
        # Find the session file
        session_path = next((p for sid, p in self._list_sessions() if sid == session_id), None)
        if not session_path:
            return []

        # Read entries and convert to messages
        entries = read_session(session_path)
        tool_mapping = _build_tool_id_mapping(entries)
        messages = [
            m for entry in entries if (m := entry_to_chat_message(entry, session_id, tool_mapping))
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
        """Get a single message by ID.

        Args:
            message_id: UUID of the message
            session_id: Optional session ID hint for faster lookup

        Returns:
            The message if found, None otherwise
        """
        # If session_id is provided, search only that session; otherwise only parse the
        # transcripts that contain the UUID at all.
        sessions = (
            [(session_id, p)]
            if session_id and (p := self._find_session_path(session_id))
            else self._sessions_containing(message_id)
        )
        for sid, session_path in sessions:
            entries = read_session(session_path)
            tool_mapping = _build_tool_id_mapping(entries)
            for entry in entries:
                if isinstance(entry, ClaudeEntry) and entry.uuid == message_id:
                    return entry_to_chat_message(entry, sid, tool_mapping)

        return None

    async def get_message_ancestry(
        self,
        message_id: str,
        *,
        session_id: str | None = None,
    ) -> list[ChatMessage[str]]:
        """Get the ancestry chain of a message.

        Traverses parent_uuid chain to build full history.
        When session_id is provided, loads the session once and traverses in-memory.

        Args:
            message_id: UUID of the message
            session_id: Optional session ID hint for faster lookup

        Returns:
            List of messages from oldest ancestor to the specified message
        """
        # Parent chains run through attachments and system entries, not only messages:
        # follow every chain entry, return the user and assistant messages on the way.
        # Fast path: if we know the session, load it once and walk in memory.
        if session_id and (session_path := self._find_session_path(session_id)):
            entries = read_session(session_path)
            tool_mapping = _build_tool_id_mapping(entries)
            chain = walk_ancestry(index_chain_entries(entries), message_id)
            return [
                msg
                for entry in reversed(chain)
                if isinstance(entry, ClaudeEntry)
                and (msg := entry_to_chat_message(entry, session_id, tool_mapping))
            ]

        # Slow path: the chain may span sessions. Parse only transcripts that contain the
        # UUID being looked up, each at most once, and index all their chain entries.
        by_uuid: dict[str, tuple[str, ClaudeChainEntry, dict[str, str]]] = {}
        parsed: set[Path] = set()
        seen: set[str] = set()  # parent chains can contain cycles
        ancestors: list[ChatMessage[str]] = []
        current_id = message_id
        while current_id and current_id not in seen:
            seen.add(current_id)
            if current_id not in by_uuid:
                for sid, path in self._sessions_containing(current_id):
                    if path in parsed:
                        continue
                    parsed.add(path)
                    entries = read_session(path)
                    mapping = _build_tool_id_mapping(entries)
                    for uuid, entry in index_chain_entries(entries).items():
                        by_uuid.setdefault(uuid, (sid, entry, mapping))
                    if current_id in by_uuid:
                        break
            if (located := by_uuid.get(current_id)) is None:
                break
            sid, entry, mapping = located
            if isinstance(entry, ClaudeEntry) and (
                msg := entry_to_chat_message(entry, sid, mapping)
            ):
                ancestors.append(msg)
            current_id = entry.parent_uuid
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
        """Fork a conversation at a specific point.

        Creates a new session file. The fork point message_id is returned
        so callers can set it as parent_uuid for new messages.

        Args:
            source_session_id: Source session ID
            new_session_id: New session ID
            fork_from_message_id: UUID to fork from. If None, forks from last message
            new_agent_name: Not used in Claude format (no agent metadata in sessions)

        Returns:
            The UUID of the fork point message
        """
        # Find source session
        sessions = self._list_sessions()
        source_path = next((spath for sid, spath in sessions if sid == source_session_id), None)
        if not source_path:
            raise ValueError(f"Source conversation not found: {source_session_id}")
        # Read source entries
        entries = read_session(source_path)
        # Find fork point
        fork_point_id: str | None = None
        if fork_from_message_id:
            # Verify message exists
            found = False
            for entry in entries:
                if isinstance(entry, ClaudeEntry) and entry.uuid == fork_from_message_id:
                    found = True
                    fork_point_id = fork_from_message_id
                    break
            if not found:
                err = f"Message {fork_from_message_id} not found in conversation"
                raise ValueError(err)
        # Find last message
        elif msg_entries := [e for e in entries if isinstance(e, ClaudeEntry)]:
            fork_point_id = msg_entries[-1].uuid

        # Create new session file (empty for now - will be populated when messages added)
        # Determine project from source path structure
        project_name = source_path.parent.name
        new_path = self.projects_path / project_name / f"{new_session_id}.jsonl"
        new_path.parent.mkdir(parents=True, exist_ok=True)
        new_path.touch()
        return fork_point_id


if __name__ == "__main__":
    provider = ClaudeStorageProvider()
    print(provider._list_sessions())
