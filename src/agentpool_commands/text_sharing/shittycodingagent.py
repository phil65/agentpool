"""ShittyCodingAgent.ai text sharing provider.

Creates a GitHub Gist and returns a preview URL via shittycodingagent.ai.
"""

import os
from typing import TYPE_CHECKING, Any

from agentpool_commands.text_sharing.base import ShareResult, TextSharer


if TYPE_CHECKING:
    from agentpool_commands.text_sharing.base import Visibility


PREVIEW_BASE_URL = "https://shittycodingagent.ai/session"


class ShittyCodingAgentSharer(TextSharer):
    """Share text via GitHub Gists with shittycodingagent.ai preview URL.

    Creates a secret (unlisted) GitHub Gist and returns a URL in the format:
    https://shittycodingagent.ai/session?{gist_id}
    """

    def __init__(self, token: str | None = None) -> None:
        """Initialize the ShittyCodingAgent sharer.

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
        return "ShittyCodingAgent.ai"

    async def share(
        self,
        content: str,
        *,
        title: str | None = None,
        syntax: str | None = None,
        visibility: Visibility = "unlisted",
        expires_in: int | None = None,
    ) -> ShareResult:
        """Share content via GitHub Gist with shittycodingagent.ai preview.

        Args:
            content: The text content to share
            title: Filename for the gist (defaults to "session.html")
            syntax: File extension hint (e.g. "html")
            visibility: Always creates unlisted gist (visibility param ignored)
            expires_in: Ignored (gists don't expire)

        Returns:
            ShareResult with shittycodingagent.ai preview URL
        """
        import anyenv

        # Default to session.html for the typical use case
        filename = title or f"session.{syntax or 'html'}"
        # Always create as unlisted (secret) gist
        payload: dict[str, Any] = {
            "files": {filename: {"content": content}},
            "public": False,
        }
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        url = "https://api.github.com/gists"
        response: dict[str, Any] = await anyenv.post_json(url, payload, headers=headers)
        gist_id = response["id"]
        raw_url = response["files"][filename]["raw_url"]
        # Build the shittycodingagent.ai preview URL
        preview_url = f"{PREVIEW_BASE_URL}?{gist_id}"
        return ShareResult(url=preview_url, raw_url=raw_url, id=gist_id)


if __name__ == "__main__":
    import asyncio

    async def main() -> None:
        """Example usage of the ShittyCodingAgentSharer class."""
        sharer = ShittyCodingAgentSharer()
        result = await sharer.share(
            "<html><body><h1>Test Session</h1></body></html>",
            title="session.html",
        )
        print(f"Preview URL: {result.url}")
        print(f"Raw: {result.raw_url}")
        print(f"Gist ID: {result.id}")

    asyncio.run(main())
