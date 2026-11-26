"""Pastebin.com text sharing provider."""

import os
from typing import TYPE_CHECKING, Any

from agentpool_commands.text_sharing.base import ShareResult, TextSharer


if TYPE_CHECKING:
    from agentpool_commands.text_sharing.base import Visibility


# Pastebin expiry values
EXPIRY_MAP = {
    None: "N",  # Never
    600: "10M",  # 10 Minutes
    3600: "1H",  # 1 Hour
    86400: "1D",  # 1 Day
    604800: "1W",  # 1 Week
    1209600: "2W",  # 2 Weeks
    2592000: "1M",  # 1 Month
    15552000: "6M",  # 6 Months
    31536000: "1Y",  # 1 Year
}

# Map visibility to Pastebin's api_paste_private values
VISIBILITY_MAP = {"public": "0", "unlisted": "1", "private": "2"}


def _get_expiry_code(seconds: int | None) -> str:
    """Convert seconds to Pastebin expiry code, rounding to nearest valid value."""
    if seconds is None:
        return "N"

    valid_seconds = sorted(k for k in EXPIRY_MAP if k is not None)
    closest = min(valid_seconds, key=lambda x: abs(x - seconds))
    return EXPIRY_MAP[closest]


class PastebinSharer(TextSharer):
    """Share text via Pastebin.com."""

    def __init__(self, api_key: str | None = None) -> None:
        """Initialize the Pastebin sharer.

        Args:
            api_key: Pastebin API developer key. If not provided,
                     reads from PASTEBIN_API_KEY environment variable.
        """
        self._api_key = api_key or os.environ.get("PASTEBIN_API_KEY")
        if not self._api_key:
            msg = "Pastebin API key required. Set PASTEBIN_API_KEY or pass api_key parameter."
            raise ValueError(msg)

    @property
    def name(self) -> str:
        """Name of the sharing service."""
        return "Pastebin"

    async def share(
        self,
        content: str,
        *,
        title: str | None = None,
        syntax: str | None = None,
        visibility: Visibility = "unlisted",
        expires_in: int | None = None,
    ) -> ShareResult:
        """Share content on Pastebin.com.

        Args:
            content: The text content to share
            title: Title/name of the paste
            syntax: Syntax highlighting (e.g. "python", "markdown", "javascript")
            visibility: "public", "unlisted", or "private"
            expires_in: Expiration time in seconds (rounded to nearest valid value)
        """
        import anyenv

        data: dict[str, Any] = {
            "api_dev_key": self._api_key,
            "api_option": "paste",
            "api_paste_code": content,
            "api_paste_private": VISIBILITY_MAP.get(visibility, "1"),
            "api_paste_expire_date": _get_expiry_code(expires_in),
        }

        if title:
            data["api_paste_name"] = title

        if syntax:
            data["api_paste_format"] = syntax

        response = await anyenv.post("https://pastebin.com/api/api_post.php", data=data)
        url = response.text.strip()
        if url.startswith("Bad API request"):
            raise RuntimeError(f"Pastebin API error: {url}")
        paste_key = url.split("/")[-1]
        raw_url = f"https://pastebin.com/raw/{paste_key}"
        return ShareResult(url=url, raw_url=raw_url, id=paste_key)


if __name__ == "__main__":
    import asyncio

    async def main() -> None:
        sharer = PastebinSharer()
        result = await sharer.share("# Test Paste\n\nHello from anyenv!", title="test.md")
        print(f"URL: {result.url}")
        print(f"Raw: {result.raw_url}")
        print(f"ID: {result.id}")

    asyncio.run(main())
