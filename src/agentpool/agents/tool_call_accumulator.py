"""Tool call accumulator for streaming tool arguments.

This module provides utilities for accumulating streamed tool call arguments
from LLM APIs that stream JSON arguments incrementally (like Anthropic's
input_json_delta or OpenAI's function call streaming).
"""

from dataclasses import dataclass
from typing import Any

from pydantic_core import from_json


@dataclass(slots=True)
class ToolCall:
    """Tool call in construction."""

    name: str
    args_buffer: str = ""


class ToolCallAccumulator:
    """Accumulates streamed tool call arguments.

    LLM APIs stream tool call arguments as deltas. This class accumulates them
    and provides the complete arguments when the tool call ends, as well as
    best-effort partial argument parsing during streaming.

    Example:
        ```python
        accumulator = ToolCallAccumulator()

        # On content_block_start with tool_use
        accumulator.start("toolu_123", "write_file")

        # On input_json_delta events
        accumulator.add_args("toolu_123", '{"path": "/tmp/')
        accumulator.add_args("toolu_123", 'test.txt", "content"')
        accumulator.add_args("toolu_123", ': "hello"}')

        # Get partial args for UI preview (repairs incomplete JSON)
        partial = accumulator.get_partial_args("toolu_123")

        # On content_block_stop, get final parsed args
        name, args = accumulator.complete("toolu_123")
        ```
    """

    def __init__(self) -> None:
        self._calls: dict[str, ToolCall] = {}

    def start(self, tool_call_id: str, tool_name: str) -> None:
        """Start tracking a new tool call.

        Args:
            tool_call_id: Unique identifier for the tool call
            tool_name: Name of the tool being called
        """
        self._calls[tool_call_id] = ToolCall(name=tool_name)

    def add_args(self, tool_call_id: str, delta: str) -> None:
        """Add argument delta to a tool call.

        Args:
            tool_call_id: Tool call identifier
            delta: JSON string fragment to append
        """
        if tool_call_id in self._calls:
            self._calls[tool_call_id].args_buffer += delta

    def complete(self, tool_call_id: str) -> tuple[str, dict[str, Any]] | None:
        """Complete a tool call and return (tool_name, parsed_args).

        Removes the tool call from tracking and returns the final parsed arguments.

        Args:
            tool_call_id: Tool call identifier

        Returns:
            Tuple of (tool_name, args_dict) or None if call not found
        """
        if tool_call_id not in self._calls:
            return None

        call_data = self._calls.pop(tool_call_id)
        try:
            args = from_json(call_data.args_buffer) if call_data.args_buffer else {}
        except ValueError:
            args = {"_raw": call_data.args_buffer}
        return call_data.name, args

    def get_pending(self, tool_call_id: str) -> tuple[str, str] | None:
        """Get pending call data (tool_name, args_buffer) without completing.

        Args:
            tool_call_id: Tool call identifier

        Returns:
            Tuple of (tool_name, args_buffer) or None if not found
        """
        if tool_call_id not in self._calls:
            return None
        data = self._calls[tool_call_id]
        return data.name, data.args_buffer

    def get_partial_args(self, tool_call_id: str) -> dict[str, Any]:
        """Get best-effort parsed args from incomplete JSON stream.

        Uses heuristics to complete truncated JSON for preview purposes.
        Handles unclosed strings, missing braces/brackets, and trailing commas.

        Args:
            tool_call_id: Tool call ID

        Returns:
            Partially parsed arguments or empty dict
        """
        if tool_call_id not in self._calls:
            return {}
        buffer = self._calls[tool_call_id].args_buffer
        if not buffer:
            return {}
        return json_from_string(buffer)

    def is_pending(self, tool_call_id: str) -> bool:
        """Check if a tool call is being tracked."""
        return tool_call_id in self._calls

    def get_tool_name(self, tool_call_id: str) -> str | None:
        """Get the tool name for a pending call."""
        if tool_call_id not in self._calls:
            return None
        return self._calls[tool_call_id].name

    def clear(self) -> None:
        """Clear all pending tool calls."""
        self._calls.clear()


def json_from_string(buffer: str) -> dict[str, Any]:
    try:
        result = from_json(buffer, allow_partial="trailing-strings")
    except ValueError:
        return {}
    return result if isinstance(result, dict) else {}
