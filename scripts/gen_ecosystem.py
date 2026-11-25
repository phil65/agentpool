"""Generate `docs/architecture/ecosystem.md`: agentpool's own (phil65) dependency packages.

Reads `uv.lock` (runtime dependencies incl. extras, and the `docs` dependency group) and the
installed package metadata (summaries). Run after dependency changes:

    uv sync --all-extras && uv run python scripts/gen_ecosystem.py
"""

from __future__ import annotations

from importlib import metadata
from pathlib import Path
import tomllib


ROOT = Path(__file__).parent.parent
OUTPUT = ROOT / "docs" / "architecture" / "ecosystem.md"

# Packages maintained in the same ecosystem (GitHub phil65/*), grouped by role.
GROUPS: dict[str, list[str]] = {
    "Protocols & agent backends": [
        "aclientp",
        "clawd-code-sdk",
        "codexed",
        "pyt3client",
        "llmling-models",
        "tokonomics",
    ],
    "Tools & execution": [
        "exxec",
        "docler",
        "searchly",
        "sublime-search",
        "ripgrep-rs",
        "evented",
        "anyvoice",
        "githarbor",
    ],
    "Prompting & UI": ["promptantic", "slashed", "toprompt", "jinjarope", "clinspector"],
    "Foundation": ["anyenv", "upathtools", "schemez", "epregistry", "yamling"],
    "Docs toolchain": ["mknodes"],
}
OWN = {name for names in GROUPS.values() for name in names} | {"agentpool"}
# GitHub repo names that differ from the package name
REPOS = {"ripgrep-rs": "python-ripgrep", "clawd-code-sdk": "claude-agent-sdk-python"}


def _node(name: str) -> str:
    return name.replace("-", "_")


def _deps(pkg: dict, *, extras: bool) -> list[tuple[str, str | None]]:
    out: list[tuple[str, str | None]] = [(d["name"], None) for d in pkg.get("dependencies", [])]
    if extras:
        for extra, deps in pkg.get("optional-dependencies", {}).items():
            out += [(d["name"], extra) for d in deps]
    return out


def _closure(
    pkgs: dict[str, dict], start: list[tuple[str, str | None]]
) -> tuple[set[tuple[str, str, str | None]], set[str]]:
    """Edges between own packages reachable from given start edges (runtime deps)."""
    edges: set[tuple[str, str, str | None]] = set()
    seen: set[str] = set()
    todo = [name for name, _ in start if name in OWN]
    plain = {name for name, via in start if via is None}
    edges |= {
        ("agentpool", name, via)
        for name, via in start
        if name in OWN and (via is None or name not in plain)
    }
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        for dep, _ in _deps(pkgs.get(name, {}), extras=False):
            if dep in OWN:
                edges.add((name, dep, None))
                todo.append(dep)
    return edges, seen


def _group_of(name: str) -> str:
    return next(group for group, names in GROUPS.items() if name in names)


def _mermaid(edges: set[tuple[str, str, str | None]], nodes: set[str]) -> str:
    """Groups as subgraphs; agentpool points to the groups, edges between packages are kept."""
    lines = ["```mermaid", "graph LR", "  agentpool[agentpool]"]
    for group, names in GROUPS.items():
        members = [n for n in names if n in nodes]
        if not members:
            continue
        lines.append(f'  subgraph {_node(group.split()[0])}["{group}"]')
        lines += [f"    {_node(n)}[{n}]" for n in members]
        lines.append("  end")
    groups = sorted({_group_of(b) for a, b, _ in edges if a == "agentpool"})
    lines += [f"  agentpool --> {_node(g.split()[0])}" for g in groups]
    for a, b, _via in sorted(e for e in edges if e[0] != "agentpool"):
        lines.append(f"  {_node(a)} --> {_node(b)}")
    lines.append("```")
    return "\n".join(lines)


def _summary(name: str) -> str:
    try:
        return (metadata.metadata(name).get("Summary") or "").strip()
    except metadata.PackageNotFoundError:
        return ""


def _table(nodes: set[str], edges: set[tuple[str, str, str | None]]) -> str:
    used_by: dict[str, set[str]] = {}
    for a, b, via in edges:
        used_by.setdefault(b, set()).add(f"{a} (`{via}` extra)" if via else a)
    rows = ["| Package | Role | Description | Used by |", "|---|---|---|---|"]
    for group, names in GROUPS.items():
        for name in names:
            if name not in nodes:
                continue
            repo = REPOS.get(name, name)
            link = f"[{name}](https://github.com/phil65/{repo})"
            users = ", ".join(sorted(used_by.get(name, set())))
            rows.append(f"| {link} | {group} | {_summary(name)} | {users} |")
    return "\n".join(rows)


def main() -> None:
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    pkgs = {p["name"]: p for p in lock["package"]}
    root = pkgs["agentpool"]
    app_edges, app_nodes = _closure(pkgs, _deps(root, extras=True))
    docs_start: list[tuple[str, str | None]] = [
        (d["name"], None) for d in root.get("dev-dependencies", {}).get("docs", [])
    ]
    docs_edges, docs_nodes = _closure(pkgs, docs_start)
    text = f"""---
title: Ecosystem
description: The phil65 packages agentpool is built from, generated from uv.lock
---

# Ecosystem

AgentPool is built from a set of smaller packages maintained alongside it
([github.com/phil65](https://github.com/phil65)). This page shows how they depend on each other.
Generated from `uv.lock` by `scripts/gen_ecosystem.py`; regenerate it after dependency changes.

## Runtime (incl. extras)

`agentpool` uses packages of every group (see "Used by" in the table, incl. the extras they come
with); the arrows between packages show how they depend on each other.

{_mermaid(app_edges, app_nodes)}

{_table(app_nodes, app_edges)}

## Docs toolchain

The documentation (`docs` dependency group) is built with [Zensical](https://zensical.org);
[mknodes](https://github.com/phil65/mknodes) provides the `/// mknodes` blocks and generated pages.

{_mermaid(docs_edges, docs_nodes)}

{_table(docs_nodes, docs_edges)}
"""
    OUTPUT.write_text(text, encoding="utf-8")
    rel = OUTPUT.relative_to(ROOT)
    print(f"wrote {rel}: {len(app_nodes)} runtime / {len(docs_nodes)} docs packages")


if __name__ == "__main__":
    main()
