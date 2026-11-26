"""Prompt slash commands."""

from slashed import CommandContext, CommandError

from agentpool.messaging.context import NodeContext  # noqa: TC001
from agentpool_commands.base import NodeCommand
from agentpool_commands.completers import PromptCompleter


class ShowPromptCommand(NodeCommand):
    """Show prompts from configured prompt hubs.

    Usage examples:
      /prompt role.reviewer            # Use builtin prompt
      /prompt langfuse:explain@v2     # Use specific version
      /prompt some_prompt[var=value]  # With variables
    """

    name = "prompt"
    category = "prompts"

    async def execute_command(self, ctx: CommandContext[NodeContext], identifier: str) -> None:
        """Show prompt content.

        Args:
            ctx: Command context
            identifier: Prompt identifier ([provider:]name[@version][?var=val])
        """
        try:
            prompt = await ctx.context.prompt_manager.get(identifier)
            await ctx.print(f"## 📝 Prompt Content\n\n```\n{prompt}\n```")
        except Exception as e:
            raise CommandError(f"Error getting prompt: {e}") from e

    def get_completer(self) -> PromptCompleter:
        """Get completer for prompt names."""
        return PromptCompleter()


class ListPromptsCommand(NodeCommand):
    """List available prompts from all providers.

    Show all prompts available in the current configuration.
    Each prompt is shown with its name and description.
    """

    name = "list-prompts"
    category = "prompts"

    async def execute_command(self, ctx: CommandContext[NodeContext]) -> None:
        """List available prompts from all providers.

        Args:
            ctx: Command context
        """
        prompt_manager = ctx.context.prompt_manager
        prompts = await prompt_manager.list_prompts()
        output_lines = ["\n## 📝 Available Prompts\n"]

        for provider, provider_prompts in prompts.items():
            if not provider_prompts:
                continue

            output_lines.append(f"\n### {provider.title()}\n")
            sorted_prompts = sorted(provider_prompts)

            # For builtin prompts we can show their description
            if provider == "builtin":
                builtin_configs = prompt_manager.get_builtin_prompts()
                for prompt_name in sorted_prompts:
                    prompt = builtin_configs.get(prompt_name)
                    desc = f" - *{prompt.category}*" if prompt else ""
                    output_lines.append(f"- **{prompt_name}**{desc}")
            else:
                # For other providers, just show names
                output_lines.extend(f"- `{prompt_name}`" for prompt_name in sorted_prompts)
        await ctx.print("\n".join(output_lines))
