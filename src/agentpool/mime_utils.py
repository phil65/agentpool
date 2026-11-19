"""Centralized MIME type utilities for agentpool.

Guesses use a fixed table instead of the machine's ``mime.types`` files: those map
common source files to unrelated types (e.g. ``.ts`` to ``video/mp2t`` on many Linux
distributions, ``.rs`` to ``application/rls-services+xml``), which made results
differ between machines and sent such files down the binary path.
"""

from __future__ import annotations

import mimetypes


_mime_types = mimetypes.MimeTypes()  # Python's built-in table only, no system files
for _mime, _extension in (
    # Types Python's table lacks (some only before 3.14), as pydantic-ai adds them
    ("application/rtf", ".rtf"),
    ("application/vnd.oasis.opendocument.presentation", ".odp"),
    ("application/vnd.oasis.opendocument.spreadsheet", ".ods"),
    ("application/vnd.oasis.opendocument.text", ".odt"),
    ("application/vnd.openxmlformats-officedocument.presentationml.presentation", ".pptx"),
    ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx"),
    ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx"),
    ("text/markdown", ".md"),
    ("text/markdown", ".mdx"),
    ("text/x-asciidoc", ".asciidoc"),
    ("image/webp", ".webp"),
    ("video/3gpp", ".three_gp"),
    ("video/x-matroska", ".mkv"),
    ("video/x-ms-wmv", ".wmv"),
    ("video/x-flv", ".flv"),
    ("audio/aac", ".aac"),
    ("audio/aiff", ".aiff"),
    ("audio/flac", ".flac"),
    ("audio/ogg", ".oga"),
    ("audio/wav", ".wav"),
    ("application/yaml", ".yaml"),
    ("application/yaml", ".yml"),
    ("application/toml", ".toml"),
    ("application/xml", ".xml"),
):
    _mime_types.add_type(_mime, _extension)


# MIME type prefixes that are definitely binary (no need to probe content)
BINARY_MIME_PREFIXES = (
    "image/",
    "audio/",
    "video/",
    "application/octet-stream",
    "application/zip",
    "application/gzip",
    "application/x-tar",
    "application/pdf",
    "application/x-executable",
    "application/x-sharedlib",
    "application/vnd.openxmlformats-officedocument.",
    "application/vnd.oasis.opendocument.",
    "application/msword",
    "application/vnd.ms-",
)

# How many bytes to probe for binary detection
BINARY_PROBE_SIZE = 8192


def guess_type(path: str) -> str | None:
    """Guess the MIME type of a file based on its path/extension.

    Uses a fixed table (Python's built-in types plus common additions), so the result
    does not depend on the machine.

    Args:
        path: File path or URL to guess type for

    Returns:
        MIME type string or None if unknown
    """
    mime_type, _ = _mime_types.guess_type(path)
    return mime_type


def is_binary_mime(mime_type: str | None) -> bool:
    """Check if MIME type is known to be binary (skip content probing).

    Args:
        mime_type: MIME type string or None

    Returns:
        True if the MIME type is definitely binary
    """
    if mime_type is None:
        return False
    return any(mime_type.startswith(prefix) for prefix in BINARY_MIME_PREFIXES)


def is_binary_content(data: bytes) -> bool:
    """Detect binary content by probing for null bytes.

    Uses the same heuristic as git: if the first ~8KB contains a null byte,
    the content is considered binary.

    Args:
        data: Raw bytes to check

    Returns:
        True if content appears to be binary
    """
    probe = data[:BINARY_PROBE_SIZE]
    return b"\x00" in probe


def detect_image_media_type(data: bytes) -> str:
    """Detect image media type from magic bytes.

    Args:
        data: Raw image bytes (at least first 12 bytes needed)

    Returns:
        Media type string (defaults to "image/png" if unknown)
    """
    match data[:12]:
        case b if b[:3] == b"\xff\xd8\xff":
            return "image/jpeg"
        case b if b[:4] == b"\x89PNG":
            return "image/png"
        case b if b[:6] in (b"GIF87a", b"GIF89a"):
            return "image/gif"
        case b if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
            return "image/webp"
        case _:
            return "image/png"
