"""Read file tool."""

from typing import TYPE_CHECKING, Literal

from agentpool.tool_impls.read.tool import ReadTool
from agentpool_config.tools import ToolHints


if TYPE_CHECKING:
    from exxec import ExecutionEnvironment
    from fsspec.asyn import AsyncFileSystem

    from agentpool.prompts.conversion_manager import ConversionManager

__all__ = ["ReadTool", "create_read_tool"]

# Tool metadata defaults
NAME = "read"
DESCRIPTION = """Read the content of a text file, or use vision capabilities
to read images or documents.

Supports:
- Text files with optional line-based partial reads
- Binary files (images, PDFs, audio, video) returned as BinaryContent
- Automatic image resizing for better model compatibility
- Structure maps for large code files
- Multiple text encodings"""
CATEGORY: Literal["read"] = "read"
HINTS = ToolHints(read_only=True, idempotent=True)


def create_read_tool(
    *,
    env: ExecutionEnvironment | None = None,
    fs: AsyncFileSystem | None = None,
    converter: ConversionManager | None = None,
    cwd: str | None = None,
    max_file_size_kb: int = 64,
    max_image_size: int | None = 2000,
    max_image_bytes: int | None = None,
    large_file_tokens: int = 12_000,
    map_max_tokens: int = 2048,
    name: str = NAME,
    description: str = DESCRIPTION,
    requires_confirmation: bool = False,
) -> ReadTool:
    """Create a configured ReadTool instance.

    Args:
        env: Execution environment to use. Falls back to agent.env if not set.
        fs: Filesystem to operate on instead of the environment's (custom filesystems).
        converter: Optional converter for binary files. If set, converts supported
            file types to markdown instead of returning BinaryContent.
        cwd: Working directory for resolving relative paths.
        max_file_size_kb: Maximum file size in KB for read operations (default: 64KB).
        max_image_size: Max width/height for images in pixels. Larger images are
            auto-resized. Set to None to disable.
        max_image_bytes: Max file size for images in bytes. Images exceeding this
            are compressed. Default: None (uses 4.5MB).
        large_file_tokens: Token threshold for switching to structure map (default: 12000).
        map_max_tokens: Maximum tokens for structure map output (default: 2048).
        name: Tool name override.
        description: Tool description override.
        requires_confirmation: Whether tool execution needs confirmation.

    Returns:
        Configured ReadTool instance.

    Example:
        # Basic usage
        read = create_read_tool()

        # With custom limits
        read = create_read_tool(
            max_file_size_kb=128,
            max_image_size=1500,
        )

        # With specific environment and cwd
        read = create_read_tool(
            env=my_env,
            cwd="/workspace/project",
        )

        # With converter for automatic markdown conversion
        from agentpool.prompts.conversion_manager import ConversionManager
        read = create_read_tool(
            converter=ConversionManager(),
        )
    """
    return ReadTool(
        name=name,
        description=description,
        category=CATEGORY,
        hints=HINTS,
        env=env,
        fs=fs,
        converter=converter,
        cwd=cwd,
        max_file_size_kb=max_file_size_kb,
        max_image_size=max_image_size,
        max_image_bytes=max_image_bytes,
        large_file_tokens=large_file_tokens,
        map_max_tokens=map_max_tokens,
        requires_confirmation=requires_confirmation,
    )
