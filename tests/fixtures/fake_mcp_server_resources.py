"""Fake MCP server with resources for L4 e2e testing of MCP resource integration.

This module is spawned as a subprocess by the ``subprocess_server_with_mcp_resources``
fixture (see ``tests/e2e/conftest.py``) to verify that ``GET /experimental/resource``
reports real MCP resources and that resource injection works end-to-end.

It uses ``fastmcp`` (the project's existing MCP SDK) to avoid hand-rolled
JSON-RPC. The server exposes:

- **Tool**: ``ping`` — trivial tool so the server isn't tool-less.
- **Text resource**: ``config://app/settings`` — returns a JSON string.
- **Blob resource**: ``image://logo`` — returns a 1x1 PNG as binary.
- **Resource template**: ``file:///{path}`` — template for file-like URIs.

The server reports its name as ``fake-resource-server`` and version as ``0.1.0``
via the MCP ``initialize`` handshake's ``serverInfo`` field.

Run directly:

    python tests/fixtures/fake_mcp_server_resources.py
"""

from __future__ import annotations

import base64
import os
import sys

from fastmcp import FastMCP


if len(sys.argv) >= 3 and sys.argv[1] == "--variant":
    _VARIANT = sys.argv[2]
    del sys.argv[1:3]
else:
    _VARIANT = os.environ.get("FAKE_RESOURCE_SERVER_VARIANT", "primary")
_TOOL_NAME = "ping" if _VARIANT == "primary" else f"ping_{_VARIANT}"
mcp = FastMCP(f"fake-resource-server-{_VARIANT}", version="0.1.0")


# ---------------------------------------------------------------------------
# Tool (kept minimal so the server isn't tool-less)
# ---------------------------------------------------------------------------


@mcp.tool(name=_TOOL_NAME)
def ping() -> str:
    """Return a deterministic pong string.

    Returns:
        ``"pong"``
    """
    return "pong"


# ---------------------------------------------------------------------------
# Text resource
# ---------------------------------------------------------------------------


@mcp.resource("config://app/settings")
def app_settings() -> str:
    """Return application settings as a JSON string.

    Returns:
        A JSON string with ``app_name`` and ``version`` keys.
    """
    return '{"app_name": "fake-resource-server", "version": "0.1.0"}'


# ---------------------------------------------------------------------------
# Blob resource (1x1 transparent PNG)
# ---------------------------------------------------------------------------

# Minimal 1x1 transparent PNG (67 bytes).
_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


@mcp.resource("image://logo")
def logo_image() -> bytes:
    """Return a 1x1 transparent PNG as raw bytes.

    Returns:
        PNG binary data (67 bytes).
    """
    return _PNG_1X1


# ---------------------------------------------------------------------------
# Resource template
# ---------------------------------------------------------------------------


@mcp.resource("file:///{path}")
def read_file(path: str) -> str:
    """Return deterministic file content for the given path.

    Args:
        path: The file path (URL-decoded).

    Returns:
        ``"Content of: {path}"``
    """
    return f"Content of: {path}"


if __name__ == "__main__":
    mcp.run()
