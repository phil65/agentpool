"""Checks that keep the documentation in step with the code.

- YAML examples in the docs made of manifest sections (agents, teams, storage, ...) must be
  valid manifests.
- Every CLI command has a page in docs/cli/.
- Every model of a discriminated union in the manifest is rendered on some docs page
  (via the `union_to_markdown` / `schema_to_markdown` filters).
"""

from __future__ import annotations

import importlib
from pathlib import Path
import re
from typing import Annotated, Any, get_args, get_origin

import pydantic
import pytest
import yaml

from agentpool import AgentsManifest
from agentpool.models.manifest import EXTENSION_KEY_PREFIX


DOCS = Path(__file__).parent.parent / "docs"
SKIPPED_DIRS = {"proposals", "superpowers"}
"""Design documents describe planned config, not the current one."""

MANIFEST_KEYS = set(AgentsManifest.model_fields) - {"config_file_path"}
FENCE = re.compile(r"^([ \t]*)```(yaml|yml)[^\n]*\n(.*?)^\1```[ \t]*$", re.DOTALL | re.MULTILINE)
FILTER_REF = re.compile(r'\{\{\s*"([\w.:]+)"\s*\|\s*(union_to_markdown|schema_to_markdown)')


def _manifest_keys(data: dict[Any, Any]) -> set[Any]:
    """Top-level keys of a YAML example, without extension keys (``x-...``)."""
    return {key for key in data if not str(key).startswith(EXTENSION_KEY_PREFIX)}


def _doc_pages() -> list[Path]:
    return sorted(
        p for p in DOCS.rglob("*.md") if not SKIPPED_DIRS & set(p.relative_to(DOCS).parts)
    )


def _manifest_examples() -> list[Any]:
    params = []
    for page in _doc_pages():
        text = page.read_text(encoding="utf-8")
        for match in FENCE.finditer(text):
            indent, _, body = match.groups()
            body = "\n".join(line.removeprefix(indent) for line in body.splitlines())
            try:
                data = yaml.safe_load(body)
            except yaml.YAMLError:
                continue
            # Snippets made of manifest sections only (agents, teams, jobs, storage, ...)
            if not isinstance(data, dict) or not data or not _manifest_keys(data) <= MANIFEST_KEYS:
                continue
            if "INHERIT" in data:  # refers to other files
                continue
            line = text[: match.start()].count("\n") + 1
            params.append(pytest.param(data, id=f"{page.relative_to(DOCS)}:{line}"))
    # YAML files (included into pages, or example configs)
    for path in sorted([*DOCS.rglob("*.yml"), *DOCS.rglob("*.yaml")]):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data and _manifest_keys(data) <= MANIFEST_KEYS:
            params.append(pytest.param(data, id=str(path.relative_to(DOCS))))
    return params


@pytest.mark.parametrize("data", _manifest_examples())
def test_yaml_example_is_valid_manifest(data: dict[str, Any]):
    """YAML examples made of manifest sections validate against AgentsManifest.

    Import paths of illustrative user code (`myapp.tools.x`) cannot be imported here,
    so import errors are ignored.
    """
    errors: list[Any] = []
    try:
        AgentsManifest.model_validate(data)
    except pydantic.ValidationError as e:
        errors = _without_import_errors(e.errors())
    assert errors == []


def _without_import_errors(errors: list[Any]) -> list[Any]:
    """Drop import errors and the errors of the other union members of the same value."""
    import_errors = [err["loc"] for err in errors if err["type"] == "import_error"]
    failed = [_value_loc(loc) for loc in import_errors]
    return [
        err
        for err in errors
        if not any(err["loc"][: len(f)] == f for f in failed)
        and not any(_is_other_union_member(err, loc) for loc in import_errors)
    ]


def _is_other_union_member(error: Any, import_loc: tuple[Any, ...]) -> bool:
    """A type error of another member of a union that the failed import is part of.

    E.g. `system_prompt: str | list[...]` reports `system_prompt.str` when a list item
    fails to import.
    """
    loc = error["loc"]
    return (
        error["type"] in ("string_type", "union_tag_invalid")
        and len(loc) < len(import_loc)
        and import_loc[: len(loc) - 1] == loc[:-1]
        and import_loc[len(loc) - 1] != loc[-1]
    )


def _value_loc(loc: tuple[Any, ...]) -> tuple[Any, ...]:
    """The location of the value an error is about: up to the last list index, if any."""
    indices = [i for i, part in enumerate(loc) if isinstance(part, int)]
    return loc[: indices[-1] + 1] if indices else loc


def test_every_cli_command_has_a_page():
    import clinspector

    from agentpool_cli.__main__ import cli

    info = clinspector.get_cmd_info(cli)
    commands = {name for name, cmd in info.subcommands.items() if not cmd.hidden}
    pages = {p.stem for p in (DOCS / "cli").glob("*.md")} - {"index"}
    assert commands - pages == set(), "CLI commands without a page in docs/cli/"
    assert pages - commands == set(), "pages in docs/cli/ for commands that do not exist"


def _members(tp: Any) -> list[Any]:
    origin = get_origin(tp)
    if origin is Annotated:
        return _members(get_args(tp)[0])
    if origin is not None:  # unions and containers
        return [m for arg in get_args(tp) for m in _members(arg)]
    return [tp]


def _models(tp: Any) -> list[type[pydantic.BaseModel]]:
    return [m for m in _members(tp) if isinstance(m, type) and issubclass(m, pydantic.BaseModel)]


def _documented_models() -> set[type]:
    documented: set[type] = set()
    for page in DOCS.rglob("*.md"):
        for path, kind in FILTER_REF.findall(page.read_text(encoding="utf-8")):
            module, _, attr = path.replace(":", ".").rpartition(".")
            obj = getattr(importlib.import_module(module), attr)
            documented |= set(_models(obj)) if kind == "union_to_markdown" else {obj}
    return documented


def test_config_unions_are_documented():
    """All members of discriminated unions reachable from the manifest have docs."""
    documented = _documented_models()
    seen: set[type] = set()
    missing: dict[str, list[str]] = {}

    def walk(model: type[pydantic.BaseModel], path: str) -> None:
        if model in seen:
            return
        seen.add(model)
        for name, field in model.model_fields.items():
            members = list(dict.fromkeys(_models(field.annotation)))
            if len(members) > 1 and (undocumented := [m for m in members if m not in documented]):
                missing.setdefault(f"{path}.{name}", [m.__name__ for m in undocumented])
            for member in members:
                walk(member, f"{path}.{name}")

    walk(AgentsManifest, "manifest")
    assert missing == {}, "\n".join(
        f"{path}: {', '.join(names)}" for path, names in missing.items()
    )
