"""Debug toolset for agent self-introspection and runtime debugging."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
import logging
from typing import Any, Literal

import anyenv
from pydantic_ai import RunContext  # noqa: TC002

from agentpool.agents.context import AgentContext  # noqa: TC001
from agentpool.resource_providers import StaticResourceProvider
from agentpool.utils.identifiers import script_slug


LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


@dataclass
class LogRecord:
    """A captured log record."""

    level: str
    logger: str
    message: str
    timestamp: str
    extra: dict[str, Any] = field(default_factory=dict)


LEVEL_PRIORITY = {
    "DEBUG": 10,
    "INFO": 20,
    "WARNING": 30,
    "ERROR": 40,
    "CRITICAL": 50,
}


class MemoryLogHandler(logging.Handler):
    """A logging handler that stores records in memory for later retrieval."""

    def __init__(self, max_records: int = 1000) -> None:
        super().__init__()
        self.max_records = max_records
        self.records: deque[LogRecord] = deque(maxlen=max_records)
        self.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        """Store the log record."""
        log_record = LogRecord(
            level=record.levelname,
            logger=record.name,
            message=self.format(record),
            timestamp=self.formatter.formatTime(record) if self.formatter else "",
            extra={k: v for k, v in record.__dict__.items() if k not in logging.LogRecord.__dict__},
        )
        self.records.append(log_record)

    def get_records(
        self,
        level: LogLevel | None = None,
        logger_filter: str | None = None,
        limit: int | None = None,
    ) -> list[LogRecord]:
        """Get filtered log records.

        Args:
            level: Minimum log level to include
            logger_filter: Only include loggers containing this string
            limit: Maximum number of records to return (newest first)

        Returns:
            List of matching log records
        """
        min_level = LEVEL_PRIORITY.get(level, 0) if level else 0
        filtered = []
        for record in reversed(self.records):
            if LEVEL_PRIORITY.get(record.level, 0) < min_level:
                continue
            if logger_filter and logger_filter not in record.logger:
                continue
            filtered.append(record)
            if limit and len(filtered) >= limit:
                break

        return filtered

    def clear(self) -> None:
        """Clear all stored records."""
        self.records.clear()


# Global memory handler instance
_memory_handler: MemoryLogHandler | None = None


def get_memory_handler() -> MemoryLogHandler:
    """Get or create the global memory log handler."""
    global _memory_handler  # noqa: PLW0603
    if _memory_handler is None:
        _memory_handler = MemoryLogHandler()
        # Attach to root logger to capture everything
        logging.getLogger().addHandler(_memory_handler)
    return _memory_handler


def install_memory_handler(max_records: int = 1000) -> MemoryLogHandler:
    """Install the memory handler if not already installed.

    Args:
        max_records: Maximum number of records to keep in memory

    Returns:
        The memory handler instance
    """
    global _memory_handler  # noqa: PLW0603
    if _memory_handler is None:
        _memory_handler = MemoryLogHandler(max_records=max_records)
        logging.getLogger().addHandler(_memory_handler)
    return _memory_handler


# =============================================================================
# Introspection Tool
# =============================================================================

INTROSPECTION_USAGE = """
Execute Python code with full access to your runtime context.

Available in namespace:
- ctx: AgentContext (ctx.agent, ctx.pool, ctx.config, ctx.definition, etc.)
- run_ctx: pydantic-ai RunContext (current run state)
- me: Shortcut for ctx.agent (your Agent instance)
- save(key, value): Save an object to persist between calls
- state: Dict of saved objects from previous calls

You can inspect yourself, the pool, other agents, your tools, and more.
Write an async main() function that returns the result.

Example - inspect your own tools:
```python
async def main():
    tools = await me.tools.get_tools()
    return [i.name for i in tools]
```

Example - check pool state:
```python
async def main():
    if ctx.pool:
        agents = list(ctx.pool.get_agents().keys())
        return f"Agents in pool: {agents}"
    return "No pool available"
```

Example - explore with dir():
```python
async def main():
    return dir(ctx)
```
"""


# =============================================================================
# Log Tools
# =============================================================================


async def get_logs(
    level: LogLevel = "INFO",
    logger_filter: str | None = None,
    limit: int = 50,
) -> str:
    """Get recent log entries from memory.

    Args:
        level: Minimum log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        logger_filter: Only show logs from loggers containing this string
        limit: Maximum number of log entries to return

    Returns:
        Formatted log entries
    """
    handler = get_memory_handler()
    records = handler.get_records(level=level, logger_filter=logger_filter, limit=limit)
    if not records:
        return "No log entries found matching criteria"

    lines = [f"=== {len(records)} log entries (newest first) ===\n"]
    for record in records:
        lines.append(record.message)  # noqa: PERF401

    return "\n".join(lines)


# =============================================================================
# Path Tools
# =============================================================================


async def get_platform_paths() -> str:
    """Get platform-specific paths for agentpool.

    Returns:
        Dictionary of platform paths
    """
    import platformdirs

    paths = {
        "config": platformdirs.user_config_dir("agentpool"),
        "data": platformdirs.user_data_dir("agentpool"),
        "cache": platformdirs.user_cache_dir("agentpool"),
        "logs": platformdirs.user_log_dir("agentpool"),
        "state": platformdirs.user_state_dir("agentpool"),
    }

    lines = ["Platform paths for agentpool:", ""]
    for name, path in paths.items():
        lines.append(f"  {name}: {path}")

    return "\n".join(lines)


# =============================================================================
# Toolset Class
# =============================================================================


class DebugTools(StaticResourceProvider):
    """Debug and introspection tools for agent development.

    Provides tools for:
    - Self-introspection via code execution with runtime context access
    - Log inspection and management
    - Platform path discovery
    - Stateful namespace for persisting objects between introspection calls
    """

    def __init__(self, name: str = "debug") -> None:
        """Initialize debug tools.

        Args:
            name: Toolset name/namespace
        """
        super().__init__(name=name)
        self._namespace_storage: dict[str, Any] = {}  # Stateful storage for introspection
        desc = (self.execute_introspection.__doc__ or "") + "\n\n" + INTROSPECTION_USAGE
        self._tools = [
            self.create_tool(
                self.execute_introspection, category="other", description_override=desc
            ),
            self.create_tool(get_logs, category="read", read_only=True, idempotent=True),
            self.create_tool(get_platform_paths, category="other", read_only=True, idempotent=True),
        ]

    async def execute_introspection(  # noqa: D417
        self, ctx: AgentContext, run_ctx: RunContext[Any], code: str, title: str
    ) -> str:
        """Execute Python code with access to your own runtime context.

        This is a debugging/development tool that gives you full access to
        inspect and interact with your runtime environment.

        Args:
            code: Python code with async main() function to execute
            title: Short descriptive title for this script (3-4 words)

        Returns:
            Result of execution or error message
        """
        # Emit progress with the code being executed
        await ctx.events.tool_call_progress(
            title="Executing introspection code",
            status="in_progress",
            items=[f"```python\n{code}\n```"],
        )

        # Build namespace with runtime context and stateful storage
        def save(key: str, value: Any) -> None:
            """Save a value to persist between introspection calls."""
            self._namespace_storage[key] = value

        namespace: dict[str, Any] = {
            "ctx": ctx,
            "run_ctx": run_ctx,
            "me": ctx.agent,
            "save": save,
            "state": self._namespace_storage.copy(),
        }
        start_time = datetime.now(UTC)
        exit_code = 0
        error_msg = None
        result_str = ""
        try:
            exec(code, namespace)
            if "main" not in namespace:
                return "Error: Code must define an async main() function"
            result = await namespace["main"]()
            result_str = (
                str(result)
                if result is not None
                else "Code executed successfully (no return value)"
            )

            await ctx.events.tool_call_progress(
                title="Executed introspection code successfully",
                status="in_progress",
                items=[f"```python\n{code}\n```\n\n```terminal\n{result_str}\n```"],
            )
        except Exception as e:  # noqa: BLE001
            exit_code = 1
            error_msg = f"{type(e).__name__}: {e}"
            result_str = error_msg
        finally:
            # Save to agent's internal filesystem
            timestamp = start_time.strftime("%Y%m%d_%H%M%S")
            slug = script_slug(title)
            # Write script file
            script_path = f"debug/scripts/{timestamp}_{slug}.py"
            ctx.internal_fs.pipe(script_path, code.encode())
            # Write metadata file
            metadata = {
                "title": title,
                "timestamp": start_time.isoformat(),
                "exit_code": exit_code,
                "duration": (datetime.now(UTC) - start_time).total_seconds(),
                "result": result_str,
                "error": error_msg,
            }
            metadata_path = f"debug/scripts/{timestamp}_{slug}.json"
            ctx.internal_fs.pipe(metadata_path, anyenv.dump_json(metadata, indent=True).encode())
        return result_str or "(empty result)"
