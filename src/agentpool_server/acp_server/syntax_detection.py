"""Syntax highlighting utilities for file content display."""

import os
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from pathlib import Path

DOT_FILE_TO_LANGUAGE = {
    ".bashrc": "bash",
    ".bash_profile": "bash",
    ".bash_logout": "bash",
    ".zshrc": "bash",
    ".zsh_profile": "bash",
    ".profile": "bash",
    ".vimrc": "vim",
    ".gvimrc": "vim",
    ".tmux.conf": "tmux",
    ".gitignore": "gitignore",
    ".gitconfig": "gitconfig",
    ".editorconfig": "editorconfig",
    ".eslintrc": "json",
    ".prettierrc": "json",
    ".babelrc": "json",
}

EXT_TO_LANGUAGE = {
    # Python
    ".py": "python",
    ".pyx": "python",
    ".pyi": "python",
    # JavaScript/TypeScript
    ".js": "javascript",
    ".mjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".mts": "typescript",
    # JVM Languages
    ".java": "java",
    ".kt": "kotlin",
    ".scala": "scala",
    ".groovy": "groovy",
    # C/C++
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hxx": "cpp",
    # Rust
    ".rs": "rust",
    # Go
    ".go": "go",
    # Web
    ".html": "html",
    ".htm": "html",
    ".xml": "xml",
    ".xhtml": "html",
    ".css": "css",
    ".scss": "scss",
    ".sass": "sass",
    ".less": "less",
    # Data formats
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".csv": "csv",
    # Shell/Scripts
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
    ".fish": "fish",
    ".ps1": "powershell",
    ".psm1": "powershell",
    ".bat": "batch",
    ".cmd": "batch",
    # Other languages
    ".rb": "ruby",
    ".php": "php",
    ".sql": "sql",
    ".r": "r",
    ".swift": "swift",
    ".dart": "dart",
    ".lua": "lua",
    ".pl": "perl",
    ".pm": "perl",
    ".vim": "vim",
    ".tex": "latex",
    ".cls": "latex",
    ".sty": "latex",
    # Config/Other
    ".ini": "ini",
    ".cfg": "ini",
    ".conf": "conf",
    ".log": "log",
    ".md": "markdown",
    ".markdown": "markdown",
    ".mdown": "markdown",
    ".mkd": "markdown",
    ".rst": "rst",
    ".txt": "text",
    # Docker
    ".dockerfile": "dockerfile",
    # Makefiles
    ".mk": "makefile",
    # Nginx
    ".nginx": "nginx",
    # Apache
    ".htaccess": "apache",
    # Git
    ".gitignore": "gitignore",
    ".gitconfig": "gitconfig",
}


def get_language_from_path(path: str | Path) -> str:  # noqa: PLR0911
    """Get syntax highlighting language identifier from file path.

    Args:
        path: File path to analyze

    Returns:
        Language identifier for syntax highlighting, or empty string if unknown

    Examples:
        >>> get_language_from_path("src/main.py")
        'python'
        >>> get_language_from_path("config.json")
        'json'
        >>> get_language_from_path("Dockerfile")
        'dockerfile'
        >>> get_language_from_path("unknown.xyz")
        ''
    """
    path_str = str(path)
    _, ext = os.path.splitext(path_str.lower())  # noqa: PTH122
    # Special cases for files without extensions or specific names
    filename = os.path.basename(path_str.lower())  # noqa: PTH119
    # Docker files
    if filename in ("dockerfile", "dockerfile.dev", "dockerfile.prod"):
        return "dockerfile"
    # Build files
    if filename in ("makefile", "rakefile", "justfile"):
        return "makefile"
    if filename in (".rules", ".cursorrules"):
        return "markdown"
    # License files
    if filename in ("license", "licence", "copying", "contributing"):
        return "text"
    if filename == "yarn.lock":
        return "yaml"
    # Config files starting with dot
    if filename in DOT_FILE_TO_LANGUAGE:
        return DOT_FILE_TO_LANGUAGE[filename]
    # README files
    if filename.startswith("readme") and "." not in filename:
        return "text"
    # Package files
    return EXT_TO_LANGUAGE.get(ext, "")


def format_code_block(content: str, language: str = "") -> str:
    r"""Format content as a markdown code block with optional language.

    Args:
        content: The code/text content
        language: Optional language identifier for syntax highlighting

    Returns:
        Formatted markdown code block

    Examples:
        >>> format_code_block('print("hello")', 'python')
        '```python\\nprint("hello")\\n```'
        >>> format_code_block('some text')
        '```\\nsome text\\n```'
    """
    return f"```{language}\n{content}\n```"


def format_file_content(content: str, file_path: str | Path) -> str:
    r"""Format file content as a syntax-highlighted code block.

    Args:
        content: The file content
        file_path: Path to the file (used to determine language)

    Returns:
        Formatted markdown code block with appropriate syntax highlighting

    Examples:
        >>> format_file_content('def hello(): pass', 'main.py')
        '```python\\ndef hello(): pass\\n```'
    """
    language = get_language_from_path(file_path)
    return format_code_block(content, language)


def format_zed_code_block(
    content: str,
    file_path: str | Path,
    start_line: int | None = None,
    end_line: int | None = None,
) -> str:
    r"""Format content as a Zed-compatible code block with path and optional line numbers.

    Args:
        content: The code/text content
        file_path: Path to the file (used in code block header)
        start_line: Optional starting line number (1-based)
        end_line: Optional ending line number (1-based)

    Returns:
        Formatted Zed-compatible code block

    Examples:
        >>> format_zed_code_block('def hello(): pass', 'src/main.py')
        '```src/main.py\\ndef hello(): pass\\n```'
        >>> format_zed_code_block('def hello(): pass', 'src/main.py', 10)
        '```src/main.py#L10\\ndef hello(): pass\\n```'
        >>> format_zed_code_block('def hello(): pass', 'src/main.py', 10, 15)
        '```src/main.py#L10-15\\ndef hello(): pass\\n```'
    """
    path_str = str(file_path)

    # Build line number suffix if provided
    line_suffix = ""
    if start_line is not None:
        if end_line is not None and end_line != start_line:
            line_suffix = f"#L{start_line}-{end_line}"
        else:
            line_suffix = f"#L{start_line}"

    return f"```{path_str}{line_suffix}\n{content}\n```"
