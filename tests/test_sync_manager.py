"""Tests for SyncManager file scanning and GitHub release lookup (issue #57.6b/c)."""

from typing import TYPE_CHECKING, Any

import anyenv

from agentpool_sync.manager import SyncManager
from agentpool_sync.models import SyncMetadata
from agentpool_sync.packages import _fetch_github_release


if TYPE_CHECKING:
    from pathlib import Path

    import pytest


class _UrlsOnlyParser:
    extensions = (".x",)

    def parse(self, content: str) -> SyncMetadata | None:
        return SyncMetadata(urls=["https://example.com/spec"])

    def update(self, content: str, metadata: SyncMetadata) -> str:
        return content


def test_scan_file_keeps_files_with_only_urls(tmp_path: Path):
    path = tmp_path / "doc.x"
    path.write_text("content")
    manager = SyncManager(tmp_path)
    manager.register_parser(_UrlsOnlyParser())  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]
    result = manager.scan_file(path)
    assert result is not None
    assert result[1].urls == ["https://example.com/spec"]


async def test_github_release_url_keeps_trailing_letters(monkeypatch: pytest.MonkeyPatch):
    urls: list[str] = []

    async def get_json(url: str, **_kwargs: Any) -> dict[str, Any]:
        urls.append(url)
        return {"body": "notes"}

    monkeypatch.setattr(anyenv, "get_json", get_json)
    notes = await _fetch_github_release("https://github.com/pydantic/pydantic-ai.git", "1.0")
    assert notes == "notes"
    assert urls == ["https://api.github.com/repos/pydantic/pydantic-ai/releases/tags/v1.0"]
