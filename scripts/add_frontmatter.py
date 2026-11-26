"""Script to add frontmatter metadata from Python doc definitions to markdown files."""

import ast
from pathlib import Path
from typing import Any


# Root paths
DOCS_CODE_DIR = Path("src/agentpool_docs")
DOCS_DIR = Path("docs")


def extract_page_metadata(python_file: Path) -> dict[str, dict[str, Any]]:
    """Extract page metadata from Python documentation files.

    Returns:
        Dict mapping markdown file paths to their metadata (title, icon, etc.)
    """
    content = python_file.read_text()
    tree = ast.parse(content)

    metadata: dict[str, dict[str, Any]] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            # Look for @nav.route.page decorator
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Call):
                    # Check if it's a nav.route.page call
                    # Pattern: nav.route.page(...)
                    is_page_decorator = False
                    if (
                        isinstance(decorator.func, ast.Attribute)
                        and decorator.func.attr == "page"
                        and (
                            isinstance(decorator.func.value, ast.Attribute)
                            and decorator.func.value.attr == "route"
                        )
                    ):
                        is_page_decorator = True

                    if is_page_decorator:
                        page_metadata: dict[str, Any] = {}

                        # Extract positional args (page title)
                        if decorator.args and isinstance(decorator.args[0], ast.Constant):
                            page_metadata["title"] = decorator.args[0].value

                        # Extract keyword args (icon, hide, etc.)
                        for keyword in decorator.keywords:
                            if keyword.arg and isinstance(keyword.value, ast.Constant):
                                page_metadata[keyword.arg] = keyword.value.value

                        # Find mk.MkTemplate call in function body
                        for stmt in node.body:
                            # Handle: page += mk.MkTemplate("path")
                            if isinstance(stmt, ast.AugAssign):
                                right = stmt.value

                                if isinstance(right, ast.Call):
                                    # Check for mk.MkTemplate or MkTemplate
                                    is_template = False
                                    if (
                                        isinstance(right.func, ast.Attribute)
                                        and right.func.attr == "MkTemplate"
                                    ) or (
                                        isinstance(right.func, ast.Name)
                                        and right.func.id == "MkTemplate"
                                    ):
                                        is_template = True

                                    if (
                                        is_template
                                        and right.args
                                        and isinstance(right.args[0], ast.Constant)
                                    ):
                                        md_path = right.args[0].value
                                        assert isinstance(md_path, str)
                                        if page_metadata:
                                            metadata[md_path] = page_metadata
                                            break  # Found the template, move to next function

    return metadata


def read_file_with_frontmatter(file_path: Path) -> tuple[dict[str, Any], str]:
    """Read a markdown file and extract existing frontmatter and content.

    Returns:
        Tuple of (frontmatter_dict, content)
    """
    content = file_path.read_text()

    # Check for existing frontmatter
    if content.startswith("---\n"):
        parts = content.split("---\n", 2)
        if len(parts) >= 3:  # noqa: PLR2004
            # Has frontmatter
            frontmatter_text = parts[1]
            body = parts[2]

            # Parse YAML-like frontmatter
            frontmatter = {}
            for line in frontmatter_text.strip().split("\n"):
                if ":" in line:
                    key, value = line.split(":", 1)
                    frontmatter[key.strip()] = value.strip().strip('"').strip("'")

            return frontmatter, body

    return {}, content


def write_file_with_frontmatter(
    file_path: Path,
    frontmatter: dict[str, Any],
    content: str,
) -> None:
    """Write a markdown file with frontmatter."""
    if not frontmatter:
        file_path.write_text(content)
        return

    # Build frontmatter section
    lines = ["---"]
    for key, value in frontmatter.items():
        if isinstance(value, str):
            lines.append(f'{key}: "{value}"')
        else:
            lines.append(f"{key}: {value}")
    lines.append("---")

    # Ensure content starts on new line after frontmatter
    if content and not content.startswith("\n"):
        content = "\n" + content

    file_path.write_text("\n".join(lines) + content)


def main() -> None:
    """Main function to add frontmatter to all markdown files."""
    # Collect all metadata from Python files
    all_metadata: dict[str, dict[str, Any]] = {}

    for py_file in DOCS_CODE_DIR.glob("*.py"):
        if py_file.name.startswith("_") or py_file.name == "py.typed":
            continue

        metadata = extract_page_metadata(py_file)
        all_metadata.update(metadata)

    # Update markdown files
    updated_count = 0
    for md_rel_path, metadata in all_metadata.items():
        md_path = Path(md_rel_path)

        if not md_path.exists():
            continue

        # Read existing content
        existing_fm, content = read_file_with_frontmatter(md_path)

        # Merge metadata (new values take precedence)
        new_fm = existing_fm.copy()

        # Add title if present and not already set
        if "title" in metadata and "title" not in new_fm:
            new_fm["title"] = metadata["title"]

        # Add icon if present and not already set
        if "icon" in metadata and "icon" not in new_fm:
            new_fm["icon"] = metadata["icon"]

        # Only write if frontmatter changed
        if new_fm != existing_fm:
            write_file_with_frontmatter(md_path, new_fm, content)
            updated_count += 1

    print(f"Updated {updated_count} markdown files with frontmatter")


if __name__ == "__main__":
    main()
