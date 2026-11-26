from typing import TYPE_CHECKING, Literal, assert_never, overload

from upathtools import AsyncUPath, list_files, read_folder, read_path

from agentpool.log import get_logger
from agentpool.utils.baseregistry import AgentPoolError


if TYPE_CHECKING:
    from collections.abc import Iterator

    from fsspec import AbstractFileSystem
    from upathtools import AsyncUPath, UPath
    from upathtools.filesystems import UnionFileSystem

    from agentpool.models.manifest import ResourceConfig


logger = get_logger(__name__)


class VFSRegistry:
    """Registry for virtual filesystems built on UnionFileSystem."""

    def __init__(self) -> None:
        """Initialize empty VFS registry."""
        from upathtools.filesystems import UnionFileSystem

        self._union_fs = UnionFileSystem({})
        logger.debug("Initialized VFS registry")

    def __contains__(self, name: str) -> bool:
        """Check if a resource is registered."""
        return name in self._union_fs.filesystems

    def __len__(self) -> int:
        """Get number of registered resources."""
        return len(self._union_fs.filesystems)

    def __iter__(self) -> Iterator[str]:
        """Iterate over registered resource names."""
        return iter(self._union_fs.filesystems)

    @property
    def is_empty(self) -> bool:
        """Check if registry has any resources."""
        return len(self._union_fs.filesystems) == 0

    def register(self, name: str, fs: AbstractFileSystem | str, *, replace: bool = False) -> None:
        """Register a filesystem resource.

        Args:
            name: Resource name to use as mount point
            fs: Filesystem instance or URI string
            replace: Whether to replace existing resource

        Raises:
            ValueError: If resource exists and replace=False
        """
        logger.debug("Registering resource", name=name, type=type(fs).__name__)
        try:
            self._union_fs.register(name, fs, replace=replace)
        except ValueError as e:
            raise AgentPoolError(str(e)) from e

    def unregister(self, name: str) -> None:
        """Unregister a filesystem resource.

        Args:
            name: Resource name to remove

        Raises:
            AgentPoolError: If resource doesn't exist
        """
        logger.debug("Unregistering resource", name=name)
        try:
            self._union_fs.unregister(name)
        except ValueError as e:
            raise AgentPoolError(str(e)) from e

    def register_from_config(self, name: str, config: ResourceConfig) -> AbstractFileSystem:
        """Register a resource from configuration.

        Args:
            name: Resource name
            config: Resource configuration

        Returns:
            The registered filesystem instance
        """
        from upathtools_config.base import FileSystemConfig, URIFileSystemConfig

        match config:
            case str() as uri:
                fs_config = URIFileSystemConfig(uri=uri)
                fs = fs_config.create_fs()
            case FileSystemConfig():
                fs = config.create_fs()
            case _ as unreachable:
                assert_never(unreachable)

        self.register(name, fs)
        return fs

    def list_resources(self) -> list[str]:
        """List all registered resource names."""
        return self._union_fs.list_mount_points()

    def get_fs(self) -> UnionFileSystem:
        """Get unified filesystem view of all resources."""
        return self._union_fs

    @overload
    def get_upath(
        self, resource_name: str | None = None, *, as_async: Literal[True]
    ) -> AsyncUPath: ...

    @overload
    def get_upath(
        self, resource_name: str | None = None, *, as_async: Literal[False] = False
    ) -> UPath: ...

    @overload
    def get_upath(
        self, resource_name: str | None = None, *, as_async: bool = False
    ) -> UPath | AsyncUPath: ...

    def get_upath(
        self, resource_name: str | None = None, *, as_async: bool = False
    ) -> UPath | AsyncUPath:
        """Get a UPath object for accessing resources.

        Args:
            resource_name: Specific resource or None for unified view
            as_async: Whether to return AsyncUPath

        Returns:
            UPath or AsyncUPath instance
        """
        if resource_name is not None and resource_name not in self._union_fs.filesystems:
            raise AgentPoolError(f"Resource not found: {resource_name}")

        return self._union_fs.get_upath(resource_name, as_async=as_async)

    async def get_content(
        self,
        path: str,
        encoding: str = "utf-8",
        recursive: bool = True,
        exclude: list[str] | None = None,
        max_depth: int | None = None,
    ) -> str:
        """Get content from a resource as text.

        Args:
            path: Path to read, either:
                - resource (whole resource)
                - resource/file.txt (single file)
                - resource/folder (directory)
            encoding: Text encoding for binary content
            recursive: For directories, whether to read recursively
            exclude: For directories, patterns to exclude
            max_depth: For directories, maximum depth to read

        Returns:
            For files: file content
            For directories: concatenated content of all files
        """
        # Normalize path - ensure it has resource prefix
        if "/" not in path:
            path = f"{path}/"

        if await self._union_fs._isdir(path):
            content_dict = await read_folder(
                self._union_fs.get_upath(path),
                encoding=encoding,
                recursive=recursive,
                exclude=exclude,
                max_depth=max_depth,
            )
            # Combine all files with headers
            sections = []
            for rel_path, content in sorted(content_dict.items()):
                sections.extend([f"--- {rel_path} ---", content, ""])
            return "\n".join(sections)

        return await read_path(self._union_fs.get_upath(path), encoding=encoding)

    async def query(
        self,
        path: str,
        pattern: str = "**/*",
        *,
        recursive: bool = True,
        include_dirs: bool = False,
        exclude: list[str] | None = None,
        max_depth: int | None = None,
    ) -> list[str]:
        """Query contents of a resource or subfolder.

        Args:
            path: Path to query, either:
                - resource (queries whole resource)
                - resource/subfolder (queries specific folder)
            pattern: Glob pattern to match files against
            recursive: Whether to search subdirectories
            include_dirs: Whether to include directories in results
            exclude: List of patterns to exclude
            max_depth: Maximum directory depth for recursive search

        Example:
            # Query whole resource
            files = await registry.query("docs")

            # Query specific subfolder
            files = await registry.query("docs/guides", pattern="*.md")
        """
        # Normalize path - ensure it has resource prefix
        if "/" not in path:
            path = f"{path}/"

        resource = path.split("/")[0]
        if resource not in self:
            raise AgentPoolError(f"Resource not found: {resource}")

        files = await list_files(
            self._union_fs.get_upath(path),
            pattern=pattern,
            recursive=recursive,
            include_dirs=include_dirs,
            exclude=exclude,
            max_depth=max_depth,
        )
        return [str(p) for p in files]

    def reset(self) -> None:
        """Reset registry to empty state."""
        from upathtools.filesystems import UnionFileSystem

        logger.debug("Resetting VFS registry")
        self._union_fs = UnionFileSystem({})


if __name__ == "__main__":
    from upathtools.filesystems.isolated_memory_fs import IsolatedMemoryFileSystem

    registry = VFSRegistry()
    fs1 = IsolatedMemoryFileSystem()
    registry.register("test", fs1)
    union = registry.get_fs()
    print(union.ls(""))
