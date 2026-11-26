"""Zed tool results: the content Zed stored, in its old and current forms."""

import base64

from pydantic_ai import BinaryContent
import pytest

from agentpool_storage.zed_provider.helpers import parse_tool_results
from agentpool_storage.zed_provider.models import ZedToolResult


# 1x1 PNG
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


def _result(content: object, output: object = None, name: str = "read_file") -> ZedToolResult:
    return ZedToolResult.model_validate({
        "tool_use_id": "t1",
        "tool_name": name,
        "is_error": False,
        "content": content,
        "output": output,
    })


@pytest.mark.parametrize(
    "content",
    [
        [{"Text": "hello"}],  # Since September 2026: a list
        {"Text": "hello"},  # Before: one item
        "hello",  # An item as plain string
        [{"Text": "hel"}, {"Text": "lo"}],
    ],
)
def test_tool_result_text_in_every_form(content: object) -> None:
    [part] = parse_tool_results({"t1": _result(content)})
    assert part.content == "hello"


def test_edit_file_result_keeps_the_text_the_model_saw() -> None:
    """Its raw output is a structured diff; the text comes from the content."""
    output = {"input_path": "a.py", "old_text": "x", "new_text": "y", "diff": "-x\n+y"}
    result = _result({"Text": "Edited a.py successfully"}, output, name="edit_file")
    [part] = parse_tool_results({"t1": result})
    assert part.content == "Edited a.py successfully"


def test_raw_output_when_the_content_has_no_text() -> None:
    [part] = parse_tool_results({"t1": _result(None, "raw text")})
    assert part.content == "raw text"


def test_tool_result_images_are_kept() -> None:
    image = {"Image": {"source": base64.b64encode(PNG).decode(), "size": None}}
    [part] = parse_tool_results({"t1": _result([{"Text": "screenshot"}, image])})
    assert isinstance(part.content, list)
    text, binary = part.content
    assert text == "screenshot"
    assert isinstance(binary, BinaryContent)
    assert binary.data == PNG
    assert binary.media_type == "image/png"
