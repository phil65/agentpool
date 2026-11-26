"""Fail when the built site contains rendering errors that the docs build does not report.

`/// mknodes` blocks and `python exec="true"` code blocks show their errors in the page
instead of failing the build. Run after `zensical build`.

Usage:
    uv run python scripts/check_site.py [site_dir]
"""

import html as html_module
from pathlib import Path
import re
import sys


MARKERS = {
    'class="mknodes-error"': "mknodes block failed to render",
    "Execution Error:": "executed code block raised",
}


def _plain(html: str) -> str:
    """The text of an HTML snippet, whitespace collapsed."""
    text = html_module.unescape(re.sub(r"<[^>]+>", " ", html.split(">", 1)[-1]))
    return " ".join(text.split())[:600]


def main() -> int:
    site = Path(sys.argv[1] if len(sys.argv) > 1 else "site")
    problems = []
    for page in sorted(site.rglob("*.html")):
        text = page.read_text(encoding="utf-8", errors="replace")
        for marker, message in MARKERS.items():
            if (index := text.find(marker)) != -1:
                detail = _plain(text[index : index + 1500])
                problems.append(f"{page.relative_to(site)}: {message}\n    {detail}")
    for problem in problems:
        print(problem)
    print(f"{len(problems)} rendering errors in {site}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
