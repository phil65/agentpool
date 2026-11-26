"""Tests for repomap structure outlines."""

from typing import TYPE_CHECKING

from agentpool.repomap import languages
from agentpool.repomap.languages import filename_to_grammar, get_supported_languages
from agentpool.repomap.outline import get_file_map_from_content
from agentpool.repomap.tags import get_tags_from_content


if TYPE_CHECKING:
    from pathlib import Path

    import pytest


TS_SOURCE = """\
export interface Shape {
  area(): number;
}

export class Circle implements Shape {
  constructor(private r: number) {}
  area(): number {
    return Math.PI * this.r ** 2;
  }
}

export function total(shapes: Shape[]): number {
  return shapes.reduce((s, x) => s + x.area(), 0);
}
"""

TSX_SOURCE = """\
interface Props {
  label: string;
}

export function Button({ label }: Props) {
  return <button>{label}</button>;
}
"""

PY_SOURCE = """\
class A:
    def __init__(self) -> None:
        self.x = 1


class B:
    def __init__(self) -> None:
        self.y = 2
"""


def _defs(content: str, filename: str) -> set[str]:
    return {t.name for t in get_tags_from_content(content, filename) if t.kind == "def"}


def test_typescript_definitions():
    assert {"Shape", "Circle", "area", "total"} <= _defs(TS_SOURCE, "shapes.ts")
    outline = get_file_map_from_content(TS_SOURCE, "shapes.ts")
    assert outline is not None
    assert "export class Circle implements Shape {  # [5-10]" in outline
    assert "export function total(shapes: Shape[]): number {  # [12-14]" in outline


def test_tsx_uses_the_tsx_grammar():
    assert filename_to_grammar("App.tsx") == "tsx"
    assert filename_to_grammar("app.ts") == "typescript"
    assert {"Props", "Button"} <= _defs(TSX_SOURCE, "App.tsx")


def test_identical_signatures_get_their_own_line_ranges():
    outline = get_file_map_from_content(PY_SOURCE, "mod.py")
    assert outline is not None
    init_lines = [line for line in outline.splitlines() if "def __init__" in line]
    assert init_lines == [
        "│    def __init__(self) -> None:  # [2-3]",
        "│    def __init__(self) -> None:  # [7-8]",
    ]


def test_languages_from_the_fallback_query_folder_are_supported():
    supported = get_supported_languages()
    assert {"typescript", "tsx", "kotlin", "php", "scala", "python"} <= supported


def test_query_not_matching_the_grammar_yields_no_tags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    broken = tmp_path / "python-tags.scm"
    broken.write_text("(no_such_node) @name.definition.class", encoding="utf-8")
    monkeypatch.setattr(languages, "get_scm_fname", lambda lang: broken)
    assert get_tags_from_content(PY_SOURCE, "mod.py") == []
