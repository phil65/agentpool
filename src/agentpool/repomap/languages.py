"""Language support utilities for tree-sitter."""

from __future__ import annotations

from pathlib import Path


QUERY_DIRS = ("tree-sitter-language-pack", "tree-sitter-languages")
"""Query folders under ``repomap/queries``, in lookup order.

``tree-sitter-language-pack`` holds the maintained queries; the older
``tree-sitter-languages`` set covers languages the first one lacks
(e.g. TypeScript, Kotlin, PHP, Scala).
"""

QUERY_ALIASES = {"tsx": "typescript"}
"""Grammars that share another language's tag query."""

GRAMMAR_OVERRIDES = {".tsx": "tsx"}
"""Extensions whose grammar differs from grep-ast's mapping.

grep-ast maps ``.tsx`` to the TypeScript grammar, which cannot parse JSX.
"""


def filename_to_grammar(fname: str) -> str | None:
    """Get the tree-sitter grammar for a filename.

    Args:
        fname: Filename (e.g. "foo.py", "src/App.tsx")

    Returns:
        Grammar name, or None if the file type is unknown
    """
    from grep_ast import filename_to_lang  # type: ignore[import-untyped]

    if grammar := GRAMMAR_OVERRIDES.get(Path(fname).suffix):
        return grammar
    lang: str | None = filename_to_lang(fname)
    return lang


def get_scm_fname(lang: str) -> Path | None:
    """Get path to tree-sitter query file for a language.

    Args:
        lang: Grammar name (e.g. "python", "tsx")

    Returns:
        Path to the first matching .scm query file, or None if there is none
    """
    from importlib import resources

    fname = f"{QUERY_ALIASES.get(lang, lang)}-tags.scm"
    queries = resources.files("agentpool") / "repomap" / "queries"
    for folder in QUERY_DIRS:
        ref = queries / folder / fname
        if ref.is_file():
            with resources.as_file(ref) as path:
                return path
    return None


def get_supported_languages() -> set[str]:
    """Get set of languages that have tree-sitter tag support.

    Returns:
        Set of language identifiers that have query files
    """
    from grep_ast.parsers import PARSERS  # type: ignore[import-untyped]

    languages = set(PARSERS.values()) | set(GRAMMAR_OVERRIDES.values())
    return {lang for lang in languages if get_scm_fname(lang)}


def is_language_supported(fname: str) -> bool:
    """Check if a file's language supports tree-sitter tags.

    Args:
        fname: Filename (e.g. "foo.py", "main.rs")

    Returns:
        True if language is supported for tag extraction
    """
    lang = filename_to_grammar(fname)
    return lang is not None and get_scm_fname(lang) is not None


def get_supported_languages_md() -> str:
    """Generate markdown table of supported languages."""
    from grep_ast.parsers import PARSERS

    supported = get_supported_languages()
    # Group by language
    lang_exts: dict[str, list[str]] = {}
    for ext, lang in PARSERS.items():
        grammar = GRAMMAR_OVERRIDES.get(ext, lang)
        if grammar in supported:
            lang_exts.setdefault(grammar, []).append(ext)

    # Build markdown table
    lines = ["| Language | Extensions |", "|----------|------------|"]
    for lang in sorted(lang_exts):
        exts = ", ".join(f"`{e}`" for e in sorted(lang_exts[lang]))
        lines.append(f"| {lang} | {exts} |")

    return "\n".join(lines)
