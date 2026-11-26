"""Parsers for extracting sync metadata from different file types."""

from .python_parser import PythonSyncParser
from .markdown_parser import MarkdownSyncParser
from .protocol import MetadataParser


# Registry of built-in parsers
BUILTIN_PARSERS: list[MetadataParser] = [PythonSyncParser(), MarkdownSyncParser()]


def get_parser_for_file(path: str) -> MetadataParser | None:
    """Get appropriate parser for a file path."""
    for parser in BUILTIN_PARSERS:
        if path.endswith(parser.extensions):
            return parser
    return None


__all__ = ["MarkdownSyncParser", "MetadataParser", "PythonSyncParser"]
