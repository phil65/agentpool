from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

from agentpool.mime_utils import guess_type
from agentpool_config.converters import ConversionConfig


if TYPE_CHECKING:
    from docler.converters.base import DocumentConverter
    from upathtools import JoinablePathLike


class ConversionManager:
    """Manages document conversion using configured providers.

    In order to not make things super complex, all Converters will be implemented as sync.
    The manager will handle async I/O and thread pooling.
    """

    def __init__(self, config: ConversionConfig | list[DocumentConverter]) -> None:
        if isinstance(config, list):
            self.config = ConversionConfig()
            self._converters = config
        else:
            self.config = config
            self._converters = self._setup_converters()
        self._executor = ThreadPoolExecutor(max_workers=3)

    def __del__(self) -> None:
        self._executor.shutdown(wait=False)

    def supports_file(self, path: JoinablePathLike) -> bool:
        """Check if any converter supports the file."""
        mime_type = guess_type(str(path)) or "application/octet-stream"
        return any(c.supports_mime_type(mime_type) for c in self._converters)

    def supports_content(self, content: Any, mime_type: str | None = None) -> bool:
        """Check if any converter supports the file."""
        return any(c.supports_mime_type(content) for c in self._converters)

    def _setup_converters(self) -> list[DocumentConverter]:
        """Create converter instances from config."""
        return [i.get_provider() for i in self.config.providers or []]
        # Always add PlainConverter as fallback
        # if it gets configured by user, that one gets preference.

    async def convert_file(self, path: JoinablePathLike) -> str:
        """Convert file using first supporting converter.

        Raises:
            ValueError: No converter supports the file's type
        """
        loop = asyncio.get_running_loop()
        mime_type = guess_type(str(path)) or "text/plain"

        for converter in self._converters:
            # Run support check in thread pool
            supports = await loop.run_in_executor(
                self._executor, converter.supports_mime_type, mime_type
            )
            if not supports:
                continue
            content = await loop.run_in_executor(self._executor, converter.convert_file, path)
            return str(content)
        msg = f"No converter supports {mime_type!r} ({path})"
        raise ValueError(msg)

    async def convert_content(self, content: Any, mime_type: str | None = None) -> str:
        """Convert content using first supporting converter."""
        loop = asyncio.get_running_loop()

        for converter in self._converters:
            # Run support check in thread pool
            supports = await loop.run_in_executor(
                self._executor, converter.supports_mime_type, mime_type or "text/plain"
            )
            if not supports:
                continue

            doc = await converter.convert_content(content, mime_type or "text/plain")
            return doc.content

        return str(content)  # Fallback for unsupported content


if __name__ == "__main__":
    from docler_config.converter_configs import MarkItDownConfig

    from agentpool_config.converters import ConversionConfig

    config = ConversionConfig(providers=[MarkItDownConfig()])
    manager = ConversionManager(config)
