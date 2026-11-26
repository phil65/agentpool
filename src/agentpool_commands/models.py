"""Model-related commands."""

from typing import TYPE_CHECKING, Any

from slashed import CommandContext  # noqa: TC002
from slashed.completers import CallbackCompleter

from agentpool.agents.context import AgentContext  # noqa: TC001
from agentpool_commands.base import NodeCommand
from agentpool_commands.completers import get_model_names


if TYPE_CHECKING:
    from agentpool.messaging import MessageNode


class ListModelsCommand(NodeCommand):
    """List available models for the current agent.

    Displays all models that can be used with the current agent,
    formatted as a markdown table with model ID, name, and description.

    Examples:
      /list-models
    """

    name = "list-models"
    category = "model"

    async def execute_command(self, ctx: CommandContext[AgentContext]) -> None:
        """List available models.

        Args:
            ctx: Command context
        """
        agent = ctx.context.agent
        models = await agent.get_available_models()

        if not models:
            await ctx.print("No models available for this agent.")
            return

        # Build markdown table
        lines = [
            "| Model ID | Name | Description |",
            "|----------|------|-------------|",
        ]

        for model in models:
            model_id = model.id_override or model.id
            name = model.name or ""
            description = model.description or ""
            # Escape pipe characters in fields
            name = name.replace("|", "\\|")
            description = description.replace("|", "\\|")
            lines.append(f"| `{model_id}` | {name} | {description} |")

        await ctx.print("\n".join(lines))

    @classmethod
    def supports_node(cls, node: MessageNode[Any, Any]) -> bool:
        from agentpool import ACPAgent, Agent, ClaudeCodeAgent

        return isinstance(node, Agent | ACPAgent | ClaudeCodeAgent)


class SetModelCommand(NodeCommand):
    """Change the language model for the current conversation.

    The model change takes effect immediately for all following messages.
    Previous messages and their context are preserved.

    Examples:
      /set-model gpt-5
      /set-model openai:gpt-5-mini
      /set-model claude-2

    Note: Available models depend on your configuration and API access.
    """

    name = "set-model"
    category = "model"

    async def execute_command(self, ctx: CommandContext[AgentContext], model: str) -> None:
        """Change the model for the current conversation.

        Args:
            ctx: Command context
            model: Model name to switch to
        """
        try:
            # Create new session with model override
            await ctx.context.agent.set_model(model)
            await ctx.print(f"✅ **Model changed to:** `{model}`")
        except Exception as e:  # noqa: BLE001
            await ctx.print(f"❌ **Failed to change model:** {e}")

    def get_completer(self) -> CallbackCompleter:
        """Get completer for model names."""
        return CallbackCompleter(get_model_names)

    @classmethod
    def supports_node(cls, node: MessageNode[Any, Any]) -> bool:
        from agentpool import ACPAgent, Agent, ClaudeCodeAgent

        return isinstance(node, Agent | ACPAgent | ClaudeCodeAgent)
