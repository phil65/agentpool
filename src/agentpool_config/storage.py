"""Storage configuration."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Final, Literal

from platformdirs import user_data_dir
from pydantic import ConfigDict, Field, SecretStr
from schemez import Schema
from tokonomics.model_names import ModelId
from yamling import FormatType


if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

    from agentpool_storage.base import StorageProvider


FilterMode = Literal["and", "override"]

APP_NAME: Final = "agentpool"
APP_AUTHOR: Final = "agentpool"
DATA_DIR: Final = Path(user_data_dir(APP_NAME, APP_AUTHOR))
DEFAULT_DB_NAME: Final = "history.db"
DEFAULT_TITLE_PROMPT: Final = """\
Generate metadata for this conversation request. Provide:
- A short, descriptive title (3-7 words)
- A single emoji that represents the topic
- An iconify icon name (e.g., 'mdi:code-braces', 'mdi:database', 'mdi:bug')"""


def get_database_path() -> str:
    """Get the database file path, creating directories if needed."""
    db_path = DATA_DIR / DEFAULT_DB_NAME
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{db_path}"


# Shared engine cache - ensures one engine per database URL
_engine_cache: dict[str, AsyncEngine] = {}


def is_pytest() -> bool:
    """Check if running under pytest (must be called at runtime, not import time)."""
    return bool(os.getenv("PYTEST_CURRENT_TEST"))


def get_shared_engine(url: str, pool_size: int = 5) -> AsyncEngine:
    """Get or create a shared engine for the given database URL.

    This ensures all storage instances using the same database share
    a single engine and connection pool, avoiding lock contention.
    """
    from sqlalchemy.ext.asyncio import create_async_engine

    if url in _engine_cache:
        return _engine_cache[url]

    # Convert URL to async format
    url_str = str(url)
    if url_str.startswith("sqlite://"):
        url_str = url_str.replace("sqlite://", "sqlite+aiosqlite://", 1)
    elif url_str.startswith("postgresql://"):
        url_str = url_str.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif url_str.startswith("mysql://"):
        url_str = url_str.replace("mysql://", "mysql+aiomysql://", 1)

    # SQLite doesn't support pool_size parameter
    if url_str.startswith("sqlite+aiosqlite://"):
        engine = create_async_engine(url_str)
    else:
        engine = create_async_engine(url_str, pool_size=pool_size)

    _engine_cache[url] = engine
    return engine


class BaseStorageProviderConfig(Schema):
    """Base storage provider configuration."""

    type: str = Field(init=False)
    """Storage provider type."""

    log_messages: bool = Field(default=True, title="Log messages")
    """Whether to log messages"""

    agents: set[str] | None = Field(default=None, title="Agent filter")
    """Optional set of agent names to include. If None, logs all agents."""

    log_sessions: bool = Field(default=True, title="Log conversations")
    """Whether to log conversations"""

    log_commands: bool = Field(default=True, title="Log commands")
    """Whether to log command executions"""

    def get_provider(self) -> StorageProvider:
        """Create a storage provider instance from this configuration."""
        raise NotImplementedError


class SQLStorageConfig(BaseStorageProviderConfig):
    """SQL database storage configuration."""

    model_config = ConfigDict(json_schema_extra={"x-doc-title": "SQL Storage"})

    type: Literal["sql"] = Field("sql", init=False)
    """SQLModel storage configuration."""

    url: str = Field(
        default_factory=get_database_path,
        examples=["sqlite:///history.db", "postgresql://user:pass@localhost/db"],
        title="Database URL",
    )
    """Database URL (e.g. sqlite:///history.db)"""

    pool_size: int = Field(default=5, ge=1, examples=[5, 10, 20], title="Connection pool size")
    """Connection pool size"""

    auto_migration: bool = Field(default=True, title="Auto migration")
    """Whether to automatically add missing columns"""

    def get_engine(self) -> AsyncEngine:
        return get_shared_engine(self.url, self.pool_size)

    def get_provider(self) -> StorageProvider:
        """Create a SQL storage provider instance."""
        from agentpool_storage.sql_provider import SQLModelProvider

        return SQLModelProvider(self)


class FileStorageConfig(BaseStorageProviderConfig):
    """File storage configuration."""

    model_config = ConfigDict(json_schema_extra={"x-doc-title": "File Storage"})

    type: Literal["file"] = Field("file", init=False)
    """File storage configuration."""

    path: str = Field(examples=["/data/storage.json", "~/data.yaml"], title="Storage file path")
    """Path to storage file (extension determines format unless specified)"""

    format: FormatType = Field(default="auto", examples=["auto", "yaml"], title="Storage format")
    """Storage format (auto=detect from extension)"""

    encoding: str = Field(default="utf-8", examples=["utf-8", "ascii"], title="File encoding")
    """File encoding of the storage file."""

    def get_provider(self) -> StorageProvider:
        """Create a file storage provider instance."""
        from agentpool_storage.file_provider import FileProvider

        return FileProvider(self)


class MemoryStorageConfig(BaseStorageProviderConfig):
    """In-memory storage configuration for testing."""

    model_config = ConfigDict(json_schema_extra={"x-doc-title": "Memory Storage"})

    type: Literal["memory"] = Field("memory", init=False)
    """In-memory storage configuration for testing."""

    def get_provider(self) -> StorageProvider:
        """Create a memory storage provider instance."""
        from agentpool_storage.memory_provider import MemoryStorageProvider

        return MemoryStorageProvider(self)


class ClaudeStorageConfig(BaseStorageProviderConfig):
    """Claude Code native storage format configuration.

    Reads/writes to Claude Code's native JSONL format in ~/.claude/projects/.
    Useful for sharing conversation history between agentpool and Claude Code CLI.
    """

    model_config = ConfigDict(json_schema_extra={"x-doc-title": "Claude Storage"})

    type: Literal["claude"] = Field("claude", init=False)
    """Claude Code native storage configuration."""

    path: str = Field(
        default="~/.claude",
        examples=["~/.claude", "/home/user/.claude"],
        title="Claude data directory",
    )
    """Path to Claude data directory (default: ~/.claude)"""

    def get_provider(self) -> StorageProvider:
        """Create a Claude storage provider instance."""
        from agentpool_storage.claude_provider import ClaudeStorageProvider

        return ClaudeStorageProvider(self)


class ZedStorageConfig(BaseStorageProviderConfig):
    """Zed IDE native storage format configuration.

    Reads from Zed's native SQLite + zstd-compressed JSON format.
    Useful for importing conversation history from Zed's AI assistant.

    This is a READ-ONLY provider - it cannot write back to Zed's format.
    """

    model_config = ConfigDict(json_schema_extra={"x-doc-title": "Zed Storage"})

    type: Literal["zed"] = Field("zed", init=False)
    """Zed IDE native storage configuration."""

    path: str = Field(
        default="~/.local/share/zed/threads/threads.db",
        examples=["~/.local/share/zed/threads/threads.db", "~/.local/share/zed"],
        title="Zed threads database path",
    )
    """Path to Zed threads database (or parent directory)."""

    def get_provider(self) -> StorageProvider:
        """Create a Zed storage provider instance."""
        from agentpool_storage.zed_provider import ZedStorageProvider

        return ZedStorageProvider(self)


class ACPStorageConfig(BaseStorageProviderConfig):
    """ACP server storage configuration.

    Read-only provider that queries a connected ACP server for session history.
    The connection is established at runtime by the agent, not from config.
    """

    model_config = ConfigDict(json_schema_extra={"x-doc-title": "ACP Storage"})

    type: Literal["acp"] = Field("acp", init=False)
    """ACP server storage configuration."""

    def get_provider(self) -> StorageProvider:
        """Create an ACP storage provider instance."""
        from agentpool_storage.acp_provider import ACPStorageProvider

        return ACPStorageProvider(self)


class CodexStorageConfig(BaseStorageProviderConfig):
    """Codex server storage configuration.

    Read-only provider that queries a connected Codex server for session history.
    The connection is established at runtime by the agent, not from config.
    """

    model_config = ConfigDict(json_schema_extra={"x-doc-title": "Codex Storage"})

    type: Literal["codex"] = Field("codex", init=False)
    """Codex server storage configuration."""

    def get_provider(self) -> StorageProvider:
        """Create a Codex storage provider instance."""
        from agentpool_storage.codex_provider import CodexStorageProvider

        return CodexStorageProvider(self)


class T3StorageConfig(BaseStorageProviderConfig):
    """T3 Code storage configuration.

    Read-only provider for the threads of a T3 Code environment, read over its MCP
    endpoint (``t3_thread_list`` / ``t3_thread_read``), so the server must be running.
    Signs in like a T3 agent: ``url`` with ``token``, else a sign-in saved with
    ``pyt3 login`` (``login`` picks one).
    """

    model_config = ConfigDict(json_schema_extra={"x-doc-title": "T3 Code Storage"})

    type: Literal["t3"] = Field("t3", init=False)
    """T3 Code storage configuration."""

    login: str | None = Field(default=None, examples=["homelab"], title="Saved sign-in")
    """The ``pyt3 login`` sign-in to use (the only or default one when unset)."""

    url: str | None = Field(
        default=None, examples=["https://t3.example.ts.net"], title="Environment URL"
    )
    """The environment's URL (with ``token``, instead of a saved sign-in)."""

    token: SecretStr | None = Field(default=None, examples=["${T3_TOKEN}"], title="Access token")
    """An access token for ``url``."""

    project: str | None = Field(
        default=None, examples=["/home/me/src/app", "proj_123"], title="Project"
    )
    """The project whose threads to read (an id, or its folder on the T3 host); all when unset."""

    agent_name: str = Field(default="t3", title="Agent name")
    """The agent the threads are listed under."""

    def get_provider(self) -> StorageProvider:
        """Create a T3 Code storage provider instance."""
        from agentpool_storage.t3_provider import T3StorageProvider

        return T3StorageProvider(self)


StorageProviderConfig = Annotated[
    SQLStorageConfig
    | FileStorageConfig
    | MemoryStorageConfig
    | ClaudeStorageConfig
    | ZedStorageConfig
    | ACPStorageConfig
    | CodexStorageConfig
    | T3StorageConfig,
    Field(discriminator="type"),
]


class StorageConfig(Schema):
    """Global storage configuration.

    Docs: https://phil65.github.io/agentpool/YAML%20Configuration/storage_configuration/
    """

    providers: list[StorageProviderConfig] | None = Field(
        default=None,
        title="Storage providers",
        examples=[[{"type": "file", "path": "/data/storage.json"}]],
    )
    """List of configured storage providers"""

    default_provider: str | None = Field(
        default=None,
        examples=["sql", "file", "memory"],
        title="Default provider",
    )
    """Name of default provider for history queries.
    If None, uses first configured provider."""

    agents: set[str] | None = Field(default=None, title="Global agent filter")
    """Global agent filter. Can be overridden by provider-specific filters."""

    filter_mode: FilterMode = Field(
        default="and",
        examples=["and", "override"],
        title="Filter mode",
    )
    """How to combine global and provider agent filters:
    - "and": Both global and provider filters must allow the agent
    - "override": Provider filter overrides global filter if set
    """

    log_messages: bool = Field(default=True, title="Log messages")
    """Whether to log messages."""

    log_sessions: bool = Field(default=True, title="Log conversations")
    """Whether to log conversations."""

    log_commands: bool = Field(default=True, title="Log commands")
    """Whether to log command executions."""

    title_generation_model: ModelId | str | None = Field(
        default="google-gla:gemini-2.5-flash-lite,openrouter:deepseek/deepseek-r1-0528:free",
        examples=[
            "google-gla:gemini-2.5-flash-lite",
            "google-gla:gemini-2.5-flash-lite,openrouter:deepseek/deepseek-r1-0528:free",
            None,
        ],
        title="Title generation model",
    )
    """Model to use for generating conversation titles.
    Set to None to disable automatic title generation."""

    title_generation_prompt: str = Field(
        default=DEFAULT_TITLE_PROMPT,
        examples=[DEFAULT_TITLE_PROMPT, "Summarize this given request in 5 words"],
        title="Title generation prompt",
    )
    """Prompt template for generating conversation titles."""

    model_config = ConfigDict(frozen=True)

    @property
    def effective_providers(self) -> list[StorageProviderConfig]:
        """Get effective list of providers.

        Returns:
            - Default SQLite provider if providers is None
            - Empty list if providers is empty list
            - Configured providers otherwise
        """
        if self.providers is None:
            return [MemoryStorageConfig()] if is_pytest() else [SQLStorageConfig()]
        return self.providers
