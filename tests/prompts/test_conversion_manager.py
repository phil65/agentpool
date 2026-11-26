"""``ConversionManager.convert_file`` uses the first converter that supports the file."""

from typing import Any

import pytest

from agentpool.prompts.conversion_manager import ConversionManager


class _Converter:
    def __init__(self, name: str, mime: str) -> None:
        self.name, self.mime, self.calls = name, mime, 0

    def supports_mime_type(self, mime_type: str) -> bool:
        return mime_type == self.mime

    def convert_file(self, path: Any) -> str:
        self.calls += 1
        return f"by {self.name}"


async def test_first_supporting_converter_wins() -> None:
    first, second = _Converter("first", "application/pdf"), _Converter("second", "application/pdf")
    other = _Converter("other", "text/html")
    manager = ConversionManager([other, first, second])  # type: ignore[list-item]
    assert await manager.convert_file("doc.pdf") == "by first"
    assert (other.calls, first.calls, second.calls) == (0, 1, 0)


async def test_no_supporting_converter_is_a_clear_error() -> None:
    manager = ConversionManager([_Converter("html", "text/html")])  # type: ignore[list-item]
    with pytest.raises(ValueError, match="No converter"):
        await manager.convert_file("doc.pdf")
