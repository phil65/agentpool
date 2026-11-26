"""Tests for the multi-file repository map and its command line interface."""

import subprocess
import sys
from typing import TYPE_CHECKING

import pytest
from upathtools.filesystems import AsyncLocalFileSystem

from agentpool.repomap.__main__ import find_source_files, main, matches_any
from agentpool.repomap.core import RepoMap
from agentpool.repomap.tags import MAX_SIGNATURE_LENGTH, doc_summary, extract_file_tags


if TYPE_CHECKING:
    from pathlib import Path


BASE = '''\
"""Renderer base class: turns nodes into Markdown. More details follow here."""


class Renderer:
    """Renders nodes as Markdown.

    Rule lookup walks the node class hierarchy: a rule for `MkBlock` also renders
    `MkDetailsBlock`, unless a class in between renders itself (defines
    `to_markdown`).
    """

    def render(
        self,
        node: object,
        *,
        separator: str = "\\n\\n",  # the joiner
    ) -> str:
        """Render a node, e.g. a header. Handles children too."""
        return str(node)


def get_renderer(flavor: str) -> Renderer:
    return Renderer()
'''

USER = """\
from pkg.base import Renderer, get_renderer


def build(flavor: str) -> str:
    renderer = get_renderer(flavor)
    return renderer.render(flavor)
"""

RARE = """\
def rarely_used_helper(value: int) -> int:
    return value + 1
"""


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "base.py").write_text(BASE, encoding="utf-8")
    (pkg / "user.py").write_text(USER, encoding="utf-8")
    extra = pkg / "extra"
    extra.mkdir()
    for i in range(6):
        (extra / f"mod{i}.py").write_text(
            f"def helper_{i}(x):\n    return get_renderer(x)\n", encoding="utf-8"
        )
    (extra / "rare.py").write_text(RARE, encoding="utf-8")
    return tmp_path


async def _map(root: Path, max_tokens: int = 4096, **kwargs: set[str]) -> str:
    repo_map = RepoMap(AsyncLocalFileSystem(), root_path=str(root), max_tokens=max_tokens)
    files = [str(p) for p in sorted(root.rglob("*.py"))]
    return await repo_map.get_map(files, **kwargs) or ""


async def test_map_uses_paths_relative_to_root(repo: Path):
    content = await _map(repo)
    assert str(repo) not in content
    assert "pkg/base.py:" in content.splitlines()[0] or "pkg/base.py:" in content


async def test_multiline_signatures_are_shown_on_one_line(repo: Path):
    content = await _map(repo)
    assert '    def render(self, node: object, *, separator: str = "\\n\\n") -> str:' in content
    assert "def render(\n" not in content


async def test_docstrings_show_the_first_sentence_only(repo: Path):
    content = await _map(repo)
    assert "pkg/base.py:  # Renderer base class: turns nodes into Markdown." in content
    assert "  class Renderer:  # Renders nodes as Markdown." in content
    # No fragments of later docstring lines
    assert "unless a class" not in content
    assert "More details" not in content


async def test_omitted_files_are_summarized(repo: Path):
    content = await _map(repo, max_tokens=120)
    summary = content[content.index("Not shown") :]
    assert "pkg/extra/: " in summary
    assert "files (" in summary
    shown_and_omitted = summary.split("(", 1)[1].split(" files", 1)[0]
    assert int(shown_and_omitted) > 0


async def test_focus_ranks_up_matching_definitions(repo: Path):
    unfocused = await _map(repo, max_tokens=60)
    focused = await _map(repo, max_tokens=60, boost_idents={"rarely_used"})
    assert "rarely_used_helper" not in unfocused
    assert "def rarely_used_helper(value: int) -> int:" in focused


def test_doc_summary():
    assert doc_summary("Do things, e.g. stuff. Then more.") == "Do things, e.g. stuff."
    assert doc_summary("Summary line\n  continued here.\n\nDetails.") == (
        "Summary line continued here."
    )
    assert doc_summary("") == ""
    assert doc_summary("word " * 60) == ""  # too long: left out, never cut


def test_long_signatures_drop_defaults_then_annotations():
    params = ", ".join(f"param_{i}: int = {{'key': {i}, 'other': [1, 2]}}" for i in range(5))
    tags = extract_file_tags(f"def f({params}) -> int:\n    return 1\n", "m.py").tags
    sig = next(t.signature for t in tags if t.name == "f")
    assert len(sig) <= MAX_SIGNATURE_LENGTH
    assert sig == (
        "def f(param_0: int = ..., param_1: int = ..., param_2: int = ..., "
        "param_3: int = ..., param_4: int = ...) -> int:"
    )
    many = ", ".join(f"parameter_number_{i}: dict[str, int] = 1" for i in range(8))
    tags = extract_file_tags(f"def g({many}) -> int:\n    return 1\n", "m.py").tags
    sig = next(t.signature for t in tags if t.name == "g")
    assert sig.startswith("def g(parameter_number_0=..., parameter_number_1=...")


def test_matches_any():
    assert matches_any("tests/test_a.py", ["tests"])
    assert matches_any("src/pkg/tests/test_a.py", ["tests"])
    assert matches_any("docs/conf.py", ["docs/**"])
    assert matches_any("src/a_pb2.py", ["*_pb2.py"])
    assert not matches_any("src/pkg/core.py", ["tests", "docs/**"])


def test_find_source_files_skips_venvs_and_caches(repo: Path):
    (repo / ".venv" / "lib").mkdir(parents=True)
    (repo / ".venv" / "lib" / "dep.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "node_modules").mkdir()
    (repo / "node_modules" / "dep.js").write_text("var x = 1;\n", encoding="utf-8")
    (repo / "notes.txt").write_text("no code\n", encoding="utf-8")
    files = find_source_files(repo, exclude=["extra"])
    assert files == ["pkg/__init__.py", "pkg/base.py", "pkg/user.py"]


def test_cli_prints_map(repo: Path, capsys: pytest.CaptureFixture[str]):
    assert main([str(repo), "--tokens", "2000", "--focus", "Renderer", "--stats"]) == 0
    out, err = capsys.readouterr()
    assert "pkg/base.py:" in out
    assert "files shown" in err


def test_cli_startup_does_not_import_the_framework():
    result = subprocess.run(
        [sys.executable, "-X", "importtime", "-m", "agentpool.repomap", "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "usage:" in result.stdout
    imported = {line.rsplit("|", 1)[-1].strip() for line in result.stderr.splitlines()}
    assert "agentpool.repomap" in imported
    assert "agentpool.agents" not in imported
    assert "pydantic_ai" not in imported


def test_rel_paths_use_forward_slashes_for_windows_paths():
    from agentpool.repomap.utils import get_rel_path

    assert get_rel_path(r"C:\repo\pkg\base.py", "C:\\repo") == "pkg/base.py"
    assert get_rel_path("/repo/pkg/base.py", "/repo") == "pkg/base.py"
    assert get_rel_path("/elsewhere/x.py", "/repo") == "/elsewhere/x.py"
