"""Listings made for the agent leave out hidden and gitignored files.

Filesystems' ``_find`` / ``_glob`` include them (like fsspec); agentpool filters itself.
The tests use fsspec's local filesystem behind ``AsyncFileSystemWrapper``, which lists
everything, like upathtools' ``AsyncLocalFileSystem`` now does.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from fsspec.implementations.asyn_wrapper import AsyncFileSystemWrapper
from fsspec.implementations.dirfs import DirFileSystem  # type: ignore[import-untyped]
from fsspec.implementations.local import LocalFileSystem
from fsspec.implementations.memory import MemoryFileSystem
import pytest
from upath import UPath

from agentpool import Agent, AgentContext
from agentpool.skills.skill import Skill
from agentpool.utils.listing_filter import filter_listing
from agentpool_toolsets.builtin.code import CodeTools
from agentpool_toolsets.fsspec_toolset import FSSpecTools
from agentpool_toolsets.fsspec_toolset.grep import grep_with_fsspec


if TYPE_CHECKING:
    from pathlib import Path


VISIBLE = {"src/a.py", "src/sub/b.py", "README.md"}
HIDDEN_OR_IGNORED = {
    ".git/config",
    ".venv/lib/site.py",
    "node_modules/pkg/index.py",
    "build/out.py",
    "debug.log",
    ".env",
    "src/.cache/c.py",
}


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    for rel in VISIBLE | HIDDEN_OR_IGNORED:
        file = root / rel
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("needle = 1\n")
    (root / ".gitignore").write_text("node_modules/\nbuild/\n*.log\n")
    return root


def _fs() -> AsyncFileSystemWrapper:
    return AsyncFileSystemWrapper(LocalFileSystem())


def _rel(root: Path, paths: Any) -> set[str]:
    return {os.path.relpath(str(p), root) for p in paths}


async def test_filter_listing_drops_hidden_and_ignored(project: Path):
    fs = _fs()
    found = await fs._find(str(project))
    assert _rel(project, found) >= HIDDEN_OR_IGNORED  # the filesystem lists them
    kept = await filter_listing(fs, str(project), found)
    assert _rel(project, kept) == VISIBLE


async def test_filter_listing_dirfs_view(project: Path):
    fs = DirFileSystem(str(project), _fs(), asynchronous=True)
    found = await fs._find("")
    kept = await filter_listing(fs, "", found)
    assert set(kept) == VISIBLE


async def test_filter_listing_inside_hidden_root(project: Path):
    fs = _fs()
    root = str(project / ".venv")
    kept = await filter_listing(fs, root, await fs._find(root))
    assert _rel(project / ".venv", kept) == {"lib/site.py"}


async def test_filter_listing_non_local_drops_only_hidden():
    fs = MemoryFileSystem(skip_instance_cache=True)
    fs.store = {}
    fs.pseudo_dirs = [""]
    for rel in ["a.py", ".hidden/b.py", "node_modules/c.py"]:
        fs.pipe(f"/mem/{rel}", b"x")
    paths = ["/mem/a.py", "/mem/.hidden/b.py", "/mem/node_modules/c.py"]
    assert await filter_listing(fs, "/mem", paths) == ["/mem/a.py", "/mem/node_modules/c.py"]


@pytest.fixture
def agent_ctx(project: Path) -> AgentContext:
    agent = Agent[None](name="fs_agent", model="test", env=str(project))
    return AgentContext(node=agent, tool_call_id="call_1", tool_name="t", tool_input={})


async def test_list_directory_skips_hidden_and_ignored(project: Path, agent_ctx: AgentContext):
    tools = {t.name: t for t in await FSSpecTools(_fs()).get_tools()}
    listing = str(
        await tools["list_directory"].execute_and_unwrap(
            ctx=agent_ctx, path=str(project), pattern="**/*", max_depth=5
        )
    )
    assert "a.py" in listing
    assert "b.py" in listing
    for name in ["site.py", "index.py", "out.py", "debug.log", ".env", ".git", "c.py"]:
        assert name not in listing


async def test_grep_fsspec_skips_hidden_and_ignored(project: Path):
    result = await grep_with_fsspec(_fs(), "needle", str(project))
    matches = result["matches"]
    assert "a.py" in matches
    for name in ["site.py", "index.py", "out.py", "debug.log", "config", "c.py"]:
        assert name not in matches


async def test_skill_files_skip_hidden_and_ignored(tmp_path: Path):
    root = tmp_path / "my-skill"
    (root / "references").mkdir(parents=True)
    (root / "SKILL.md").write_text("---\nname: my-skill\ndescription: d\n---\nbody\n")
    (root / "references" / "guide.md").write_text("guide")
    (root / ".DS_Store").write_text("x")
    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "x.pyc").write_text("x")
    (root / ".gitignore").write_text("__pycache__/\n")
    (root / ".git").mkdir()
    skill = Skill(name="my-skill", description="d", skill_path=UPath(root))
    assert await skill.list_files() == ["SKILL.md", "references/guide.md"]


async def test_diagnostics_skip_hidden_and_ignored(
    project: Path, agent_ctx: AgentContext, monkeypatch: pytest.MonkeyPatch
):
    from agentpool_toolsets.fsspec_toolset.diagnostics import DiagnosticsManager

    seen: list[str] = []

    async def fake_run_for_files(self: Any, paths: list[str], **kwargs: Any) -> Any:
        seen.extend(paths)
        raise RuntimeError("stop")

    monkeypatch.setattr(DiagnosticsManager, "run_for_files", fake_run_for_files)
    tools = CodeTools()
    monkeypatch.setattr(tools, "_get_fs", lambda ctx: _fs())
    with pytest.raises(RuntimeError, match="stop"):
        await tools.run_diagnostics(agent_ctx, str(project))
    assert _rel(project, seen) == VISIBLE
