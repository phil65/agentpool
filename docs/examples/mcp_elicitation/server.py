"""FastMCP server that asks the user for input mid-workflow via elicitation.

Uses Multi Round-Trip Requests (MCP 2026-07-28): instead of calling back into the
client mid-call, the tool returns an `InputRequiredResult`. The client answers the
embedded requests and retries the call, and `request_state` tells the tool which
step it is in.
"""

from fastmcp import Context, FastMCP
from mcp.types import ElicitRequest, ElicitRequestFormParams, ElicitResult, InputRequiredResult


mcp = FastMCP("Release Server")

BUMPS = ["patch", "minor", "major"]


def _bump(version: str, part: str) -> str:
    major, minor, patch = (int(i) for i in version.split("."))
    match part:
        case "major":
            return f"{major + 1}.0.0"
        case "minor":
            return f"{major}.{minor + 1}.0"
        case _:
            return f"{major}.{minor}.{patch + 1}"


def _elicit(key: str, message: str, schema: dict[str, object], state: str) -> InputRequiredResult:
    params = ElicitRequestFormParams(message=message, requested_schema=schema)
    return InputRequiredResult(
        input_requests={key: ElicitRequest(params=params)}, request_state=state
    )


@mcp.tool
async def release(ctx: Context, package: str, version: str) -> str | InputRequiredResult:
    """Prepare a release: ask the user for the version bump, then for confirmation."""
    responses = ctx.input_responses or {}
    match ctx.request_state, responses:
        # Step 1: ask which part of the version to bump (enum choice)
        case None, _:
            schema = {
                "type": "object",
                "properties": {"bump": {"type": "string", "enum": BUMPS}},
                "required": ["bump"],
            }
            msg = f"{package} is at {version}. Which part should be bumped?"
            return _elicit("bump", msg, schema, state="choosing")

        # Step 2: ask for confirmation (boolean), carrying the choice in request_state
        case "choosing", {"bump": ElicitResult(action="accept", content={"bump": str(bump)})}:
            schema = {
                "type": "object",
                "properties": {"value": {"type": "boolean"}},
                "required": ["value"],
            }
            msg = f"Release {package} {_bump(version, bump)}?"
            return _elicit("confirm", msg, schema, state=f"confirming:{bump}")
        case "choosing", _:
            return "Release cancelled."

        # Step 3: done
        case str(state), {"confirm": ElicitResult(action="accept", content={"value": True})}:
            bump = state.removeprefix("confirming:")
            return f"Released {package} {_bump(version, bump)}."
        case str(), _:
            return "Release cancelled."

        case state, _:
            return f"Unexpected response in step {state!r}: {responses!r}"


if __name__ == "__main__":
    mcp.run()
