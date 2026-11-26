"""Text sharing services for sharing content via paste services."""

from typing import Any, Literal, assert_never, overload

from agentpool_commands.text_sharing.base import ShareResult, TextSharer, Visibility
from agentpool_commands.text_sharing.github_gist import GistSharer
from agentpool_commands.text_sharing.paste_rs import PasteRsSharer
from agentpool_commands.text_sharing.pastebin import PastebinSharer
from agentpool_commands.text_sharing.shittycodingagent import ShittyCodingAgentSharer


TextSharerStr = Literal["gist", "pastebin", "paste_rs", "shittycodingagent"]


@overload
def get_sharer(
    provider: Literal["gist"],
    *,
    token: str | None = None,
) -> GistSharer: ...


@overload
def get_sharer(
    provider: Literal["pastebin"],
    *,
    api_key: str | None = None,
) -> PastebinSharer: ...


@overload
def get_sharer(
    provider: Literal["paste_rs"],
) -> PasteRsSharer: ...


@overload
def get_sharer(
    provider: Literal["shittycodingagent"],
    *,
    token: str | None = None,
) -> ShittyCodingAgentSharer: ...


def get_sharer(provider: TextSharerStr, **kwargs: Any) -> TextSharer:
    """Get a text sharer based on provider name.

    Args:
        provider: The text sharing provider to use
        **kwargs: Keyword arguments to pass to the provider constructor

    Returns:
        An instance of the specified text sharer

    Example:
        ```python
        # GitHub Gist (reads GITHUB_TOKEN/GH_TOKEN from env)
        sharer = get_sharer("gist")

        # GitHub Gist with explicit token
        sharer = get_sharer("gist", token="ghp_...")

        # Pastebin (reads PASTEBIN_API_KEY from env)
        sharer = get_sharer("pastebin")

        # Pastebin with explicit key
        sharer = get_sharer("pastebin", api_key="...")

        # paste.rs (no auth needed)
        sharer = get_sharer("paste_rs")
        ```
    """
    match provider:
        case "gist":
            return GistSharer(**kwargs)
        case "pastebin":
            return PastebinSharer(**kwargs)
        case "paste_rs":
            return PasteRsSharer()
        case "shittycodingagent":
            return ShittyCodingAgentSharer(**kwargs)
        case _ as unreachable:
            assert_never(unreachable)


__all__ = [
    "GistSharer",
    "PasteRsSharer",
    "PastebinSharer",
    "ShareResult",
    "ShittyCodingAgentSharer",
    "TextSharer",
    "TextSharerStr",
    "Visibility",
    "get_sharer",
]
