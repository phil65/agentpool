"""Converters of the Claude Code agent: turn results and prompt content."""

from __future__ import annotations

from typing import Any

from clawd_code_sdk.models import UserTextPrompt, message_adapter
from pydantic_ai import BinaryContent
import pytest

from agentpool.agents.claude_code_agent.converters import TurnError, to_prompt_input, to_turn_error


USAGE = {"input_tokens": 1, "output_tokens": 1}


def _result(subtype: str, *, is_error: bool, **fields: Any) -> Any:
    return message_adapter.validate_python({
        "type": "result",
        "subtype": subtype,
        "session_id": "s",
        "uuid": "u",
        "duration_ms": 1,
        "duration_api_ms": 1,
        "is_error": is_error,
        "num_turns": 1,
        "total_cost_usd": 0,
        "usage": USAGE,
        "stop_reason": "end_turn",
        **fields,
    })


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (_result("success", is_error=False, result="done"), None),
        (
            _result("success", is_error=True, result="Not logged in · Please run /login"),
            TurnError(message="Not logged in · Please run /login", code="login_required"),
        ),
        (
            _result("success", is_error=True, result="API Error: 500"),
            TurnError(message="API Error: 500", code="error"),
        ),
        (
            _result("error_max_turns", is_error=True, errors=[]),
            TurnError(message="error_max_turns", code="error_max_turns"),
        ),
        (
            _result("error_during_execution", is_error=True, errors=["boom", "bang"]),
            TurnError(message="boom, bang", code="error_during_execution"),
        ),
        # A stop reason or an interrupt explains the end: not a failure
        (_result("success", is_error=True, result="x", stop_reason="refusal"), None),
        (_result("error_during_execution", is_error=True, stop_reason="max_tokens"), None),
        (
            _result("error_during_execution", is_error=True, terminal_reason="aborted_streaming"),
            None,
        ),
    ],
)
def test_turn_error(result: Any, expected: TurnError | None) -> None:
    assert to_turn_error(result) == expected


def test_text_attachment_becomes_context() -> None:
    content = BinaryContent(data=b"hello", media_type="text/plain", identifier="notes.txt")
    assert list(to_prompt_input([content])) == [
        UserTextPrompt(text='<context ref="notes.txt">\nhello\n</context>')
    ]
