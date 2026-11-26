"""Agent session slash commands."""

from slashed import CommandContext  # noqa: TC002

from agentpool.agents.context import AgentContext  # noqa: TC001
from agentpool_commands.base import AgentCommand


class ClearCommand(AgentCommand):
    """Clear the current chat session history.

    This removes all previous messages but keeps tools and settings.
    """

    name = "clear"
    category = "session"

    async def execute_command(self, ctx: CommandContext[AgentContext]) -> None:
        """Clear chat history.

        Args:
            ctx: Command context
        """
        await ctx.context.agent.conversation.clear()
        await ctx.print("🧹 **Chat history cleared**")


class ResetCommand(AgentCommand):
    """Reset the entire session state.

    - Clears chat history
    - Restores default tool settings
    - Resets any session-specific configurations
    """

    name = "reset"
    category = "session"

    async def execute_command(self, ctx: CommandContext[AgentContext]) -> None:
        """Reset session state.

        Args:
            ctx: Command context
        """
        await ctx.context.native_agent.reset()
        await ctx.print("🔄 **Session state reset** - history cleared, tools and settings restored")
