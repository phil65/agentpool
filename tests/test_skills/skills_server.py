"""Stdio MCP server publishing the skills found under a directory (SEP-2640).

Usage: ``python skills_server.py <skills-root>``
"""

from pathlib import Path
import sys

from fastmcp import FastMCP
from fastmcp.skills.provider import SkillsDirectoryProvider


def main() -> None:
    server = FastMCP("skills-test-server")
    server.add_provider(SkillsDirectoryProvider(Path(sys.argv[1])))
    server.run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    main()
