"""Fail when the built site contains rendering errors that the docs build does not report.

`/// mknodes` blocks and `python exec="true"` code blocks show their errors in the page
instead of failing the build. Run after `zensical build`.

Usage:
    uv run python scripts/check_site.py [site_dir]
"""

from __future__ import annotations

from pathlib import Path
import sys


MARKERS = {
    'class="mknodes-error"': "mknodes block failed to render",
    "Execution Error:": "executed code block raised",
}


def main() -> int:
    site = Path(sys.argv[1] if len(sys.argv) > 1 else "site")
    problems = [
        f"{page.relative_to(site)}: {message}"
        for page in sorted(site.rglob("*.html"))
        for marker, message in MARKERS.items()
        if marker in page.read_text(encoding="utf-8", errors="replace")
    ]
    for problem in problems:
        print(problem)
    print(f"{len(problems)} rendering errors in {site}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
