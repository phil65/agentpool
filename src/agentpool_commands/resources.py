"""Resource management commands."""

from __future__ import annotations

from slashed import CommandContext, CommandError

from agentpool.agents.context import AgentContext  # noqa: TC001
from agentpool.log import get_logger
from agentpool.messaging.context import NodeContext  # noqa: TC001
from agentpool_commands.base import AgentCommand, NodeCommand
from agentpool_commands.markdown_utils import format_table


logger = get_logger(__name__)


class ListResourcesCommand(NodeCommand):
    """Display all resources available to the agent.

    Shows:
    - Resource names and descriptions
    - Resource types and URIs
    - Whether parameters are supported
    - MIME types

    Resource types can be:
    - path: Files or URLs
    - text: Raw text content
    - cli: Command line tools
    - source: Python source code
    - callable: Python functions
    - image: Image files

    Use /show-resource for detailed information about specific resources.
    """

    name = "list-resources"
    category = "resources"

    async def execute_command(self, ctx: CommandContext[NodeContext]) -> None:
        """List available resources.

        Args:
            ctx: Command context
        """
        if ctx.context.pool is None:
            raise ValueError("AgentPool instance not found")

        try:
            fs = ctx.context.pool.vfs_registry.get_fs()
            root = await fs._ls("/", detail=True)

            rows = []
            for entry in root:
                protocol = entry["name"].removesuffix("://")
                info = await fs._info(f"{protocol}://")

                rows.append({
                    "Resource": protocol,
                    "Type": info.get("type", "unknown"),
                    "URI": info.get("uri", ""),
                    "Description": info.get("description", ""),
                })

            headers = ["Resource", "Type", "URI", "Description"]
            table = format_table(headers, rows)
            await ctx.print(f"## 📁 Available Resources\n\n{table}")
        except Exception as e:  # noqa: BLE001
            await ctx.print(f"❌ **Failed to list resources:** {e}")


class ShowResourceCommand(NodeCommand):
    """Display detailed information and content of a specific resource.

    Shows:
    - Resource metadata (type, URI, description)
    - MIME type information
    - Parameter support status
    - Resource content (if loadable)

    For resources that support parameters:
    - Pass parameters as --param arguments
    - Parameters are passed to resource loader

    Examples:
      /show-resource config.yml               # Show configuration file
      /show-resource template --date today    # Template with parameters
      /show-resource image.png               # Show image details
      /show-resource api --key value         # API with parameters

    Note: Some resources might require parameters to be viewed.
    """

    name = "show-resource"
    category = "resources"

    async def execute_command(
        self,
        ctx: CommandContext[NodeContext],
        name: str,
        **kwargs: str,
    ) -> None:
        """Show details or content of a resource.

        Args:
            ctx: Command context
            name: Resource name to show
            **kwargs: Additional parameters for the resource
        """
        if ctx.context.pool is None:
            raise ValueError("No pool available")
        try:
            fs = ctx.context.pool.vfs_registry.get_fs()

            # Get resource info
            try:
                info = await fs._info(f"{name}://")
            except Exception as e:  # noqa: BLE001
                await ctx.print(f"❌ **Resource** `{name}` **not found:** {e}")
                return

            sections = [f"## 📁 Resource: {name}\n"]
            if typ := info.get("type"):
                sections.append(f"**Type:** `{typ}`")
            if uri := info.get("uri"):
                sections.append(f"**URI:** `{uri}`")
            if desc := info.get("description"):
                sections.append(f"Description: {desc}")
            if mime := info.get("mime_type"):
                sections.append(f"MIME Type: {mime}")

            # Try to list contents
            try:
                listing = await fs._ls(f"{name}://", detail=False)
                if listing:
                    sections.extend(["\n# Contents:", "```", *listing, "```"])
            except Exception as e:  # noqa: BLE001
                sections.append(f"\nFailed to list contents: {e}")

            await ctx.print("\n".join(sections))
        except Exception as e:  # noqa: BLE001
            await ctx.print(f"❌ **Error accessing resource:** {e}")


class AddResourceCommand(AgentCommand):
    """Add content from a resource to the next message.

    Parameters are passed to the resource loader if supported.

    Examples:
    /add-resource config.yml
    /add-resource template --date today
    /add-resource api_data --key value
    """

    name = "add-resource"
    category = "resources"

    async def execute_command(
        self,
        ctx: CommandContext[AgentContext],
        resource_path: str,
        *,
        pattern: str | None = None,
        **kwargs: str,
    ) -> None:
        """Add resource content as context for the next message.

        Args:
            ctx: Command context
            resource_path: Resource name or resource/path
            pattern: Pattern for filtering files
            **kwargs: Additional parameters for the resource
        """
        if ctx.context.pool is None:
            raise ValueError("No pool available")
        try:
            # Parse resource name and path
            parts = resource_path.split("/", 1)
            resource_name = parts[0]
            path = parts[1] if len(parts) > 1 else ""

            registry = ctx.context.pool.vfs_registry

            if path:
                if "*" in path:
                    # It's a pattern - use query
                    files = await registry.query(resource_name, pattern=path)
                    for file in files:
                        content = await registry.get_content(resource_name, file)
                        ctx.context.agent.conversation.add_context_message(
                            content, source=f"{resource_name}/{file}", **kwargs
                        )
                    msg = f"Added {len(files)} files from {resource_name!r} matching {path!r}"
                else:
                    # Specific file
                    content = await registry.get_content(resource_name, path)
                    ctx.context.agent.conversation.add_context_message(
                        content, source=f"{resource_name}/{path}", **kwargs
                    )
                    msg = f"Added '{resource_name}/{path}' to context"
            else:
                # Add all content from resource root
                files = await registry.query(resource_name, pattern=pattern or "**/*")
                for file in files:
                    content = await registry.get_content(resource_name, file)
                    ctx.context.agent.conversation.add_context_message(
                        content, source=f"{resource_name}/{file}", **kwargs
                    )
                msg = f"✅ **Added {len(files)} files from** `{resource_name}`"

            await ctx.print(msg)

        except Exception as e:
            msg = f"Error loading resource: {e}"
            logger.exception(msg)
            raise CommandError(msg) from e
