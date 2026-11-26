"""Zed IDE storage provider.

This package implements a read-only storage backend that reads Zed IDE's
native thread format from ~/.local/share/zed/threads/threads.db.

Zed stores conversations as zstd-compressed JSON in a SQLite database.
This provider enables importing and analyzing Zed conversations.
"""

from agentpool_storage.zed_provider.provider import ZedStorageProvider

__all__ = [
    "ZedStorageProvider",
]
