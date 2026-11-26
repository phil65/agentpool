"""paste.rs text sharing provider."""

from typing import TYPE_CHECKING

from agentpool_commands.text_sharing.base import ShareResult, TextSharer


if TYPE_CHECKING:
    from agentpool_commands.text_sharing.base import Visibility


class PasteRsSharer(TextSharer):
    """Share text via paste.rs."""

    @property
    def name(self) -> str:
        """Name of the sharing service."""
        return "paste.rs"

    async def share(
        self,
        content: str,
        *,
        title: str | None = None,
        syntax: str | None = None,
        visibility: Visibility = "unlisted",
        expires_in: int | None = None,
    ) -> ShareResult:
        """Share content on paste.rs.

        Args:
            content: The text content to share
            title: Ignored (paste.rs doesn't support titles)
            syntax: Used as file extension for syntax highlighting
            visibility: Ignored (all pastes are unlisted)
            expires_in: Ignored (paste.rs doesn't support expiration)
        """
        import anyenv

        headers = {"Content-Type": "text/plain; charset=utf-8"}
        data = content.encode()
        response = await anyenv.request("POST", "https://paste.rs/", data=data, headers=headers)
        url = response.text.strip()
        ext = syntax or "txt"
        return ShareResult(url=f"{url}.{ext}", raw_url=url)


if __name__ == "__main__":
    import asyncio

    async def main() -> None:
        sharer = PasteRsSharer()
        result = await sharer.share("# Test Paste\n\nHello from anyenv!", syntax="md")
        print(f"URL: {result.url}")
        print(f"Raw: {result.raw_url}")

    asyncio.run(main())
