"""Agent management commands."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from slashed import CommandContext, CommandError
from slashed.completers import CallbackCompleter

from agentpool.agents.native_agent import Agent
from agentpool.messaging.context import NodeContext  # noqa: TC001
from agentpool_commands.base import NodeCommand
from agentpool_commands.completers import get_available_agents, get_available_nodes
from agentpool_commands.markdown_utils import format_table


if TYPE_CHECKING:
    from agentpool.delegation import BaseTeam


class CreateAgentCommand(NodeCommand):
    """Create a new agent in the current session.

    Creates a temporary agent that inherits the current agent's model.
    The new agent will exist only for this session.

    Options:
      --system-prompt "prompt"   System instructions for the agent (required)
      --model model_name        Override model (default: same as current agent)
      --role role_name         Agent role (assistant/specialist/overseer)
      --description "text"     Optional description of the agent
      --tools "import_path1|import_path2"   Optional list tools (by import path)

    Examples:
      # Create poet using same model as current agent
      /create-agent poet --system-prompt "Create poems from any text"

      # Create analyzer with different model
      /create-agent analyzer --system-prompt "Analyze in detail" --model gpt-5

      # Create specialized helper
      /create-agent helper --system-prompt "Debug code" --role specialist
    """

    name = "create-agent"
    category = "agents"

    async def execute_command(
        self,
        ctx: CommandContext[NodeContext],
        agent_name: str,
        system_prompt: str = "",
        *,
        model: str | None = None,
        role: str | None = None,
        description: str | None = None,
        tools: str | None = None,
    ) -> None:
        """Create a new agent in the current session.

        Args:
            ctx: Command context
            agent_name: Name for the new agent
            system_prompt: System instructions for the agent
            model: Override model (default: same as current agent)
            role: Agent role (assistant/specialist/overseer)
            description: Optional description of the agent
            tools: Optional pipe-separated list of tools (by import path)
        """
        try:
            if ctx.context.pool is None:
                raise CommandError("No agent pool available")

            # Get model from args or current agent
            current_agent = ctx.context.agent
            tool_list = [t.strip() for t in tools.split("|")] if tools else None
            agent = Agent(
                name=agent_name,
                model=model or current_agent.model_name or "openai:gpt-4o-mini",
                system_prompt=system_prompt or (),
                description=description,
                tools=tool_list,
            )
            # Create and register the new agent
            await ctx.context.pool.add_agent(agent)

            msg = f"✅ **Created agent** `{agent_name}`"
            if tool_list:
                msg += f" with tools: `{', '.join(tool_list)}`"
            await ctx.print(f"{msg}\n\n💡 Use `/connect {agent_name}` to forward messages")

        except ValueError as e:
            raise CommandError(f"Failed to create agent: {e}") from e


class ShowAgentCommand(NodeCommand):
    """Display the complete configuration of the current agent as YAML.

    Shows:
    - Basic agent settings
    - Model configuration (with override indicators)
    - Environment settings (including inline environments)
    - System prompts
    - Response type configuration
    - Other settings

    Fields that have been overridden at runtime are marked with comments.
    """

    name = "show-agent"
    category = "agents"

    async def execute_command(self, ctx: CommandContext[NodeContext]) -> None:
        """Show current agent's configuration.

        Args:
            ctx: Command context
        """
        import yamling

        if ctx.context.pool is None:
            raise ValueError("No pool found")
        manifest = ctx.context.pool.manifest
        config = manifest.agents.get(ctx.context.node_name)
        if not config:
            await ctx.print(f"No agent named {ctx.context.node_name} found in manifest")
            return
        config_dict = config.model_dump(exclude_none=True)  # Get base config as dict
        # Format as annotated YAML
        yaml_config = yamling.dump_yaml(
            config_dict,
            sort_keys=False,
            indent=2,
            default_flow_style=False,
            allow_unicode=True,
        )
        # Add header and format for display
        sections = ["\n**Current Node Configuration:**", "```yaml", yaml_config, "```"]
        await ctx.print("\n".join(sections))


class ListAgentsCommand(NodeCommand):
    """Show all agents defined in the current configuration.

    Displays:
    - Agent name
    - Model used (if specified)
    - Description (if available)
    """

    name = "list-agents"
    category = "agents"

    async def execute_command(self, ctx: CommandContext[NodeContext]) -> None:
        """List all available agents.

        Args:
            ctx: Command context
        """
        if ctx.context.pool is None:
            raise CommandError("No agent pool available")

        rows = []
        # Iterate over all nodes in the pool
        for name, node in ctx.context.pool.all_agents.items():
            # Only include agents (nodes with AGENT_TYPE attribute)
            rows.append({
                "Name": name,
                "Model": str(node.model_name or "") or "",
                "Type": f"{node.AGENT_TYPE}",
                "Description": node.description or "",
            })

        headers = ["Name", "Model", "Type", "Description"]
        table = format_table(headers, rows)
        await ctx.print(f"## 🤖 Available Agents\n\n{table}")


class SwitchAgentCommand(NodeCommand):
    """Switch the current chat session to a different agent.

    Use /list-agents to see available agents.

    Example: /switch-agent url_opener
    """

    name = "switch-agent"
    category = "agents"

    async def execute_command(self, ctx: CommandContext[NodeContext], agent_name: str) -> None:
        """Switch to a different agent.

        Args:
            ctx: Command context
            agent_name: Name of the agent to switch to
        """
        raise RuntimeError("Temporarily disabled")

    def get_completer(self) -> CallbackCompleter:
        """Get completer for agent names."""
        return CallbackCompleter(get_available_agents)


class CreateTeamCommand(NodeCommand):
    """Create a team from existing agents/nodes.

    Teams allow multiple agents to work together, either in parallel
    or sequentially (pipeline).

    Options:
      --mode sequential|parallel   How the team operates (default: sequential)

    Examples:
      # Create a team named "crew" with alice and bob
      /create-team crew alice bob

      # Create a parallel team
      /create-team reviewers alice bob --mode parallel
    """

    name = "create-team"
    category = "agents"

    async def execute_command(
        self,
        ctx: CommandContext[NodeContext],
        name: str,
        *nodes: str,
        mode: Literal["sequential", "parallel"] = "sequential",
    ) -> None:
        """Create a team from existing nodes.

        Args:
            ctx: Command context
            name: Name for the team
            nodes: Names of agents/teams to include
            mode: How the team operates (sequential or parallel)
        """
        if ctx.context.pool is None:
            raise CommandError("No agent pool available")

        if len(nodes) < 2:  # noqa: PLR2004
            raise CommandError("At least 2 members are required to create a team")

        # Verify all nodes exist
        node_names = list(nodes)
        for node_name in node_names:
            if node_name not in ctx.context.pool.nodes:
                available = ", ".join(ctx.context.pool.nodes.keys())
                raise CommandError(f"Node '{node_name}' not found. Available: {available}")
        node_instances = [ctx.context.pool.nodes[name] for name in node_names]
        # Create the team
        if mode == "sequential":
            team: BaseTeam[Any, Any] = ctx.context.pool.create_team_run(node_instances, name=name)
        else:
            team = ctx.context.pool.create_team(node_instances, name=name)

        mode_str = "pipeline" if mode == "sequential" else "parallel"
        await ctx.print(
            f"**Created {mode_str} team** `{team.name}` with nodes: `{', '.join(node_names)}`"
        )

    def get_completer(self) -> CallbackCompleter:
        """Get completer for node names."""
        return CallbackCompleter(get_available_nodes)
