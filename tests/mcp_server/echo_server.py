"""Tiny FastMCP stdio server: one tool, one prompt (lifecycle and session tests)."""

from fastmcp import FastMCP


mcp = FastMCP("Echo Server")


@mcp.tool
def echo(text: str) -> str:
    """Return the text."""
    return text


@mcp.prompt
def greet() -> str:
    """Say hello."""
    return "Hello!"


if __name__ == "__main__":
    mcp.run(show_banner=False)
