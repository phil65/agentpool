"""Built-in change handlers for reconciliation."""

from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from collections.abc import Callable

    from agentpool import MessageNode
    from agentpool_sync.models import FileChange

NO_CHANGES_SENTINEL = "NO_CHANGES_NEEDED"
"""The answer the reconciliation prompt asks for when a file needs no update."""


def _means_no_changes(data: Any) -> bool:
    """Whether an agent answer is empty or the no-changes sentinel.

    Tolerates whitespace, a trailing period, quotes and a surrounding code fence.
    """
    if not data:
        return True
    lines = [line for line in str(data).splitlines() if not line.strip().startswith("```")]
    return "\n".join(lines).strip().strip("`'\". ") == NO_CHANGES_SENTINEL


class AgentReconciler:
    """Handler that uses agents from a manifest to reconcile file changes.

    Looks up the agent referenced in file metadata and uses it for reconciliation.
    """

    def __init__(
        self,
        agent_getter: Callable[[str], MessageNode[Any, Any]],
        include_file_content: bool = True,
        max_diff_lines: int = 500,
        default_agent: str | None = None,
    ) -> None:
        """Initialize the reconciler.

        Args:
            agent_getter: Callable that returns an agent by name (e.g., pool.get_agent)
            include_file_content: Whether to include current file content in prompt
            max_diff_lines: Maximum diff lines to include (truncate if larger)
            default_agent: Fallback agent name if file doesn't specify one
        """
        self._get_agent = agent_getter
        self.include_file_content = include_file_content
        self.max_diff_lines = max_diff_lines
        self.default_agent = default_agent

    async def __call__(self, change: FileChange) -> str | None:
        """Process a file change and return updated content.

        Args:
            change: The file change to process

        Returns:
            New file content if changes needed, None otherwise
        """
        agent_name = change.metadata.agent or self.default_agent
        if not agent_name:
            return None

        agent = self._get_agent(agent_name)
        if not agent:
            return None

        prompt = self._build_prompt(change)
        result = await agent.run(prompt)
        return None if _means_no_changes(result.data) else result.data

    def _build_prompt(self, change: FileChange) -> str:
        """Build the reconciliation prompt from change data."""
        parts: list[str] = []

        # File being reviewed
        parts.append(f"## File to review\n`{change.path}`")

        # Include current file content if requested
        if self.include_file_content:
            try:
                from pathlib import Path

                content = Path(change.path).read_text()
                parts.append(f"\n## Current content\n```\n{content}\n```")
            except OSError, UnicodeDecodeError:
                pass

        # Changed dependencies
        if change.changed_deps:
            parts.append(f"\n## Changed dependencies\n{', '.join(change.changed_deps)}")

        # Git diff (potentially truncated)
        if change.diff:
            diff = change.diff
            diff_lines = diff.splitlines()
            if len(diff_lines) > self.max_diff_lines:
                diff = "\n".join(diff_lines[: self.max_diff_lines])
                diff += f"\n\n... (truncated, {len(diff_lines) - self.max_diff_lines} more lines)"

            parts.append(f"\n## Git Diff\n```diff\n{diff}\n```")

        # Changed URLs with content
        if change.changed_urls:
            parts.append(f"\n## Changed URLs\n{', '.join(change.changed_urls)}")

            for url in change.changed_urls:
                url_content = change.url_contents.get(url)
                if url_content:
                    content_lines = url_content.splitlines()
                    if len(content_lines) > self.max_diff_lines:
                        url_content = "\n".join(content_lines[: self.max_diff_lines])
                        remaining = len(content_lines) - self.max_diff_lines
                        url_content += f"\n\n... (truncated, {remaining} more lines)"
                    parts.append(f"\n### Content: {url}\n```\n{url_content}\n```")

        # Additional context from metadata
        if change.metadata.context:
            context_formatted = "\n".join(
                f"- **{k}**: {v}" for k, v in change.metadata.context.items()
            )
            parts.append(f"\n## Additional context\n{context_formatted}")

        # Response instruction
        parts.append(
            "\n## Instructions\n"
            "If the file needs updates based on the dependency changes, "
            "respond with the complete updated file content.\n"
            f"If no changes are needed, respond with exactly: {NO_CHANGES_SENTINEL}"
        )

        return "\n".join(parts)


class LoggingHandler:
    """Simple handler that logs changes without modifying files.

    Useful for dry-run/preview scenarios.
    """

    def __init__(self, logger: Callable[[str], None] | None = None) -> None:
        """Initialize the handler.

        Args:
            logger: Optional logging function (defaults to print)
        """
        self._logger = logger or print

    async def __call__(self, change: FileChange) -> str | None:
        """Log the change and return None (no modifications)."""
        self._logger(f"File needs review: {change.path}")
        self._logger(f"  Agent: {change.metadata.agent or '(none)'}")
        self._logger(f"  Dependencies: {change.changed_deps}")
        self._logger(f"  URLs: {change.changed_urls}")
        return None
