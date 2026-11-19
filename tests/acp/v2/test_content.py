"""Tests for v2 wire rules and content blocks."""

from __future__ import annotations

from typing import Any

from pydantic import MISSING, BaseModel, TypeAdapter, ValidationError
import pytest

from acp.schema.v2.base import to_wire
from acp.schema.v2.content import (
    ContentBlock,
    ImageContent,
    ResourceLink,
    TextContent,
    TextResourceContents,
    UnknownContent,
)


BLOCKS: TypeAdapter[Any] = TypeAdapter(ContentBlock)


def test_missing_fields_are_left_out_and_none_is_sent_as_null() -> None:
    unset = ResourceLink(name="readme", uri="file:///README.md")
    cleared = ResourceLink(name="readme", uri="file:///README.md", title=None)

    assert to_wire(unset) == {"type": "resource_link", "name": "readme", "uri": "file:///README.md"}
    assert to_wire(cleared)["title"] is None


def test_parsing_distinguishes_missing_from_null() -> None:
    unset = ResourceLink.model_validate({"name": "n", "uri": "u"})
    cleared = ResourceLink.model_validate({"name": "n", "uri": "u", "title": None})

    assert unset.title is MISSING
    assert cleared.title is None


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"type": "text", "text": "hi"}, TextContent),
        ({"type": "image", "data": "aGk=", "mimeType": "image/png"}, ImageContent),
        ({"type": "resource_link", "name": "n", "uri": "u"}, ResourceLink),
    ],
)
def test_content_blocks_parse_by_type(payload: dict[str, Any], expected: type[BaseModel]) -> None:
    block = BLOCKS.validate_python(payload)

    assert isinstance(block, expected)
    assert to_wire(block) == payload


def test_embedded_text_resource() -> None:
    block = BLOCKS.validate_python({
        "type": "resource",
        "resource": {"uri": "file:///a.txt", "text": "content"},
    })

    assert isinstance(block.resource, TextResourceContents)


def test_unknown_content_block_round_trips() -> None:
    payload = {"type": "_x_widget", "widget": {"id": 7}}

    block = BLOCKS.validate_python(payload)

    assert isinstance(block, UnknownContent)
    assert block.type == "_x_widget"
    assert to_wire(block) == payload


def test_malformed_known_block_does_not_fall_back() -> None:
    with pytest.raises(ValidationError):
        BLOCKS.validate_python({"type": "image", "data": "aGk="})  # no mimeType
