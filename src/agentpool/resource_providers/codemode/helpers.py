"""Prepare LLM output to be in proper shape and executable."""

import ast
from typing import TYPE_CHECKING

from agentpool.log import get_logger


if TYPE_CHECKING:
    from collections.abc import Sequence

    from schemez import ToolsetCodeGenerator

    from agentpool.tools import Tool

logger = get_logger(__name__)


def validate_code(python_code: str) -> None:
    """Validate code structure and raise ModelRetry for fixable issues."""
    from pydantic_ai import ModelRetry

    code = python_code.strip()
    try:
        ast.parse(code)
    except SyntaxError as e:
        msg = f"Invalid code syntax: {e}"
        logger.info("Invalid code", code=code)
        raise ModelRetry(msg) from None
    else:
        if "async def main(" not in code:
            logger.info("Code not in async def main()", code=code)
            msg = (
                "Code must be wrapped in 'async def main():' function. "
                "Please rewrite your code like:\n"
                "async def main():\n"
                "    # your code here\n"
                "    return result"
            )
            raise ModelRetry(msg)

        # Check if code contains a return statement
        if "return " not in code:
            logger.info("Code does not contain a return statement", code=code)
            msg = "The main() function should return a value."
            raise ModelRetry(msg)


def tools_to_codegen(
    tools: Sequence[Tool],
    include_docstrings: bool = True,
) -> ToolsetCodeGenerator:
    """Create a ToolsetCodeGenerator from a sequence of Tools.

    Args:
        tools: Tools to generate code for
        include_docstrings: Include function docstrings in documentation

    Returns:
        ToolsetCodeGenerator instance
    """
    from pydantic_ai import RunContext
    from schemez import ToolCodeGenerator, ToolsetCodeGenerator, create_schema

    from agentpool.agents.context import AgentContext

    generators = [
        ToolCodeGenerator(
            schema=create_schema(
                t.get_callable(),
                name_override=t.name,
                description_override=t.description,
                mode="openai",
                exclude_types=[AgentContext, RunContext],
            ),
            callable=t.get_callable(),
            name_override=t.name,
        )
        for t in tools
    ]
    return ToolsetCodeGenerator(generators, include_docstrings)
