"""GitHub Gist text sharing provider."""

import os
from typing import TYPE_CHECKING, Any

from agentpool_commands.text_sharing.base import ShareResult, TextSharer


if TYPE_CHECKING:
    from agentpool_commands.text_sharing.base import Visibility


class GistSharer(TextSharer):
    """Share text via GitHub Gists."""

    def __init__(self, token: str | None = None) -> None:
        """Initialize the GitHub Gist sharer.

        Args:
            token: GitHub personal access token. If not provided,
                   reads from GITHUB_TOKEN environment variable.
        """
        self._token = token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if not self._token:
            msg = "GitHub token required. Set GITHUB_TOKEN/GH_TOKEN or pass token parameter."
            raise ValueError(msg)

    @property
    def name(self) -> str:
        """Name of the sharing service."""
        return "GitHub Gist"

    async def share(
        self,
        content: str,
        *,
        title: str | None = None,
        syntax: str | None = None,
        visibility: Visibility = "unlisted",
        expires_in: int | None = None,
    ) -> ShareResult:
        """Share content as a GitHub Gist.

        Args:
            content: The text content to share
            title: Filename for the gist (defaults to "shared.txt" or "shared.{syntax}")
            syntax: File extension hint (e.g. "py", "md")
            visibility: "public" or "unlisted" (private not supported)
            expires_in: Ignored (gists don't expire)
        """
        import anyenv

        filename = title or f"shared.{syntax or 'txt'}"
        public = visibility == "public"
        payload: dict[str, Any] = {"files": {filename: {"content": content}}, "public": public}
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        url = "https://api.github.com/gists"
        response: dict[str, Any] = await anyenv.post_json(url, payload, headers=headers)
        raw_url = response["files"][filename]["raw_url"]
        return ShareResult(url=response["html_url"], raw_url=raw_url, id=response["id"])


if __name__ == "__main__":
    import asyncio

    async def main() -> None:
        """Example usage of the GistSharer class."""
        sharer = GistSharer()
        result = await sharer.share("# Test Gist!", title="test.md", syntax="md")
        print(f"URL: {result.url}")
        print(f"Raw: {result.raw_url}")
        print(f"ID: {result.id}")

    asyncio.run(main())
