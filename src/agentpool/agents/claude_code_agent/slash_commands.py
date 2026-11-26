import re
from typing import TYPE_CHECKING

from agentpool.agents.context import AgentContext  # noqa: TC001


if TYPE_CHECKING:
    from clawd_code_sdk import ClaudeSDKClient
    from clawd_code_sdk.models import ClaudeCodeCommandInfo
    from slashed import Command, CommandContext


MCP_SUFFIX = " (MCP)"
"""How the CLI marks the commands of MCP server prompts (``server:prompt (MCP)``)."""

_MCP_COMMAND = re.compile(r"^/mcp:([^:\s]+):(\S+)(?:\s+(.*))?$", re.DOTALL)
"""An MCP prompt command as clients name it: ``/mcp:server:prompt args``."""


def command_name(cli_name: str) -> str:
    """The name clients use for a CLI command (``mcp:server:prompt`` for MCP prompts)."""
    if cli_name.endswith(MCP_SUFFIX):
        return f"mcp:{cli_name.removesuffix(MCP_SUFFIX)}"
    return cli_name


def to_cli_command_text(text: str) -> str:
    """Rewrite ``/mcp:server:prompt args`` into the CLI's ``/server:prompt (MCP) args``."""
    if (match := _MCP_COMMAND.match(text)) is None:
        return text
    server, prompt, args = match.groups()
    return f"/{server}:{prompt}{MCP_SUFFIX}" + (f" {args}" if args else "")


def create_claude_code_command(cmd_info: ClaudeCodeCommandInfo) -> Command:
    """Create a slashed Command from Claude Code command info.

    Args:
        cmd_info: Command info dict with 'name', 'description', 'argumentHint'

    Returns:
        A slashed Command that executes via Claude Code
    """
    from slashed import Command

    cli_name = cmd_info.name
    name = command_name(cli_name)
    category = "mcp" if cli_name.endswith(MCP_SUFFIX) else "claude_code"

    async def execute_command(
        ctx: CommandContext[AgentContext],
        args: list[str],
        kwargs: dict[str, str],
    ) -> None:
        """Execute the Claude Code slash command."""
        # Build command string
        args_str = " ".join(args) if args else ""
        if kwargs:
            kwargs_str = " ".join(f"{k}={v}" for k, v in kwargs.items())
            args_str = f"{args_str} {kwargs_str}".strip()
        # Execute via agent run - slash commands go through as prompts
        from agentpool.agents.claude_code_agent.claude_code_agent import ClaudeCodeAgent

        agent = ctx.context.agent
        assert isinstance(agent, ClaudeCodeAgent)
        client = agent._client
        if not client:
            return
        await agent._idle_watch.stop()  # Take over the CLI's message stream
        try:
            await client.query(f"/{cli_name} {args_str}".strip())  # The CLI's spelling
            if (sdk_session_id := await _print_response(ctx, client)) is not None:
                await agent._follow_sdk_session(sdk_session_id)
        finally:
            agent._idle_watch.start(client)

    return Command.from_raw(
        execute_command,
        name=name,
        description=cmd_info.description or f"Claude Code command: {name}",
        category=category,
        usage=cmd_info.argument_hint,
    )


async def _print_response(ctx: CommandContext[AgentContext], client: ClaudeSDKClient) -> str | None:
    """Print the output of a Claude Code slash command until its turn ends.

    Returns:
        The session ID of the command's result (``/clear`` moves to a new session).
    """
    from clawd_code_sdk.models import (
        AssistantMessage,
        LocalCommandOutputMessage,
        ResultErrorMessage,
        ResultSuccessMessage,
        TextBlock,
        UserMessage,
    )

    printed_text = False
    sdk_session_id: str | None = None
    async for msg in client.receive_response():
        if isinstance(msg, ResultSuccessMessage | ResultErrorMessage):
            sdk_session_id = msg.session_id
        match msg:
            case AssistantMessage():
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        await ctx.print(block.text)
                        printed_text = True
            case LocalCommandOutputMessage(content=content):
                await ctx.print(content)
            # Some commands (e.g. /compact) still use legacy XML-tagged
            # output in UserMessage instead of LocalCommandOutputMessage.
            case UserMessage() if parsed := msg.parse_command_output():
                await ctx.print(parsed)
            # The result repeats the reply's text; it is the output only of commands
            # without one (e.g. local commands)
            case ResultSuccessMessage(result=result) if result and not printed_text:
                await ctx.print(result)
            case ResultErrorMessage(subtype=subtype, errors=errors):
                await ctx.print(f"Error: {subtype}")
                if errors:
                    await ctx.print(f"Errors: {errors}")
            case _:
                pass
    return sdk_session_id
