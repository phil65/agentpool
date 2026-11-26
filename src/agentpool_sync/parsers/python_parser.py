"""Python parser."""

import re
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from agentpool_sync.models import SyncMetadata


_BLOCK_PATTERN = re.compile(r"^# /// sync\s*\n((?:^#[^\n]*\n)*?)^# ///$", re.MULTILINE)


class PythonSyncParser:
    """Parser for Python files using PEP723-style blocks.

    Format:
        # /// sync
        # agent = "doc_sync_agent"
        # dependencies = ["src/models/*.py"]
        # urls = ["https://docs.example.com"]
        # [context]
        # key = "value"
        # ///
    """

    extensions: tuple[str, ...] = (".py", ".pyi")

    def parse(self, content: str) -> SyncMetadata | None:

        match = _BLOCK_PATTERN.search(content)
        if not match:
            return None

        # Strip comment prefixes and parse as TOML-like
        block = match.group(1)
        lines = [line.lstrip("#").strip() for line in block.splitlines()]
        text = "\n".join(lines)

        return _parse_toml_like(text)

    def update(self, content: str, metadata: SyncMetadata) -> str:
        """Update or inject sync block into Python file."""
        new_block = _format_block(metadata)
        match = _BLOCK_PATTERN.search(content)

        if match:
            return content[: match.start()] + new_block + content[match.end() :]

        # Inject after module docstring or at top
        return _inject_at_top(content, new_block)


def _format_block(metadata: SyncMetadata) -> str:
    """Format metadata as a sync block."""
    lines = ["# /// sync"]
    if metadata.agent:
        lines.append(f'# agent = "{metadata.agent}"')
    if metadata.dependencies:
        deps = ", ".join(f'"{d}"' for d in metadata.dependencies)
        lines.append(f"# dependencies = [{deps}]")
    if metadata.urls:
        urls = ", ".join(f'"{u}"' for u in metadata.urls)
        lines.append(f"# urls = [{urls}]")
    if metadata.last_checked:
        lines.append(f'# last_checked = "{metadata.last_checked}"')
    if metadata.context:
        lines.append("# [context]")
        for key, value in metadata.context.items():
            lines.append(f'# {key} = "{value}"')

    lines.append("# ///")
    return "\n".join(lines) + "\n"


def _inject_at_top(content: str, block: str) -> str:
    """Inject block after docstring or at very top."""
    # Simple heuristic: after first triple-quoted block if present
    docstring_pattern = re.compile(r'^("""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\')\s*\n')
    if match := docstring_pattern.match(content):
        pos = match.end()
        return content[:pos] + "\n" + block + content[pos:]
    return block + "\n" + content


def _parse_toml_like(text: str) -> SyncMetadata:
    """Simple TOML-like parser for the metadata block."""
    import anyenv

    from agentpool_sync.models import SyncMetadata

    try:
        # TODO: need to check whether this needs to be BaseModel with extra fields allowed
        return anyenv.load_toml(text, return_type=SyncMetadata)
    except anyenv.TomlLoadError:
        return SyncMetadata()
