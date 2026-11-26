"""Logging configuration for agentpool with structlog support."""

from contextlib import contextmanager
import logging
from logging.handlers import RotatingFileHandler
import sys
from typing import TYPE_CHECKING, Any

import logfire
import structlog


if TYPE_CHECKING:
    from collections.abc import Generator, MutableMapping, Sequence

    from slashed import OutputWriter


LogLevel = int | str


_LOGGING_CONFIGURED = False


_plain_traceback = structlog.dev.plain_traceback
"""How logged exceptions render: plain text.

structlog's default (rich, with every frame's local variables) takes seconds per
exception (an agent's locals have large reprs), and logging blocks the event loop
meanwhile; rich without locals still takes about 3 seconds.
"""


def _pydantic_processor(
    logger: Any, method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Convert Pydantic models to dicts for logging."""
    from pydantic import BaseModel

    for key, value in event_dict.items():
        if isinstance(value, BaseModel):
            event_dict[key] = value.model_dump(exclude_defaults=True)
    return event_dict


def configure_logging(
    level: LogLevel = "INFO",
    *,
    use_colors: bool | None = None,
    json_logs: bool = False,
    force: bool = False,
    log_file: str | None = None,
) -> None:
    """Configure structlog and standard logging.

    Args:
        level: Logging level
        use_colors: Whether to use colored output (auto-detected if None)
        json_logs: Force JSON output regardless of TTY detection
        force: Force reconfiguration even if already configured
        log_file: Optional file path to write logs to instead of stderr
    """
    global _LOGGING_CONFIGURED  # noqa: PLW0603

    if _LOGGING_CONFIGURED and not force:
        return

    if isinstance(level, str):
        level = getattr(logging, level.upper())

    if log_file:
        _configure_file_logging(level, log_file)
    else:
        _configure_console_logging(level, use_colors=use_colors, json_logs=json_logs)

    _LOGGING_CONFIGURED = True


def _configure_file_logging(level: LogLevel, log_file: str, max_lines: int = 5000) -> None:
    """Configure logging to write to a file with human-readable format and line-based rotation.

    Args:
        level: Logging level
        log_file: Path to log file
        max_lines: Maximum number of lines before rotation (default: 5000)
    """
    if isinstance(level, str):
        level = getattr(logging, level.upper())

    # Estimate bytes per line (assuming ~200 chars average)
    max_bytes = max_lines * 200

    # Set up rotating file handler with 3 backup files
    handler = RotatingFileHandler(log_file, maxBytes=max_bytes, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    logging.basicConfig(level=level, handlers=[handler], force=True)

    # Configure structlog processors for file output
    processors: list[Any] = [
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.PositionalArgumentsFormatter(),
        _pydantic_processor,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        logfire.StructlogProcessor(),  # Capture logs for observability
        structlog.dev.ConsoleRenderer(colors=False, exception_formatter=_plain_traceback),
    ]

    structlog.configure(
        processors=processors,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def _configure_console_logging(
    level: LogLevel, *, use_colors: bool | None = None, json_logs: bool = False
) -> None:
    """Configure logging to stderr with console or JSON format."""
    if isinstance(level, str):
        level = getattr(logging, level.upper())

    # Determine output format
    colors = sys.stderr.isatty() and not json_logs if use_colors is None else use_colors or False
    use_console_renderer = not json_logs and (colors or sys.stderr.isatty())
    # Configure standard logging as backend
    handler = logging.StreamHandler(sys.stderr)
    if use_console_renderer:
        # For console output, don't show level in stdlib logging (structlog handles it)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logging.basicConfig(level=level, handlers=[handler], force=True)
    else:  # For structured output, use minimal formatting
        logging.basicConfig(level=level, handlers=[handler], force=True, format="%(message)s")
    processors: list[Any] = [
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        _pydantic_processor,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    # Add logger name only for non-console renderers (avoid duplication with stdlib)
    if not use_console_renderer:
        processors.insert(1, structlog.stdlib.add_logger_name)
        processors.append(structlog.processors.format_exc_info)

    # Add logfire processor before final renderer to capture logs for observability
    processors.append(logfire.StructlogProcessor())

    # Add final renderer
    if use_console_renderer:
        processors.append(
            structlog.dev.ConsoleRenderer(colors=colors, exception_formatter=_plain_traceback)
        )
    else:
        processors.append(structlog.processors.JSONRenderer())

    structlog.configure(
        processors=processors,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def get_logger(name: str, log_level: LogLevel | None = None) -> structlog.stdlib.BoundLogger:
    """Get a structlog logger for the given name.

    Args:
        name: The name of the logger, will be prefixed with 'agentpool.'
        log_level: The logging level to set for the logger

    Returns:
        A structlog BoundLogger instance
    """
    # Ensure basic structlog configuration exists for tests
    if not _LOGGING_CONFIGURED and not structlog.is_configured():
        # Minimal configuration that doesn't interfere with stdio
        structlog.configure(
            processors=[
                structlog.stdlib.filter_by_level,
                structlog.stdlib.add_log_level,
                _pydantic_processor,
                structlog.processors.StackInfoRenderer(),
                logfire.StructlogProcessor(),
                structlog.dev.ConsoleRenderer(colors=False, exception_formatter=_plain_traceback),
            ],
            wrapper_class=structlog.stdlib.BoundLogger,
            logger_factory=structlog.stdlib.LoggerFactory(),
            cache_logger_on_first_use=True,
        )

    logger = structlog.get_logger(f"agentpool.{name}")
    if log_level is not None:
        if isinstance(log_level, str):
            log_level = getattr(logging, log_level.upper())
            assert log_level
        # Set level on underlying stdlib logger
        stdlib_logger = logging.getLogger(f"agentpool.{name}")
        stdlib_logger.setLevel(log_level)
    return logger  # type: ignore[no-any-return]


@contextmanager
def set_handler_level(
    level: int,
    logger_names: Sequence[str],
    *,
    session_handler: OutputWriter | None = None,
) -> Generator[None]:
    """Temporarily set logging level and optionally add session handler.

    Args:
        level: Logging level to set
        logger_names: Names of loggers to configure
        session_handler: Optional output writer for session logging
    """
    loggers = [logging.getLogger(name) for name in logger_names]
    old_levels = [logger.level for logger in loggers]

    handler = None
    if session_handler:
        from slashed.log import SessionLogHandler

        handler = SessionLogHandler(session_handler)
        for logger in loggers:
            logger.addHandler(handler)

    try:
        for logger in loggers:
            logger.setLevel(level)
        yield
    finally:
        for logger, old_level in zip(loggers, old_levels, strict=True):
            logger.setLevel(old_level)
            if handler:
                logger.removeHandler(handler)
