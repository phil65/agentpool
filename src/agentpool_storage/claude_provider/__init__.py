"""Claude Code storage provider.

This package implements the storage backend compatible with Claude Code's
filesystem-based persistence format.

See ARCHITECTURE.md for detailed documentation of the storage format and
design decisions.
"""

from agentpool_storage.claude_provider.provider import ClaudeStorageProvider, SessionMetadata

__all__ = ["ClaudeStorageProvider", "SessionMetadata"]
