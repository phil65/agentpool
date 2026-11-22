"""Converters of the Claude Code agent: turn results and prompt content."""

from __future__ import annotations

from typing import Any

from clawd_code_sdk.models import UserTextPrompt, message_adapter
from pydantic_ai import BinaryContent
import pytest

from agentpool.agents.claude_code_agent.converters import (
    BashDisplay,
    TurnError,
    structured_results,
    to_bash_display,
    to_prompt_input,
    to_turn_error,
)


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


# Bash results as the CLI sends them (CLI 2.1.330): result text, structured tool_use_result
BASH_CASES = {
    "success": (
        "hello",
        {"stdout": "hello", "stderr": "", "interrupted": False, "isImage": False},
        False,
        BashDisplay(output="hello", exit_code=0),
    ),
    "failure": (
        "Exit code 3\nls: cannot access '/nope': No such file or directory",
        "Error: Exit code 3\nls: cannot access '/nope': No such file or directory",
        True,
        BashDisplay(output="ls: cannot access '/nope': No such file or directory", exit_code=3),
    ),
    "non-zero exit that is no error": (
        "(Bash completed with no output)",
        {
            "stdout": "",
            "stderr": "",
            "interrupted": False,
            "isImage": False,
            "returnCodeInterpretation": "No matches found",
        },
        False,
        BashDisplay(output="No matches found", exit_code=None),
    ),
    "large output": (
        "<persisted-output>\nOutput too large (2.6MB). Full output saved to: /p/x.txt\n...",
        {
            "stdout": "1\n2\n3",
            "stderr": "",
            "interrupted": False,
            "isImage": False,
            "persistedOutputPath": "/p/x",
            "persistedOutputSize": 2688895,
        },
        False,
        BashDisplay(
            output="1\n2\n3\n[Output truncated (2688895 bytes total): full output saved to /p/x]",
            exit_code=0,
        ),
    ),
    "background": (
        "Command running in background with ID: b1. Output is being written to: /t/b1",
        {"stdout": "", "stderr": "", "interrupted": False, "backgroundTaskId": "b1"},
        False,
        BashDisplay(
            output="Command running in background with ID: b1. Output is being written to: /t/b1",
            exit_code=None,
        ),
    ),
    "interrupted": (
        "partial\n[Request interrupted by user]",
        {"stdout": "partial", "stderr": "", "interrupted": True, "isImage": False},
        False,
        BashDisplay(
            output="partial\n[Command was aborted before completion]",
            exit_code=None,
            interrupted=True,
        ),
    ),
}


@pytest.mark.parametrize(
    ("text", "structured", "is_error", "expected"), BASH_CASES.values(), ids=list(BASH_CASES)
)
def test_bash_display(text: str, structured: Any, is_error: bool, expected: BashDisplay) -> None:
    assert to_bash_display(text, structured, is_error=is_error) == expected


@pytest.mark.parametrize(
    ("tool_use_result", "expected"),
    [
        (None, []),
        ({"stdout": "a"}, [{"stdout": "a"}]),  # one tool result: a single object
        ("Error: Exit code 1", ["Error: Exit code 1"]),
        ([{"stdout": "a"}, {"stdout": "b"}], [{"stdout": "a"}, {"stdout": "b"}]),
    ],
)
def test_structured_results(tool_use_result: Any, expected: list[Any]) -> None:
    assert structured_results(tool_use_result) == expected
