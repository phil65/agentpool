"""Compact FastMCP server demonstrating sampling and elicitation in one workflow."""

from fastmcp import Context, FastMCP
from mcp.types import ModelHint, ModelPreferences, SamplingMessage, TextContent
from fastmcp.server.elicitation import AcceptedElicitation


mcp = FastMCP("Code Fixer Server")


@mcp.tool
async def fix_code(ctx: Context, code: str) -> str:
    """Analyze code, ask user which issues to fix, then return improved code."""
    # Step 1: Use sampling to check if there are issues (yes/no)
    prefs = ModelPreferences(hints=[ModelHint(name="gpt-5-nano")])
    question = (
        f"Does this code have any syntax errors, bugs, or style issues?\n\n{code}\n\n"
        "Respond with only 'yes' or 'no'."
    )
    has_issues_response = await ctx.session.create_message(
        [SamplingMessage(role="user", content=TextContent(type="text", text=question))],
        max_tokens=500,
        system_prompt="You are a code reviewer. Respond with only 'yes' or 'no'.",
        model_preferences=prefs,
    )

    has_issues_result = has_issues_response.content
    assert isinstance(has_issues_result, TextContent)
    if has_issues_result.text.strip().lower() != "yes":
        return f"Code looks good! No issues found.\n\nOriginal code:\n{code}"

    # Step 2: Use elicitation to ask user whether to fix (boolean)
    prompt = "LLM found issues in your code. Should I fix them?"
    fix_request = await ctx.elicit(prompt, response_type=bool)  # type: ignore[arg-type]
    if not isinstance(fix_request, AcceptedElicitation) or not fix_request.data:
        return f"No changes made.\n\nOriginal code:\n{code}"

    # Step 3: Use sampling to generate fixed code
    fix_prompt = f"Fix all issues in this code:\n\n{code}"
    fix_response = await ctx.session.create_message(
        [SamplingMessage(role="user", content=TextContent(type="text", text=fix_prompt))],
        max_tokens=1000,
        system_prompt="You are a code fixer. Return only the corrected code.",
        model_preferences=prefs,
    )

    fix_result = fix_response.content
    assert isinstance(fix_result, TextContent)
    fixed_code = fix_result.text
    return f"Code fixed!\n\nOriginal:\n{code}\n\nFixed:\n{fixed_code}"


if __name__ == "__main__":
    mcp.run()
