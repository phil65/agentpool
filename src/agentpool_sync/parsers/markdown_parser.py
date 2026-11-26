"""Markdown parser."""

from typing import TYPE_CHECKING, Any

import yaml

from agentpool_sync.parsers.helpers import FRONTMATTER_PATTERN


if TYPE_CHECKING:
    from agentpool_sync.models import SyncMetadata


class MarkdownSyncParser:
    """Parser for Markdown files using YAML frontmatter.

    Format:
        ---
        sync:
          agent: doc_sync_agent
          dependencies:
            - src/models/*.py
          urls:
            - https://docs.example.com
          context:
            key: value
        ---
    """

    extensions: tuple[str, ...] = (".md", ".mdx")

    def parse(self, content: str) -> SyncMetadata | None:
        import yamling

        from agentpool_sync.models import SyncMetadata

        match = FRONTMATTER_PATTERN.match(content)
        if not match:
            return None

        try:
            frontmatter = yamling.load_yaml(match.group(1))
        except yamling.YAMLError:
            return None

        if not frontmatter or "sync" not in frontmatter:
            return None

        sync_data = frontmatter["sync"]
        return SyncMetadata(
            agent=sync_data.get("agent"),
            dependencies=sync_data.get("dependencies", []),
            urls=sync_data.get("urls", []),
            context=sync_data.get("context", {}),
            last_checked=sync_data.get("last_checked"),
        )

    def update(self, content: str, metadata: SyncMetadata) -> str:
        """Update or inject sync metadata in frontmatter."""
        sync_dict = _metadata_to_dict(metadata)
        if match := FRONTMATTER_PATTERN.match(content):
            try:
                frontmatter = yaml.safe_load(match.group(1)) or {}
            except yaml.YAMLError:
                frontmatter = {}

            frontmatter["sync"] = sync_dict
            new_frontmatter = yaml.dump(frontmatter, default_flow_style=False)
            return f"---\n{new_frontmatter}---\n" + content[match.end() :]

        # No frontmatter exists, create one
        new_frontmatter = yaml.dump({"sync": sync_dict}, default_flow_style=False)
        return f"---\n{new_frontmatter}---\n\n" + content


def _metadata_to_dict(metadata: SyncMetadata) -> dict[str, Any]:
    """Convert metadata to dict for YAML serialization."""
    result: dict[str, Any] = {}
    if metadata.agent:
        result["agent"] = metadata.agent
    if metadata.dependencies:
        result["dependencies"] = metadata.dependencies
    if metadata.urls:
        result["urls"] = metadata.urls
    if metadata.last_checked:
        result["last_checked"] = metadata.last_checked
    if metadata.context:
        result["context"] = metadata.context

    return result
