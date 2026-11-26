"""VFS registry filesystem toolset implementation."""

from fnmatch import fnmatch

from agentpool.agents.context import AgentContext  # noqa: TC001
from agentpool.resource_providers import StaticResourceProvider


async def vfs_list(  # noqa: D417
    ctx: AgentContext,
    path: str = "/",
    *,
    pattern: str = "**/*",
    recursive: bool = True,
    include_dirs: bool = False,
    exclude: list[str] | None = None,
    max_depth: int | None = None,
) -> str:
    """List contents of a filesystem path.

    Lists files from the agent's unified filesystem, which includes:
    - Agent's internal storage (execute_code/, tasks/, etc.)
    - Configured VFS resources

    Args:
        path: Path to query (e.g., "/", "/docs", "/tasks")
        pattern: Glob pattern to match files against
        recursive: Whether to search subdirectories
        include_dirs: Whether to include directories in results
        exclude: List of patterns to exclude
        max_depth: Maximum directory depth for recursive search

    Returns:
        Formatted list of matching files and directories
    """
    fs = ctx.overlay_fs
    norm_path = path.strip("/") if path not in ("/", "") else fs.root_marker
    try:
        # List using filesystem directly (not via UPath helpers that lose fs context)
        if norm_path == fs.root_marker:
            items = await fs._ls(fs.root_marker, detail=True)
        else:
            # Use glob for pattern matching
            if recursive and pattern != "*":
                glob_pattern = f"{norm_path}/{pattern}"
            else:
                glob_pattern = f"{norm_path}/{pattern}" if pattern != "**/*" else f"{norm_path}/*"
            glob_result = await fs._glob(glob_pattern, maxdepth=max_depth, detail=True)
            items = list(glob_result.values())

        # Filter results
        results: list[str] = []
        for item in items:
            name = item.get("name", "").strip("/")
            item_type = item.get("type", "file")

            # Apply exclude patterns
            if exclude and any(fnmatch(name, pat) for pat in exclude):
                continue

            # Filter directories unless include_dirs
            if item_type == "directory" and not include_dirs:
                continue

            icon = "📁" if item_type == "directory" else "📄"
            results.append(f"  {icon} /{name}")

        if not results:
            return f"No files found in {path}"

        lines = ["Available paths:"]
        lines.extend(sorted(results))
        return "\n".join(lines)
    except FileNotFoundError:
        return f"Path not found: {path}"
    except (OSError, ValueError) as e:
        return f"Error listing {path}: {e}"


async def vfs_read(  # noqa: D417
    ctx: AgentContext,
    path: str,
    *,
    encoding: str = "utf-8",
    recursive: bool = True,
    exclude: list[str] | None = None,
    max_depth: int | None = None,
) -> str:
    """Read content from a filesystem path.

    Reads from the agent's unified filesystem, which includes:
    - Agent's internal storage (execute_code/, tasks/, etc.)
    - Configured VFS resources

    Args:
        path: Path to read (e.g., "/docs/readme.md", "/tasks/task_123/output.md")
        encoding: Text encoding for binary content
        recursive: For directories, whether to read recursively
        exclude: For directories, patterns to exclude
        max_depth: For directories, maximum depth to read

    Returns:
        File content or concatenated directory contents
    """
    fs = ctx.overlay_fs
    try:
        # Normalize path for filesystem operations
        norm_path = path.strip("/") if path not in ("/", "") else fs.root_marker
        if await fs._isdir(norm_path):
            # Read directory contents
            sections: list[str] = []

            # Get files via glob
            glob_pattern = "**/*" if recursive else "*"
            glob_result = await fs._glob(
                f"{norm_path}/{glob_pattern}", maxdepth=max_depth, detail=True
            )

            if isinstance(glob_result, dict):
                files = list(glob_result.items())
            else:
                files = [(str(f), {}) for f in glob_result]

            for file_path, info in sorted(files):
                # Skip directories
                if info.get("type") == "directory":
                    continue

                # Apply exclude patterns
                rel_path = file_path.removeprefix(norm_path).strip("/")
                if exclude and any(fnmatch(rel_path, pat) for pat in exclude):
                    continue

                try:
                    content_bytes = await fs._cat_file(file_path)
                    content = content_bytes.decode(encoding)
                    sections.extend([f"--- {rel_path} ---", content, ""])
                except UnicodeDecodeError, OSError:
                    sections.extend([f"--- {rel_path} ---", "[Binary or unreadable file]", ""])

            return "\n".join(sections) if sections else f"No readable files in {path}"

        # Single file read
        content_bytes = await fs._cat_file(norm_path)
        return content_bytes.decode(encoding)
    except FileNotFoundError:
        return f"Path not found: {path}"
    except (OSError, ValueError) as e:
        return f"Error reading {path}: {e}"


async def vfs_info(ctx: AgentContext) -> str:
    """Get information about the agent's unified filesystem.

    Returns:
        Formatted information about available filesystem layers
    """
    fs = ctx.overlay_fs
    sections = ["## Agent Filesystem\n"]
    sections.append("Unified view combining agent storage and configured resources.\n")
    # List layers
    sections.append("### Layers")
    for i, layer in enumerate(fs.layers):
        layer_type = "writable" if i == 0 else "read-only"
        sections.append(f"- Layer {i} ({layer_type}): {type(layer).__name__}")

    # List top-level contents
    sections.append("\n### Contents")
    try:
        for item in await fs._ls(fs.root_marker, detail=True):
            item_type = "dir" if item.get("type") == "directory" else "file"
            name = item.get("name", "").strip("/")
            layer = item.get("layer", "?")
            sections.append(f"- /{name} ({item_type}, layer {layer})")
    except (OSError, FileNotFoundError) as e:
        sections.append(f"Error listing contents: {e}")

    return "\n".join(sections)


class VFSTools(StaticResourceProvider):
    """Provider for unified filesystem tools.

    Provides tools for listing and reading from the agent's overlay filesystem,
    which combines the agent's internal storage with configured VFS resources.
    """

    def __init__(self, name: str = "vfs") -> None:
        super().__init__(name=name, tools=[])
        for tool in [
            self.create_tool(vfs_list, category="search", read_only=True, idempotent=True),
            self.create_tool(vfs_read, category="read", read_only=True, idempotent=True),
            self.create_tool(vfs_info, category="read", read_only=True, idempotent=True),
        ]:
            self.add_tool(tool)
