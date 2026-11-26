"""Commands that call web APIs through anyenv (3.x response API), with faked responses."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import anyenv
import httpx2


if TYPE_CHECKING:
    import pytest


def _response(text: str, headers: dict[str, str] | None = None) -> anyenv.HttpResponse:
    request = httpx2.Request("POST", "https://example.com/")
    return anyenv.HttpResponse(
        httpx2.Response(200, text=text, headers=headers or {}, request=request)
    )


async def test_paste_rs_returns_url(monkeypatch: pytest.MonkeyPatch):
    from agentpool_commands.text_sharing.paste_rs import PasteRsSharer

    async def fake_request(method: str, url: str, **kwargs: Any) -> anyenv.HttpResponse:
        assert kwargs["data"] == b"hello"
        return _response("https://paste.rs/abc\n")

    monkeypatch.setattr(anyenv, "request", fake_request)
    result = await PasteRsSharer().share("hello", syntax="py")
    assert result.url == "https://paste.rs/abc.py"
    assert result.raw_url == "https://paste.rs/abc"


async def test_pastebin_returns_url(monkeypatch: pytest.MonkeyPatch):
    from agentpool_commands.text_sharing.pastebin import PastebinSharer

    async def fake_post(url: str, **kwargs: Any) -> anyenv.HttpResponse:
        assert kwargs["data"]["api_paste_code"] == "hello"
        return _response("https://pastebin.com/XyZ\n")

    monkeypatch.setattr(anyenv, "post", fake_post)
    result = await PastebinSharer(api_key="k").share("hello")
    assert result.url == "https://pastebin.com/XyZ"
    assert result.raw_url == "https://pastebin.com/raw/XyZ"
    assert result.id == "XyZ"


async def test_url_to_markdown_stages_page_with_title(monkeypatch: pytest.MonkeyPatch):
    from agentpool_server.acp_server.commands.docs_commands.url_to_markdown import (
        UrlToMarkdownCommand,
    )

    async def fake_get(url: str, **kwargs: Any) -> anyenv.HttpResponse:
        assert kwargs["params"]["url"] == "https://example.com"
        return _response("# Example", {"X-Title": "Example%20Page"})

    monkeypatch.setattr(anyenv, "get", fake_get)
    staged: list[Any] = []
    output = SimpleNamespace(command_started=AsyncMock(), command_updated=AsyncMock())
    ctx = SimpleNamespace(
        context=SimpleNamespace(
            data=SimpleNamespace(output=output),
            agent=SimpleNamespace(staged_content=_Staged(staged)),
        )
    )
    await UrlToMarkdownCommand().execute_command(ctx, "https://example.com")  # type: ignore[arg-type]
    [part] = staged
    assert part.content == "Webpage content from https://example.com - Example Page:\n\n# Example"
    assert output.command_updated.await_args.kwargs["status"] == "completed"


class _Staged:
    def __init__(self, items: list[Any]) -> None:
        self.items = items

    def add(self, parts: list[Any]) -> None:
        self.items.extend(parts)

    def __len__(self) -> int:
        return len(self.items)
