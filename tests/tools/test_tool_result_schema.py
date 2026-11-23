"""ToolResult must stay resolvable at runtime for pydantic-ai's return schemas."""

from __future__ import annotations

import warnings

from pydantic_ai import Tool

from agentpool.tools.base import ToolResult


def _tool() -> ToolResult:
    return ToolResult(content="done")


def test_registering_a_tool_returning_tool_result_does_not_warn() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        tool = Tool(_tool)
    messages = [str(w.message) for w in caught if "return schema" in str(w.message)]
    assert messages == []
    assert set(tool.function_schema.return_schema["properties"]) == {
        "content",
        "structured_content",
    }
