"""Helper functions for running examples in different environments."""

import asyncio
from dataclasses import dataclass
from pathlib import Path
import types
from typing import TYPE_CHECKING, Annotated, Any, Self, Union, get_args, get_origin


if TYPE_CHECKING:
    from collections.abc import Coroutine, Iterator

    from agentpool.resource_providers import ResourceProvider
    from agentpool.tools.base import Tool


EXAMPLES_DIR = Path("src/agentpool_docs/examples")


def is_pyodide() -> bool:
    """Check if code is running in a Pyodide environment."""
    try:
        from js import Object  # type: ignore[import-not-found] # noqa: F401  # ty:ignore[import-not-found]

        return True  # noqa: TRY300
    except ImportError:
        return False


def get_config_path(module_path: str | None = None, filename: str = "config.yml") -> Path:
    """Get the configuration file path based on environment.

    Args:
        module_path: Optional __file__ from the calling module (ignored in Pyodide)
        filename: Name of the config file (default: config.yml)

    Returns:
        Path to the configuration file
    """
    if is_pyodide():
        return Path(filename)
    if module_path is None:
        raise ValueError("module_path is required in non-Pyodide environment")
    return Path(module_path).parent / filename


def run[T](coro: Coroutine[Any, Any, T]) -> T:
    """Run a coroutine in both normal Python and Pyodide environments.

    Without a running loop this is `asyncio.run`. In Pyodide the browser's loop is
    always running; its `run_until_complete` schedules the coroutine instead of blocking.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    if is_pyodide():
        return loop.run_until_complete(coro)
    coro.close()
    msg = "run() cannot be called from a running event loop; await the coroutine instead"
    raise RuntimeError(msg)


@dataclass
class Example:
    """Represents a AgentPool example with its metadata."""

    name: str
    path: Path
    title: str
    description: str
    icon: str = "octicon:code-16"

    @property
    def files(self) -> list[Path]:
        """Get all Python and YAML files (excluding __init__.py)."""
        return [
            f
            for f in self.path.glob("**/*.*")
            if f.suffix in {".py", ".yml"} and not f.name.startswith("__")
        ]

    @property
    def docs(self) -> Path | None:
        """Get docs.md file if it exists."""
        docs = self.path / "docs.md"
        return docs if docs.exists() else None

    @classmethod
    def from_directory(cls, path: Path) -> Self | None:
        """Create Example from directory if it's a valid example."""
        if not path.is_dir() or path.name.startswith("__"):
            return None

        init_file = path / "__init__.py"
        if not init_file.exists():
            return None

        # Load the module to get variables
        namespace: dict[str, str] = {}
        with init_file.open() as f:
            exec(f.read(), namespace)
        # Get metadata with defaults
        return cls(
            name=path.name,
            path=path,
            title=namespace.get("TITLE", path.name.replace("_", " ").title()),
            description=namespace.get("__doc__", ""),
            icon=namespace.get("ICON", "octicon:code-16"),
        )


def iter_examples(root: Path | str | None = None) -> Iterator[Example]:
    """Iterate over all available examples.

    Args:
        root: Optional root directory (defaults to agentpool_docs/examples)
    """
    root = Path(root) if root else EXAMPLES_DIR

    for path in sorted(root.iterdir()):
        if example := Example.from_directory(path):
            yield example


def get_example(name: str, root: Path | str | None = None) -> Example:
    """Get a specific example by name."""
    for example in iter_examples(root):
        if example.name == name:
            return example
    raise KeyError(f"Example {name!r} not found")


def get_discriminator_values(union_type: Any) -> dict[str, type]:
    """Extract discriminator values from a discriminated union.

    Args:
        union_type: A Union type (possibly wrapped in Annotated)

    Returns:
        Dict mapping discriminator values to their model classes
    """
    # Unwrap Annotated if present
    origin = get_origin(union_type)
    if origin is Annotated:
        union_type = get_args(union_type)[0]
        origin = get_origin(union_type)

    # Verify it's a Union
    if origin not in (Union, types.UnionType):
        raise TypeError(f"Expected Union type, got: {union_type}")

    # Extract discriminator values from each model
    result: dict[str, type] = {}
    for model_cls in get_args(union_type):
        if model_cls is type(None):
            continue

        # Get the 'type' field which serves as discriminator
        if hasattr(model_cls, "model_fields") and "type" in model_cls.model_fields:
            field = model_cls.model_fields["type"]
            if field.default is not None:
                result[field.default] = model_cls

    return result


def discriminator_to_filename(discriminator: str) -> str:
    """Convert discriminator value to expected doc filename."""
    return discriminator.replace("_", "-")


def check_docs_for_union(
    union_type: Any,
    docs_dir: Path | str,
    *,
    index_filename: str = "index",
) -> tuple[dict[str, type], set[str]]:
    """Check that all union members have corresponding doc files.

    Args:
        union_type: A discriminated Union type
        docs_dir: Directory containing the doc files
        index_filename: Filename to ignore (default: "index")

    Returns:
        Tuple of (missing_docs, extra_docs) where:
        - missing_docs: Dict of discriminator -> model class for undocumented types
        - extra_docs: Set of doc filenames without corresponding union member

    Example:
        ```python
        from agentpool_config.toolsets import ToolsetConfig
        missing, extra = check_docs_for_union(
            ToolsetConfig,
            Path("docs/configuration/toolsets"),
        )
        ```
    """
    docs_dir = Path(docs_dir)
    discriminators = get_discriminator_values(union_type)
    doc_files = {f.stem for f in docs_dir.glob("*.md") if f.stem != index_filename}
    expected_files = {discriminator_to_filename(d): d for d in discriminators}
    # Find mismatches
    missing_docs = {
        orig_discriminator: discriminators[orig_discriminator]
        for filename, orig_discriminator in expected_files.items()
        if filename not in doc_files
    }
    extra_docs = doc_files - set(expected_files.keys())
    return missing_docs, extra_docs


def _strip_docstring_sections(description: str) -> str:
    """Strip Args/Returns/Raises sections from a docstring, keeping only the summary.

    Args:
        description: The full docstring

    Returns:
        Just the summary/description part without parameter documentation
    """
    result = []
    in_section = False
    for line in description.split("\n"):
        stripped = line.strip()
        # Check if we're entering a standard docstring section
        if stripped in ("Args:", "Arguments:", "Returns:", "Raises:", "Yields:", "Note:"):
            in_section = True
            continue
        # Check if we're in a section (indented content after section header)
        if in_section:
            # If line is empty or still indented, skip it
            if not stripped or line.startswith(("    ", "\t")):
                continue
            # Non-indented non-empty line means new content
            in_section = False
        result.append(line)

    # Clean up trailing empty lines
    while result and not result[-1].strip():
        result.pop()

    return "\n".join(result)


def tool_to_markdown(tool: Tool) -> str:
    """Generate markdown documentation for a single tool.

    Args:
        tool: The tool to document

    Returns:
        Markdown formatted documentation string
    """
    lines = [f"### `{tool.name}`", ""]

    # Strip Args/Returns sections since we have a parameters table
    if tool.description and (desc := _strip_docstring_sections(tool.description)):
        lines.append(desc)
        lines.append("")

    # Get parameters from schema
    schema = tool.schema["function"]
    params_schema = schema.get("parameters", {})
    required = params_schema.get("required", [])
    if properties := params_schema.get("properties", {}):
        lines.append("**Parameters:**")
        lines.append("")
        lines.append("| Name | Type | Required | Description |")
        lines.append("|------|------|----------|-------------|")
        for name, details in properties.items():
            req = "✓" if name in required else ""
            type_info = details.get("type", "-")
            desc = details.get("description", "-")
            # Escape pipe characters in description
            desc = desc.replace("|", "\\|").replace("\n", " ")
            lines.append(f"| `{name}` | {type_info} | {req} | {desc} |")
        lines.append("")

    # Add hints if any are set
    hints = []
    if tool.hints.read_only:
        hints.append("read-only")
    if tool.hints.destructive:
        hints.append("destructive")
    if tool.hints.idempotent:
        hints.append("idempotent")
    if tool.hints.open_world:
        hints.append("open-world")

    if hints:
        lines.append(f"**Hints:** {', '.join(hints)}")
        lines.append("")

    if tool.category:
        lines.append(f"**Category:** {tool.category}")
        lines.append("")

    return "\n".join(lines)


def generate_tool_docs(toolset: ResourceProvider) -> str:
    """Generate markdown documentation for all tools in a toolset.

    Args:
        toolset: A ResourceProvider that provides tools

    Returns:
        Markdown formatted documentation for all tools

    Example:
        ```python exec="true"
        from agentpool_toolsets.builtin.code import CodeTools
        from agentpool.docs.utils import generate_tool_docs

        toolset = CodeTools()
        print(generate_tool_docs(toolset))
        ```
    """
    # Get tools (handle async)
    tools = run(toolset.get_tools())

    if not tools:
        return "*No tools available.*"

    lines = [f"## {toolset.name.replace('_', ' ').title()} Tools", ""]

    lines.extend(tool_to_markdown(tool) for tool in tools)

    return "\n".join(lines)


if __name__ == "__main__":
    # Example usage:
    for ex in iter_examples():
        print(f"\n{ex.title} ({ex.name})")
        print(f"Icon: {ex.icon}")
        print(f"Files: {len(ex.files)}")
        if ex.docs:
            print("Has docs.md")
        print(f"Description: {ex.description.splitlines()[0]}")
