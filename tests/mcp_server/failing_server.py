"""FastMCP stdio server whose tool fails (tool errors must reach the model)."""

from fastmcp import FastMCP


mcp = FastMCP("Failing Server")


@mcp.tool
def read_thing(name: str) -> str:
    """Read a thing that doesn't exist."""
    msg = f"no such thing: {name}"
    raise ValueError(msg)


if __name__ == "__main__":
    mcp.run(show_banner=False)
