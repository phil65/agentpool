"""Download file tool implementation."""

from dataclasses import dataclass
from pathlib import Path
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import anyio

from agentpool.agents.context import AgentContext  # noqa: TC001
from agentpool.log import get_logger
from agentpool.tools.base import Tool


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from exxec import ExecutionEnvironment
    from fsspec.asyn import AsyncFileSystem


logger = get_logger(__name__)


@dataclass
class DownloadFileTool(Tool[dict[str, Any]]):
    """Download files from URLs to the filesystem.

    A standalone tool for downloading files with:
    - HTTP/HTTPS URL downloads
    - Progress tracking
    - Configurable chunk size
    - Speed monitoring
    - Automatic directory creation

    Use create_download_file_tool() factory for convenient instantiation.
    """

    # Tool-specific configuration
    env: ExecutionEnvironment | None = None
    """Execution environment to use. Falls back to agent.env if not set."""

    fs: AsyncFileSystem | None = None
    """Filesystem to operate on (takes precedence over ``env``).

    Set for a custom filesystem: the tool then never touches the agent's
    environment (no host filesystem, cwd or subprocesses).
    """

    cwd: str | None = None
    """Working directory for resolving relative paths."""

    chunk_size: int = 8192
    """Size of chunks to download (bytes)."""

    timeout: float = 30.0
    """Request timeout in seconds."""

    def get_callable(self) -> Callable[..., Awaitable[dict[str, Any]]]:
        """Return the download_file method as the callable."""
        return self._download_file

    def _get_fs(self, ctx: AgentContext) -> AsyncFileSystem:
        """Get the filesystem: explicit fs, then env, then the agent's env."""
        if self.fs is not None:
            return self.fs
        return ctx.agent.env.get_fs() if self.env is None else self.env.get_fs()

    def _resolve_path(self, path: str, ctx: AgentContext) -> str:
        """Resolve a potentially relative path to an absolute path."""
        cwd: str | None = None
        if self.cwd:
            cwd = self.cwd
        elif self.env and self.env.cwd:
            cwd = self.env.cwd
        elif self.fs is None and ctx.agent.env and ctx.agent.env.cwd:
            cwd = ctx.agent.env.cwd

        if cwd and not (path.startswith("/") or (len(path) > 1 and path[1] == ":")):
            return str(Path(cwd) / path)
        return path

    async def _write(self, ctx: AgentContext, path: str, content: bytes) -> None:
        """Write bytes to a file."""
        await self._get_fs(ctx)._pipe_file(path, content)

    async def _download_file(
        self,
        ctx: AgentContext,
        url: str,
        target_dir: str = "downloads",
        chunk_size: int | None = None,
    ) -> dict[str, Any]:
        """Download a file from URL to the filesystem.

        Args:
            ctx: Agent context for event emission and filesystem access
            url: URL to download from
            target_dir: Directory to save the file (relative to cwd if set)
            chunk_size: Size of chunks to download (overrides default)

        Returns:
            Status information about the download
        """
        import httpx2

        effective_chunk_size = chunk_size or self.chunk_size
        start_time = time.time()

        # Resolve target directory
        target_dir = self._resolve_path(target_dir, ctx)

        msg = f"Downloading: {url}"
        await ctx.events.tool_call_start(title=msg, kind="read", locations=[url])

        # Extract filename from URL ("", "." and ".." are no file names)
        filename = Path(urlparse(url).path).name
        if filename in ("", ".", ".."):
            filename = "downloaded_file"
        full_path = f"{target_dir.rstrip('/')}/{filename}"

        try:
            fs = self._get_fs(ctx)
            # Ensure target directory exists
            await fs._makedirs(target_dir, exist_ok=True)

            async with (
                httpx2.AsyncClient() as client,  # verifies TLS certificates
                client.stream("GET", url, timeout=self.timeout) as response,
            ):
                response.raise_for_status()

                total = (
                    int(response.headers["Content-Length"])
                    if "Content-Length" in response.headers
                    else None
                )

                # Collect all data
                data = bytearray()
                async for chunk in response.aiter_bytes(effective_chunk_size):
                    data.extend(chunk)
                    size = len(data)

                    if total and (size % (effective_chunk_size * 100) == 0 or size == total):
                        progress = size / total * 100
                        elapsed = max(time.time() - start_time, 1e-6)
                        speed_mbps = (size / 1_048_576) / elapsed
                        progress_msg = f"\r{filename}: {progress:.1f}% ({speed_mbps:.1f} MB/s)"
                        await ctx.events.progress(progress, 100, progress_msg)
                        await anyio.sleep(0)

                # Write to filesystem
                await self._write(ctx, full_path, bytes(data))

            duration = time.time() - start_time
            size_mb = len(data) / 1_048_576

            await ctx.events.file_operation("read", path=full_path, success=True)

            return {
                "path": full_path,
                "filename": filename,
                "size_bytes": len(data),
                "size_mb": round(size_mb, 2),
                "duration_seconds": round(duration, 2),
                "speed_mbps": round(size_mb / duration, 2) if duration > 0 else 0,
            }

        except httpx2.ConnectError as e:
            error_msg = f"Connection error downloading {url}: {e}"
            await ctx.events.file_operation("read", path=url, success=False, error=error_msg)
            return {"error": error_msg}
        except httpx2.TimeoutException:
            error_msg = f"Timeout downloading {url}"
            await ctx.events.file_operation("read", path=url, success=False, error=error_msg)
            return {"error": error_msg}
        except httpx2.HTTPStatusError as e:
            error_msg = f"HTTP error {e.response.status_code} downloading {url}"
            await ctx.events.file_operation("read", path=url, success=False, error=error_msg)
            return {"error": error_msg}
        except Exception as e:  # noqa: BLE001
            error_msg = f"Error downloading {url}: {e!s}"
            await ctx.events.file_operation("read", path=url, success=False, error=error_msg)
            return {"error": error_msg}
