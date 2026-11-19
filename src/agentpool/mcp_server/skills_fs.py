"""Read-only filesystem over the skills an MCP server publishes (SEP-2640).

Each skill entry from ``skills/list`` lists every file of the skill with its size
and SHA-256 digest. This filesystem answers listings from those entries alone and
reads a file only when asked, through ``resources/read``. It refuses files the
entry does not list, and verifies every read against the entry before returning
it, as the Skills extension requires of hosts.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any, Literal, Protocol, Required, overload
from urllib.parse import unquote, urlsplit

from upathtools.filesystems.base import BaseAsyncFileSystem, BaseUPath, FileInfo

from agentpool.log import get_logger
from agentpool.skills.skill import SkillVerificationError


if TYPE_CHECKING:
    from collections.abc import Sequence

    from fastmcp.skills import MCPSkill, SkillResource


logger = get_logger(__name__)

MAX_SKILL_FILES = 512
MAX_SKILL_BYTES = 16 * 1024 * 1024


class ResourceReader(Protocol):
    """Reads one MCP resource as bytes (implemented by ``MCPClient``)."""

    async def read_resource_bytes(self, uri: str) -> bytes:
        """Read the resource at ``uri``."""
        ...


class MCPSkillsInfo(FileInfo, total=False):
    """Info dict for paths of the MCP skills filesystem."""

    size: Required[int]
    uri: str
    digest: str


class MCPSkillsPath(BaseUPath[MCPSkillsInfo]):
    """UPath bound to an ``MCPSkillsFileSystem``."""

    __slots__ = ()


def _uri_to_path(uri: str) -> str:
    """Map a resource URI to a filesystem path: authority, then the path segments."""
    parsed = urlsplit(uri)
    segments = [unquote(s) for s in parsed.path.split("/") if s]
    return "/".join([unquote(parsed.netloc), *segments]).strip("/")


def skill_path_for(entry: MCPSkill) -> str:
    """The filesystem path of an entry's skill directory."""
    return _uri_to_path(entry.uri).removesuffix("/SKILL.md")


class MCPSkillsFileSystem(BaseAsyncFileSystem[MCPSkillsPath, MCPSkillsInfo]):
    """Read-only, verifying filesystem over one MCP connection's skill entries."""

    protocol = "mcpskills"
    upath_cls = MCPSkillsPath
    root_marker = ""
    cachable = False

    def __init__(self, client: ResourceReader, **kwargs: Any) -> None:
        """Initialize the filesystem.

        Args:
            client: Connected MCP client (or other reader) to read skill files with.
            kwargs: Additional fsspec options.
        """
        super().__init__(**kwargs)
        self.client = client
        self._files: dict[str, SkillResource] = {}
        self._dirs: set[str] = {""}
        self._verified: dict[str, bytes] = {}

    def set_entries(self, entries: Sequence[MCPSkill]) -> dict[str, MCPSkill]:
        """Replace the served skills with ``entries``.

        Entries without a static file list (``resources: "dynamic"``), entries over
        the extension's per-skill limits, and repeated entries are skipped with a
        log message. Nested skills are kept: the spec counts a nested skill's files
        as supporting files of the enclosing one, so they share entries.

        Args:
            entries: Skill entries from ``skills/list``.

        Returns:
            The accepted entries, keyed by their skill directory path.
        """
        files: dict[str, SkillResource] = {}
        accepted: dict[str, MCPSkill] = {}
        for entry in entries:
            root = skill_path_for(entry)
            if isinstance(entry.resources, str):
                logger.info("Skipping MCP skill without a static manifest", uri=entry.uri)
                continue
            total = sum(r.size for r in entry.resources)
            if len(entry.resources) > MAX_SKILL_FILES or total > MAX_SKILL_BYTES:
                logger.warning(
                    "Skipping MCP skill over the extension limits",
                    uri=entry.uri,
                    files=len(entry.resources),
                    bytes=total,
                )
                continue
            if root in accepted:
                logger.warning("Skipping MCP skill listed twice", uri=entry.uri)
                continue
            accepted[root] = entry
            files.update({_uri_to_path(r.uri): r for r in entry.resources})
        self._files = files
        self._dirs = {""}
        for path in files:
            parts = path.split("/")[:-1]
            self._dirs.update("/".join(parts[:i]) for i in range(1, len(parts) + 1))
        digests = {r.digest for r in files.values()}
        self._verified = {d: b for d, b in self._verified.items() if d in digests}
        return accepted

    @overload
    @classmethod
    def _strip_protocol(cls, path: str) -> str: ...

    @overload
    @classmethod
    def _strip_protocol(cls, path: list[str]) -> list[str]: ...

    @classmethod
    def _strip_protocol(cls, path: str | list[str]) -> str | list[str]:
        if isinstance(path, list):
            return [cls._strip_protocol(p) for p in path]
        return path.removeprefix(f"{cls.protocol}://").strip("/")

    def _file_info(self, path: str) -> MCPSkillsInfo:
        resource = self._files[path]
        return MCPSkillsInfo(
            name=path,
            type="file",
            size=resource.size,
            uri=resource.uri,
            digest=resource.digest,
        )

    async def _info(self, path: str, **kwargs: Any) -> MCPSkillsInfo:
        path = self._strip_protocol(path)
        if path in self._files:
            return self._file_info(path)
        if path in self._dirs:
            return MCPSkillsInfo(name=path, type="directory", size=0)
        raise FileNotFoundError(path)

    @overload
    async def _ls(
        self, path: str, detail: Literal[True] = ..., **kwargs: Any
    ) -> list[MCPSkillsInfo]: ...

    @overload
    async def _ls(self, path: str, detail: Literal[False], **kwargs: Any) -> list[str]: ...

    async def _ls(
        self, path: str, detail: bool = True, **kwargs: Any
    ) -> list[str] | list[MCPSkillsInfo]:
        path = self._strip_protocol(path)
        if path in self._files:
            infos = [self._file_info(path)]
        elif path in self._dirs:
            prefix = f"{path}/" if path else ""
            children = {
                p
                for p in (*self._files, *self._dirs)
                if p and p.startswith(prefix) and "/" not in p[len(prefix) :]
            }
            infos = [await self._info(child) for child in sorted(children)]
        else:
            raise FileNotFoundError(path)
        return infos if detail else [info["name"] for info in infos]

    @overload
    async def _find(
        self,
        path: str,
        maxdepth: int | None = ...,
        withdirs: bool = ...,
        *,
        detail: Literal[False] = ...,
        **kwargs: Any,
    ) -> list[str]: ...

    @overload
    async def _find(
        self,
        path: str,
        maxdepth: int | None = ...,
        withdirs: bool = ...,
        *,
        detail: Literal[True],
        **kwargs: Any,
    ) -> dict[str, MCPSkillsInfo]: ...

    async def _find(
        self,
        path: str,
        maxdepth: int | None = None,
        withdirs: bool = False,
        *,
        detail: bool = False,
        **kwargs: Any,
    ) -> list[str] | dict[str, MCPSkillsInfo]:
        path = self._strip_protocol(path)
        prefix = f"{path}/" if path else ""
        candidates = [*self._files, *(self._dirs if withdirs else ())]
        found = sorted(
            p
            for p in candidates
            if p
            and p.startswith(prefix)
            and (maxdepth is None or p[len(prefix) :].count("/") < maxdepth)
        )
        if path in self._files:
            found = [path]
        if detail:
            return {p: await self._info(p) for p in found}
        return found

    async def _exists(self, path: str, **kwargs: Any) -> bool:
        path = self._strip_protocol(path)
        return path in self._files or path in self._dirs

    async def _isfile(self, path: str, **kwargs: Any) -> bool:
        return self._strip_protocol(path) in self._files

    async def _isdir(self, path: str) -> bool:
        return self._strip_protocol(path) in self._dirs

    async def _cat_file(
        self, path: str, start: int | None = None, end: int | None = None, **kwargs: Any
    ) -> bytes:
        """Read and verify one skill file.

        Raises:
            FileNotFoundError: If no held entry lists the file.
            SkillVerificationError: If the bytes do not match the entry's size or digest.
        """
        path = self._strip_protocol(path)
        resource = self._files.get(path)
        if resource is None:
            raise FileNotFoundError(path)
        content = self._verified.get(resource.digest)
        if content is None:
            content = await self.client.read_resource_bytes(resource.uri)
            if len(content) != resource.size:
                msg = f"{resource.uri} is {len(content)} bytes; the entry promised {resource.size}"
                raise SkillVerificationError(msg)
            if f"sha256:{hashlib.sha256(content).hexdigest()}" != resource.digest:
                raise SkillVerificationError(f"{resource.uri} does not match its entry's digest")
            self._verified[resource.digest] = content
        return content[start:end]

    async def _pipe_file(self, path: str, value: bytes, **kwargs: Any) -> None:
        raise PermissionError("The MCP skills filesystem is read-only")

    async def _put_file(self, lpath: str, rpath: str, **kwargs: Any) -> None:
        raise PermissionError("The MCP skills filesystem is read-only")

    async def _mkdir(self, path: str, create_parents: bool = True, **kwargs: Any) -> None:
        raise PermissionError("The MCP skills filesystem is read-only")

    async def _makedirs(self, path: str, exist_ok: bool = False) -> None:
        raise PermissionError("The MCP skills filesystem is read-only")

    async def _rm_file(self, path: str, **kwargs: Any) -> None:
        raise PermissionError("The MCP skills filesystem is read-only")

    async def _rm(
        self,
        path: str | list[str],
        recursive: bool = False,
        batch_size: int | None = None,
        **kwargs: Any,
    ) -> None:
        raise PermissionError("The MCP skills filesystem is read-only")


MCPSkillsFileSystem.register_fs(clobber=True)
