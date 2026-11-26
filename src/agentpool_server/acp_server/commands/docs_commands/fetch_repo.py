"""Fetch contents from a GitHub repository via UIThub."""

import os
import uuid

import httpx2
from pydantic_ai import UserPromptPart
from slashed import CommandContext  # noqa: TC002

from agentpool.log import get_logger
from agentpool.messaging.context import NodeContext  # noqa: TC001
from agentpool_commands.base import NodeCommand
from agentpool_server.acp_server.session import ACPSession  # noqa: TC001


logger = get_logger(__name__)


class FetchRepoCommand(NodeCommand):
    """Fetch contents from a GitHub repository via UIThub.

    Retrieves repository contents with various filtering options
    and displays them in a structured format.
    """

    name = "fetch-repo"
    category = "docs"

    async def execute_command(  # noqa: PLR0915
        self,
        ctx: CommandContext[NodeContext[ACPSession]],
        repo: str,
        *,
        branch: str | None = None,
        path: str | None = None,
        include_dirs: list[str] | None = None,
        disable_genignore: bool = False,
        exclude_dirs: list[str] | None = None,
        exclude_extensions: list[str] | None = None,
        include_extensions: list[str] | None = None,
        include_line_numbers: bool = True,
        max_file_size: int | None = None,
        max_tokens: int | None = None,
        omit_files: bool = False,
        yaml_string: str | None = None,
    ) -> None:
        """Fetch contents from a GitHub repository.

        Args:
            ctx: Command context with ACP session
            repo: GitHub path (owner/repo)
            branch: Branch name (defaults to main if not provided)
            path: File or directory path within the repository
            include_dirs: List of directories to include
            disable_genignore: Disable .genignore filtering
            exclude_dirs: List of directories to exclude
            exclude_extensions: List of file extensions to exclude
            include_extensions: List of file extensions to include
            include_line_numbers: Include line numbers in HTML/markdown output
            max_file_size: Maximum file size in bytes
            max_tokens: Maximum number of tokens in response
            omit_files: Only return directory structure without file contents
            yaml_string: URL encoded YAML string of file hierarchy to include
        """
        session = ctx.context.data
        assert session
        tc_id = f"fetch-repo-{uuid.uuid4().hex[:8]}"
        try:
            # Build URL
            base_url = f"https://uithub.com/{repo}"
            if branch:
                base_url += f"/tree/{branch}"
            if path:
                base_url += f"/{path}"

            # Build headers with Bearer token
            headers = {"accept": "text/markdown"}
            api_key = os.getenv("UITHUB_API_KEY")
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"

            # Build parameters
            params = {}
            if include_dirs:
                params["dir"] = ",".join(include_dirs)
            if disable_genignore:
                params["disableGenignore"] = "true"
            if exclude_dirs:
                params["exclude-dir"] = ",".join(exclude_dirs)
            if exclude_extensions:
                params["exclude-ext"] = ",".join(exclude_extensions)
            if include_extensions:
                params["ext"] = ",".join(include_extensions)
            if not include_line_numbers:
                params["lines"] = "false"
            if max_file_size:
                params["maxFileSize"] = str(max_file_size)
            if max_tokens:
                params["maxTokens"] = str(max_tokens)
            if omit_files:
                params["omitFiles"] = "true"
            if yaml_string:
                params["yamlString"] = yaml_string

            # Start tool call
            display_path = f"{repo}"
            if branch:
                display_path += f"@{branch}"
            if path:
                display_path += f":{path}"

            await session.output.command_started(
                call_id=tc_id,
                title=f"Fetching repository: {display_path}",
                kind="fetch",
            )

            # Make async HTTP request
            async with httpx2.AsyncClient() as client:
                response = await client.get(
                    base_url,
                    params=params,
                    headers=headers,
                    timeout=30.0,
                )
                response.raise_for_status()
                content = response.text

            # Stage the content for use in agent context
            part_content = f"Repository contents from {display_path}:\n\n{content}"
            staged_part = UserPromptPart(content=part_content)
            ctx.context.agent.staged_content.add([staged_part])
            # Send successful result - wrap in code block for proper display
            staged_count = len(ctx.context.agent.staged_content)
            await session.output.command_updated(
                call_id=tc_id,
                status="completed",
                title=f"Repository {display_path} fetched and staged ({staged_count} total parts)",
                text=f"```\n{content}\n```",
            )

        except httpx2.HTTPStatusError as e:
            logger.exception(
                "HTTP error fetching repository", repo=repo, status=e.response.status_code
            )
            await session.output.command_updated(
                call_id=tc_id,
                status="failed",
                title=f"HTTP {e.response.status_code}: Failed to fetch {repo}",
            )
        except httpx2.RequestError as e:
            logger.exception("Request error fetching repository", repo=repo)
            await session.output.command_updated(
                call_id=tc_id,
                status="failed",
                title=f"Network error: {e}",
            )
        except Exception as e:
            logger.exception("Unexpected error fetching repository", repo=repo)
            await session.output.command_updated(
                call_id=tc_id,
                status="failed",
                title=f"Error: {e}",
            )


if __name__ == "__main__":
    cmd = FetchRepoCommand()
