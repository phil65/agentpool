"""Filesystem interface for ChatMessage history.

Provides a read-only virtual filesystem view of conversation history,
allowing tools and agents to browse messages as files.
"""

from typing import TYPE_CHECKING, Any, Literal, Required, overload

import anyenv
from upathtools.filesystems.base import BaseAsyncFileSystem, BaseUPath, FileInfo


if TYPE_CHECKING:
    from agentpool.messaging.message_container import ChatMessageList


class ChatMessageInfo(FileInfo):
    """Info dict for ACP filesystem paths."""

    size: Required[int]


class ChatMessagePath(BaseUPath[ChatMessageInfo]):
    """Path for ACP filesystem."""

    __slots__ = ()


def _get_file_entries(messages: ChatMessageList) -> dict[str, bytes]:
    """Generate file entries from current messages."""
    entries: dict[str, bytes] = {}

    for msg in messages:
        timestamp = msg.timestamp.strftime("%Y%m%d_%H%M%S_%f")
        base_name = f"{timestamp}_{msg.role}_{msg.message_id}"

        # Content file
        content_path = f"/messages/{base_name}.txt"
        entries[content_path] = str(msg.content).encode("utf-8")

        # Metadata file
        metadata = {
            "message_id": msg.message_id,
            "role": msg.role,
            "timestamp": msg.timestamp.isoformat(),
            "parent_id": msg.parent_id,
            "model_name": msg.model_name,
            "tokens": msg.usage.total_tokens if msg.usage else None,
            "cost": float(msg.cost_info.total_cost) if msg.cost_info else None,
        }
        metadata_path = f"/messages/{base_name}.json"
        entries[metadata_path] = anyenv.dump_json(metadata, indent=True).encode()

    # Summary file
    summary = {
        "total_messages": len(messages),
        "total_tokens": messages.get_history_tokens(),
        "total_cost": messages.get_total_cost(),
        "roles": {
            "user": len([m for m in messages if m.role == "user"]),
            "assistant": len([m for m in messages if m.role == "assistant"]),
        },
    }
    entries["/summary.json"] = anyenv.dump_json(summary, indent=True).encode("utf-8")

    return entries


class ChatMessageFileSystem(BaseAsyncFileSystem[ChatMessagePath, ChatMessageInfo]):
    """Read-only filesystem exposing ChatMessages as files.

    Structure:
        /messages/{timestamp}_{role}_{id}.txt  - Message content
        /messages/{timestamp}_{role}_{id}.json - Message metadata
        /by_role/user/                         - User messages
        /by_role/assistant/                    - Assistant messages
        /summary.json                          - Conversation statistics

    The filesystem reads directly from the ChatMessageList reference,
    so it always reflects the current state without needing refresh.
    """

    protocol = "chatmsg"
    root_marker = "/"
    async_impl = True

    def __init__(
        self,
        messages: ChatMessageList,
        **kwargs: Any,
    ) -> None:
        """Initialize filesystem with message list.

        Args:
            messages: ChatMessageList to expose as filesystem
            **kwargs: Additional arguments for AsyncFileSystem
        """
        super().__init__(**kwargs)
        self._messages = messages

    def _get_dirs(self) -> set[str]:
        """Get all virtual directories."""
        return {"/", "/messages", "/by_role", "/by_role/user", "/by_role/assistant"}

    def _normalize_path(self, path: str) -> str:
        """Normalize path to consistent format."""
        if not path.startswith("/"):
            path = "/" + path
        return path.rstrip("/") or "/"

    @overload
    async def _ls(
        self, path: str, detail: Literal[True] = ..., **kwargs: Any
    ) -> list[ChatMessageInfo]: ...

    @overload
    async def _ls(self, path: str, detail: Literal[False], **kwargs: Any) -> list[str]: ...

    async def _ls(
        self,
        path: str,
        detail: bool = True,
        **kwargs: Any,
    ) -> list[ChatMessageInfo] | list[str]:
        """List directory contents."""
        path = self._normalize_path(path)
        file_entries = _get_file_entries(self._messages)

        entries: list[ChatMessageInfo] = []
        match path:
            case "/":
                entries = [
                    ChatMessageInfo(name="/messages", type="directory", size=0),
                    ChatMessageInfo(name="/by_role", type="directory", size=0),
                    ChatMessageInfo(
                        name="/summary.json",
                        type="file",
                        size=len(file_entries.get("/summary.json", b"")),
                    ),
                ]
            case "/messages":
                for file_path, content in file_entries.items():
                    if file_path.startswith("/messages/"):
                        entries.append(
                            ChatMessageInfo(name=file_path, type="file", size=len(content))
                        )
            case "/by_role":
                entries = [
                    ChatMessageInfo(name="/by_role/user", type="directory", size=0),
                    ChatMessageInfo(name="/by_role/assistant", type="directory", size=0),
                ]
            case "/by_role/user":
                for file_path, content in file_entries.items():
                    if file_path.startswith("/messages/") and "_user_" in file_path:
                        entries.append(
                            ChatMessageInfo(name=file_path, type="file", size=len(content))
                        )
            case "/by_role/assistant":
                for file_path, content in file_entries.items():
                    if file_path.startswith("/messages/") and "_assistant_" in file_path:
                        entries.append(
                            ChatMessageInfo(name=file_path, type="file", size=len(content))
                        )

        return entries if detail else [e["name"] for e in entries]

    async def _cat_file(
        self,
        path: str,
        start: int | None = None,
        end: int | None = None,
        **kwargs: Any,
    ) -> bytes:
        """Read file content."""
        path = self._normalize_path(path)
        file_entries = _get_file_entries(self._messages)
        if path in file_entries:
            return file_entries[path]
        raise FileNotFoundError(f"File not found: {path}")

    async def _info(self, path: str, **kwargs: Any) -> ChatMessageInfo:
        """Get file/directory info."""
        path = self._normalize_path(path)

        if path in self._get_dirs():
            return ChatMessageInfo(name=path, type="directory", size=0)

        file_entries = _get_file_entries(self._messages)
        if path in file_entries:
            return ChatMessageInfo(name=path, type="file", size=len(file_entries[path]))

        raise FileNotFoundError(f"Path not found: {path}")

    async def _exists(self, path: str, **kwargs: Any) -> bool:
        """Check if path exists."""
        path = self._normalize_path(path)
        return path in self._get_dirs() or path in _get_file_entries(self._messages)

    async def _isdir(self, path: str) -> bool:
        """Check if path is a directory."""
        path = self._normalize_path(path)
        return path in self._get_dirs()

    async def _isfile(self, path: str) -> bool:
        """Check if path is a file."""
        path = self._normalize_path(path)
        return path in _get_file_entries(self._messages)

    # Write operations - all raise since this is read-only

    async def _rm_file(self, path: str, **kwargs: Any) -> None:
        """Remove file - not supported."""
        raise PermissionError("ChatMessageFileSystem is read-only")

    async def _mkdir(
        self,
        path: str,
        create_parents: bool = True,
        **kwargs: Any,
    ) -> None:
        """Create directory - not supported."""
        raise PermissionError("ChatMessageFileSystem is read-only")

    async def _makedirs(self, path: str, exist_ok: bool = False) -> None:
        """Create directories - not supported."""
        raise PermissionError("ChatMessageFileSystem is read-only")

    async def _pipe_file(
        self,
        path: str,
        value: bytes,
        mode: str = "overwrite",
        **kwargs: Any,
    ) -> None:
        """Write file - not supported."""
        raise PermissionError("ChatMessageFileSystem is read-only")

    async def _put_file(
        self,
        lpath: str,
        rpath: str,
        mode: str = "overwrite",
        **kwargs: Any,
    ) -> None:
        """Upload file - not supported."""
        raise PermissionError("ChatMessageFileSystem is read-only")

    async def _cp_file(self, path1: str, path2: str, **kwargs: Any) -> None:
        """Copy file - not supported."""
        raise PermissionError("ChatMessageFileSystem is read-only")

    async def _mv_file(self, path1: str, path2: str, **kwargs: Any) -> None:
        """Move file - not supported."""
        raise PermissionError("ChatMessageFileSystem is read-only")
