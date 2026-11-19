"""Tests for MIME type helpers."""

from __future__ import annotations

import pytest

from agentpool.mime_utils import guess_type, is_binary_mime


@pytest.mark.parametrize(
    "name", ["app.ts", "view.tsx", "lib.rs", "conf.yaml", "pyproject.toml", "run.sh"]
)
def test_source_files_are_not_binary(name: str) -> None:
    # The machine's mime.types maps e.g. .ts to video/mp2t; the fixed table must not.
    assert not is_binary_mime(guess_type(name))


@pytest.mark.parametrize("name", ["photo.png", "doc.pdf", "report.docx", "sheet.xlsx", "clip.mkv"])
def test_binary_files_are_binary(name: str) -> None:
    assert is_binary_mime(guess_type(name))


def test_guess_type_uses_additions() -> None:
    assert guess_type("notes.md") == "text/markdown"
    assert guess_type("config.yml") == "application/yaml"
    assert guess_type("no_extension") is None


def test_guesses_do_not_depend_on_system_mime_files() -> None:
    # /etc/mime.types maps these to text/vnd.trolltech.linguist or video/mp2t (.ts) and
    # application/rls-services+xml (.rs); the built-in table knows neither.
    assert guess_type("app.ts") is None
    assert guess_type("lib.rs") is None
