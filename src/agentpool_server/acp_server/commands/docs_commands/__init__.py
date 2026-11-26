"""Documentation commands package.

This package contains slash commands for documentation-related operations
in ACP sessions, including source code fetching, git operations, schema
generation, URL conversion, and repository fetching.
"""

from .fetch_repo import FetchRepoCommand
from .get_schema import GetSchemaCommand
from .get_source import GetSourceCommand
from .git_diff import GitDiffCommand
from .url_to_markdown import UrlToMarkdownCommand
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from slashed import SlashedCommand

__all__ = [
    "FetchRepoCommand",
    "GetSchemaCommand",
    "GetSourceCommand",
    "GitDiffCommand",
    "UrlToMarkdownCommand",
    "get_docs_commands",
]


def get_docs_commands() -> list[type[SlashedCommand]]:
    """Get all documentation-related slash commands."""
    return [
        GetSourceCommand,
        GetSchemaCommand,
        GitDiffCommand,
        FetchRepoCommand,
        UrlToMarkdownCommand,
    ]
