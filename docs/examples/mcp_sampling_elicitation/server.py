"""Compact FastMCP server demonstrating sampling and elicitation in one workflow.

Uses Multi Round-Trip Requests (MCP 2026-07-28): instead of calling back into the
client mid-call, the tool returns an `InputRequiredResult`. The client answers the
embedded requests and retries the call, and `request_state` tells the tool which
step it is in.
"""

from fastmcp import Context, FastMCP
from mcp.types import (
    CreateMessageRequest,
    CreateMessageRequestParams,
    CreateMessageResult,
    ElicitRequest,
    ElicitRequestFormParams,
    ElicitResult,
    InputRequiredResult,
    ModelHint,
    ModelPreferences,
    SamplingMessage,
    TextContent,
)


mcp = FastMCP("Code Fixer Server")

PREFS = ModelPreferences(hints=[ModelHint(name="gpt-5-nano")])


def _sample(key: str, prompt: str, system_prompt: str, state: str) -> InputRequiredResult:
    params = CreateMessageRequestParams(
        messages=[SamplingMessage(role="user", content=TextContent(type="text", text=prompt))],
        max_tokens=1000,
        system_prompt=system_prompt,
        model_preferences=PREFS,
    )
    return InputRequiredResult(
        input_requests={key: CreateMessageRequest(params=params)},
        request_state=state,
    )


def _ask_to_fix() -> InputRequiredResult:
    params = ElicitRequestFormParams(
        message="LLM found issues in your code. Should I fix them?",
        requested_schema={
            "type": "object",
            "properties": {"value": {"type": "boolean"}},
            "required": ["value"],
        },
    )
    return InputRequiredResult(
        input_requests={"confirm": ElicitRequest(params=params)},
        request_state="confirming",
    )


@mcp.tool
async def fix_code(ctx: Context, code: str) -> str | InputRequiredResult:
    """Analyze code, ask user whether to fix issues, then return improved code."""
    responses = ctx.input_responses or {}
    match ctx.request_state, responses:
        # Step 1: Use sampling to check if there are issues (yes/no)
        case None, _:
            question = (
                f"Does this code have any syntax errors, bugs, or style issues?\n\n{code}\n\n"
                "Respond with only 'yes' or 'no'."
            )
            system_prompt = "You are a code reviewer. Respond with only 'yes' or 'no'."
            return _sample("has_issues", question, system_prompt, state="reviewing")

        # Step 2: Use elicitation to ask user whether to fix (boolean)
        case "reviewing", {"has_issues": CreateMessageResult(content=TextContent(text=answer))}:
            if answer.strip().lower() != "yes":
                return f"Code looks good! No issues found.\n\nOriginal code:\n{code}"
            return _ask_to_fix()

        # Step 3: Use sampling to generate fixed code
        case "confirming", {"confirm": ElicitResult(action="accept", content={"value": True})}:
            fix_prompt = f"Fix all issues in this code:\n\n{code}"
            system_prompt = "You are a code fixer. Return only the corrected code."
            return _sample("fixed", fix_prompt, system_prompt, state="fixing")
        case "confirming", _:
            return f"No changes made.\n\nOriginal code:\n{code}"

        case "fixing", {"fixed": CreateMessageResult(content=TextContent(text=fixed_code))}:
            return f"Code fixed!\n\nOriginal:\n{code}\n\nFixed:\n{fixed_code}"

        case state, _:
            return f"Unexpected response in step {state!r}: {responses!r}"


if __name__ == "__main__":
    mcp.run()
