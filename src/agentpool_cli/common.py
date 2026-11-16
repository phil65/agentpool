"""Common utilities for the CLI."""

from __future__ import annotations

from datetime import datetime
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys
from typing import TYPE_CHECKING, Any, Literal, assert_never

import platformdirs
import typer as t


if TYPE_CHECKING:
    from collections.abc import Sequence

OutputFormat = Literal["text", "json", "yaml"]

CONFIG_HELP = "Path to config file or name of stored config"
OUTPUT_FORMAT_HELP = "Output format. One of: text, json, yaml"
VERBOSE_HELP = "Enable debug logging"
# Command options
OUTPUT_FORMAT_CMDS = "-o", "--output-format"
VERBOSE_CMDS = "-v", "--verbose"

LOG_DIR = Path(platformdirs.user_log_dir("agentpool", "agentpool"))
TIMESTAMP = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
LOG_FILE = LOG_DIR / f"agentpool_{TIMESTAMP}.log"

MAX_LOG_SIZE = 10 * 1024 * 1024  # (10MB)

BACKUP_COUNT = 5


def setup_logging(
    *,
    level: int | str = logging.INFO,
    handlers: Sequence[logging.Handler] | None = None,
    format_string: str | None = None,
    log_to_file: bool = True,
) -> None:
    """Configure logging.

    Args:
        level: The logging level for console output
        handlers: Optional sequence of handlers to add
        format_string: Optional custom format string
        log_to_file: Whether to log to file in addition to stdout
    """
    logger = logging.getLogger("agentpool")
    logger.setLevel(logging.DEBUG)
    format_string = format_string or "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    formatter = logging.Formatter(format_string)
    if not handlers:
        handlers = []
        # Add stdout handler with user-specified level
        stdout_handler = logging.StreamHandler(sys.stdout)
        stdout_handler.setFormatter(formatter)
        stdout_handler.setLevel(level)
        handlers.append(stdout_handler)
        # Add file handler if requested (always DEBUG level)
        if log_to_file:
            try:
                LOG_DIR.mkdir(parents=True, exist_ok=True)
                file_handler = RotatingFileHandler(
                    LOG_FILE,
                    maxBytes=MAX_LOG_SIZE,
                    backupCount=BACKUP_COUNT,
                    encoding="utf-8",
                )
                file_handler.setFormatter(formatter)
                file_handler.setLevel(logging.DEBUG)
                handlers.append(file_handler)
            except Exception as exc:  # noqa: BLE001
                msg = f"Failed to create log file: {exc}"
                print(msg, file=sys.stderr)

    for handler in handlers:
        if not handler.formatter:
            handler.setFormatter(formatter)
        logger.addHandler(handler)

    logger.info("Logging initialized")
    if log_to_file:
        msg = "Console logging level: %s, File logging level: DEBUG (%s)"
        level_name = level if isinstance(level, str) else logging.getLevelName(level)
        logger.debug(msg, level_name, LOG_FILE)


def complete_config_names() -> list[str]:
    """Complete stored config names."""
    from agentpool_cli import agent_store

    return [name for name, _ in agent_store.list_configs()]


def complete_output_formats() -> list[str]:
    """Complete output format options."""
    return ["text", "json", "yaml"]


def verbose_callback(ctx: t.Context, _param: t.CallbackParam, value: bool) -> bool:
    """Handle verbose flag."""
    if value:
        setup_logging(level=logging.DEBUG)
    return value


output_format_opt = t.Option(
    "text",
    *OUTPUT_FORMAT_CMDS,
    help=OUTPUT_FORMAT_HELP,
    autocompletion=complete_output_formats,
)
verbose_opt = t.Option(False, *VERBOSE_CMDS, help=VERBOSE_HELP, callback=verbose_callback)


def format_output(result: Any, output_format: OutputFormat = "text") -> None:
    """Format and print data in the requested format using TypeAdapter.

    Args:
        result: Any object to format
        output_format: One of: text, json, yaml
    """
    from pydantic import TypeAdapter
    from rich.console import Console

    adapter = TypeAdapter(type(result))
    data = adapter.dump_python(result)
    console = Console()
    match output_format:
        case "json":
            print(json.dumps(data, indent=2, default=str))
        case "yaml":
            import yamling

            print(yamling.dump_yaml(data))
        case "text":
            console.print(data)
        case _ as unreachable:
            assert_never(unreachable)


if __name__ == "__main__":
    from dataclasses import dataclass

    @dataclass
    class Person:
        """Test class."""

        name: str
        age: int

    people = [Person("Alice", 30), Person("Bob", 25)]

    print("=== JSON ===")
    format_output(people, "json")
    print("\n=== YAML ===")
    format_output(people, "yaml")
    print("\n=== TEXT ===")
    format_output(people, "text")
