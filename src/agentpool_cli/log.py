"""Lightweight logging configuration for CLI startup.

This is a simplified copy of agentpool.log to avoid importing the full
agentpool package (which pulls in pydantic-ai, llmling-models, etc.)
just for CLI help text.
"""

import logging
import sys
from typing import Any

import structlog


_LOGGING_CONFIGURED = False


def configure_logging(
    level: str | int = "INFO",
    *,
    use_colors: bool | None = None,
) -> None:
    """Configure structlog and standard logging for CLI.

    Args:
        level: Logging level (e.g., "INFO", "DEBUG")
        use_colors: Whether to use colored output (auto-detected if None)
    """
    global _LOGGING_CONFIGURED  # noqa: PLW0603

    if _LOGGING_CONFIGURED:
        return

    if isinstance(level, str):
        level = getattr(logging, level.upper())

    # Determine if we should use colors
    colors = sys.stderr.isatty() if use_colors is None else use_colors

    # Configure standard logging
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logging.basicConfig(level=level, handlers=[handler], force=True)

    # Configure structlog
    processors: list[Any] = [
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
        structlog.dev.ConsoleRenderer(colors=colors),
    ]

    structlog.configure(
        processors=processors,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    _LOGGING_CONFIGURED = True


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Get a structlog logger for the given name.

    Args:
        name: The name of the logger

    Returns:
        A structlog BoundLogger instance
    """
    return structlog.get_logger(name)  # type: ignore[no-any-return]
