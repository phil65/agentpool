"""Logged exceptions render as plain tracebacks: rich ones take seconds and block the loop."""

import logging
import sys
from typing import TYPE_CHECKING

import structlog

from agentpool import log


if TYPE_CHECKING:
    import pytest


def _explode() -> None:
    raise RuntimeError("tool exploded")


def test_the_console_renderer_renders_plain_tracebacks(monkeypatch: pytest.MonkeyPatch) -> None:
    saved = structlog.get_config()
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    monkeypatch.setattr(log, "_LOGGING_CONFIGURED", False)
    try:
        log.configure_logging("INFO", use_colors=True, force=True)
        renderer = structlog.get_config()["processors"][-1]
    finally:
        structlog.configure(**saved)
        root.handlers[:] = handlers
        root.setLevel(level)
    assert isinstance(renderer, structlog.dev.ConsoleRenderer)
    try:
        _explode()
    except RuntimeError:
        event = {"event": "Agent stream failed", "exc_info": sys.exc_info()}
        output = renderer(None, "error", event)
    assert isinstance(output, str)
    assert "Traceback (most recent call last)" in output
    assert "RuntimeError: tool exploded" in output
    assert "╭" not in output  # rich's framed traceback
