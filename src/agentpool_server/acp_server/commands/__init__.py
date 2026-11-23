"""Slash commands."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .debug_commands import get_debug_commands
from .docs_commands import get_docs_commands


if TYPE_CHECKING:
    from slashed import SlashedCommand


def get_commands(*, debug: bool = False) -> list[SlashedCommand]:
    """Get the ACP-specific commands (the debug ones only with ``debug``)."""
    cmds = [*(get_debug_commands() if debug else []), *get_docs_commands()]
    return [i() for i in cmds]


__all__ = ["get_debug_commands", "get_docs_commands"]
