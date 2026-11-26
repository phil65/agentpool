"""Locations (directories) and projects.

OpenCode scopes most requests to a *location* (a directory, passed as
``location[directory]`` query parameter or ``x-opencode-directory`` header) which belongs
to a *project*. As in OpenCode, the project ID of a git checkout is its root commit hash,
other directories belong to the ``global`` project.
"""

from dataclasses import dataclass, field
import os
from pathlib import Path
import time

import anyio

from agentpool.log import get_logger
from opencode_v2_sdk.models import (
    LocationPublicInfo,
    LocationPublicInfoProject,
    LocationPublicRef,
    Project,
    ProjectTime,
)


logger = get_logger(__name__)

GLOBAL_PROJECT_ID = "global"


@dataclass(frozen=True)
class ProjectIdentity:
    """Resolved project of a directory."""

    id: str
    canonical: str
    """Main directory of the project (git toplevel, or the directory itself)."""
    vcs: str | None


async def _git(directory: str, *args: str) -> str | None:
    try:
        result = await anyio.run_process(["git", *args], cwd=directory, check=False)
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.decode().strip() or None


async def resolve_project(directory: str) -> ProjectIdentity:
    """Resolve the project a directory belongs to."""
    toplevel = await _git(directory, "rev-parse", "--show-toplevel")
    if toplevel is None:
        return ProjectIdentity(id=GLOBAL_PROJECT_ID, canonical=directory, vcs=None)
    roots = await _git(toplevel, "rev-list", "--max-parents=0", "HEAD")
    root = roots.splitlines()[0] if roots else None
    return ProjectIdentity(id=root or GLOBAL_PROJECT_ID, canonical=toplevel, vcs="git")


def normalize_directory(directory: str) -> str:
    """Absolute, normalized form of a location directory."""
    return os.path.normpath(os.path.abspath(os.path.expanduser(directory)))  # noqa: PTH100, PTH111


@dataclass
class ProjectRegistry:
    """Caches project resolution and tracks known projects."""

    _by_directory: dict[str, ProjectIdentity] = field(default_factory=dict)
    _created: dict[str, int] = field(default_factory=dict)
    _updated: dict[str, int] = field(default_factory=dict)
    _names: dict[str, str] = field(default_factory=dict)

    async def resolve(self, directory: str) -> ProjectIdentity:
        """Project of a directory (cached)."""
        directory = normalize_directory(directory)
        if (cached := self._by_directory.get(directory)) is not None:
            return cached
        identity = await resolve_project(directory)
        self._by_directory[directory] = identity
        now = int(time.time() * 1000)
        self._created.setdefault(identity.id, now)
        self._updated[identity.id] = now
        return identity

    async def location_info(self, directory: str) -> LocationPublicInfo:
        """``Location.PublicInfo`` of a directory."""
        identity = await self.resolve(directory)
        return LocationPublicInfo(
            directory=normalize_directory(directory),
            project=LocationPublicInfoProject(
                id=identity.id, directory=identity.canonical, canonical=identity.canonical
            ),
        )

    def rename(self, project_id: str, name: str | None) -> None:
        """Set (or clear) a project's display name."""
        if name is None:
            self._names.pop(project_id, None)
        else:
            self._names[project_id] = name
        self._updated[project_id] = int(time.time() * 1000)

    def projects(self) -> list[Project]:
        """All projects resolved so far."""
        seen: dict[str, ProjectIdentity] = {}
        for identity in self._by_directory.values():
            seen.setdefault(identity.id, identity)
        result = []
        for identity in seen.values():
            created = self._created.get(identity.id, 0)
            updated = self._updated.get(identity.id, created)
            result.append(
                Project(
                    id=identity.id,
                    canonical=identity.canonical,
                    vcs=identity.vcs,
                    name=self._names.get(identity.id) or Path(identity.canonical).name,
                    time=ProjectTime(created=created, updated=updated, active=updated),
                    sandboxes=[],
                )
            )
        return result


def public_ref(directory: str) -> LocationPublicRef:
    """``Location.PublicRef`` of a directory."""
    return LocationPublicRef(directory=normalize_directory(directory))
