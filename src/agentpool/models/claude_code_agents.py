"""Configuration models for Claude Code agents."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import ConfigDict, Field
from schemez import Schema
from tokonomics.model_names import AnthropicMaxModelName  # noqa: TC002

from agentpool import log
from agentpool.models.fields import OutputTypeField, SystemPromptField  # noqa: TC001
from agentpool.resource_providers import StaticResourceProvider
from agentpool_config import (
    AnyToolConfig,  # noqa: TC001
    BaseToolConfig,
    MCPServerConfig,
    SSEMCPServerConfig,
    StdioMCPServerConfig,
    StreamableHTTPMCPServerConfig,
)
from agentpool_config.nodes import BaseAgentConfig


if TYPE_CHECKING:
    from collections.abc import Sequence

    from clawd_code_sdk.models import AgentDefinition as CCAgentDefinition, McpServerConfig

    from agentpool.agents.claude_code_agent import ClaudeCodeAgent
    from agentpool.common_types import AnyEventHandlerType
    from agentpool.delegation import AgentPool
    from agentpool.resource_providers import ResourceProvider
    from agentpool.tools.base import Tool
    from agentpool.ui.base import InputProvider

logger = log.get_logger(__name__)

PermissionMode = Literal["default", "acceptEdits", "plan", "bypassPermissions"]
SettingSource = Literal["user", "project", "local"]
ToolName = Literal[
    "Task",
    "TaskOutput",
    "Bash",
    "Glob",
    "Grep",
    "ExitPlanMode",
    "Read",
    "Edit",
    "Write",
    "NotebookEdit",
    "WebFetch",
    "TodoWrite",
    "WebSearch",
    "KillShell",
    "AskUserQuestion",
    "Skill",
    "EnterPlanMode",
    "LSP",
    "Chrome",
]


class AgentDefinition(Schema):
    """Agent definition configuration."""

    description: str = Field(..., title="Agent Description", examples=["QA Assistant"])
    """A brief description of the agent's purpose."""

    prompt: str = Field(..., title="Agent Prompt", examples=["Do XY"])
    """The prompt to use for this agent."""

    tools: list[str] | None = Field(default=None, title="Agent Tools", examples=["Bash"])
    """The tools this agent has access to."""

    model: Literal["sonnet", "opus", "haiku", "inherit"] | str | None = Field(  # noqa: PYI051
        default=None,
        title="Agent Model",
        examples=["sonnet"],
    )
    """The model to use for this agent."""

    memory: SettingSource | None = Field(
        default=None,
        title="Agent Memory",
        examples=["user", "project"],
    )

    disallowed_tools: list[str] | None = Field(
        default=None,
        title="Disallowed Tools",
        examples=["Bash"],
    )
    """Tools this agent is not allowed to use."""

    critical_system_reminder_experimental: str | None = Field(
        default=None,
        title="Critical System Reminder",
        alias="criticalSystemReminder_EXPERIMENTAL",
    )
    """Critical system reminder message to display to the user."""

    skills: list[str] | None = Field(default=None, title="Skills", examples=["my-skill"])
    """Skills this agent has."""

    max_turns: int | None = Field(default=None, title="Max Turns")
    """Maximum number of agentic turns (API round-trips) before stopping."""

    background: bool | None = Field(default=None, title="Run in Background")
    """Whether this agent runs in the background."""

    # hooks: AgentHooksConfig | None = Field(default=None, title="Agent Hooks")
    # """Hook configurations for this agent."""

    effort: Literal["low", "medium", "high", "xhigh", "max"] | int | None = Field(
        default=None,
        title="Reasoning effort",
        examples=["high"],
    )
    """Effort level for thinking depth."""

    permission_mode: PermissionMode | None = Field(
        default=None,
        title="Permission Mode",
        examples=["bypassPermissions"],
    )
    """Permission mode for this agent."""

    isolation: Literal["worktree"] | None = Field(
        default=None,
        title="Isolation Mode",
        examples=["worktree"],
    )
    """Isolation mode. ``"worktree"`` runs the agent in a separate git worktree."""

    mcp_servers: dict[str, MCPServerConfig] | None = None
    """Configuration for MCP servers."""


class ClaudeCodeAgentConfig(BaseAgentConfig):
    """Configuration for Claude Code agents.

    Claude Code agents use the Claude Agent SDK to interact with Claude Code CLI,
    enabling file operations, terminal access, and code editing capabilities.

    Example:
        ```yaml
        agent:
          coder:
            type: claude_code
            model: claude-sonnet-4-5
            allowed_tools:
              - Read
              - Write
              - Bash
            system_prompt: "You are a helpful coding assistant."
            max_turns: 10

          planner:
            permission_mode: plan
            max_thinking_tokens: 10000
            include_builtin_system_prompt: false
            system_prompt:
              - "You are a planning-only assistant."
              - "Focus on architecture decisions."
        ```
    """

    model_config = ConfigDict(
        json_schema_extra={
            "title": "Claude Code Agent Configuration",
            "x-icon": "simple-icons:anthropic",
        }
    )

    type: Literal["claude_code"] = Field(default="claude_code", init=False)
    """Top-level discriminator for agent type."""

    model: AnthropicMaxModelName | str | None = Field(
        default="opus",
        title="Model",
        examples=["claude-sonnet-4-6", "claude-opus-4-6", "claude-haiku-4-5"],
    )
    """Model to use for this agent. Defaults to Claude's default model."""

    allowed_tools: list[ToolName | str] | None = Field(
        default=None,
        title="Allowed Tools",
        examples=[["Read", "Write", "Bash"], ["Read", "Grep", "Glob"]],
    )
    """List of tool names the agent is allowed to use.

    If not specified, all tools are available (subject to permission_mode).
    Common tools: Read, Write, Edit, Bash, Glob, Grep, Task, WebFetch, etc.
    """

    disallowed_tools: list[ToolName | str] | None = Field(
        default=None,
        title="Disallowed Tools",
        examples=[["Bash", "Write"], ["Task"]],
    )
    """List of tool names the agent is NOT allowed to use.

    Takes precedence over allowed_tools if both are specified.
    """

    system_prompt: SystemPromptField = None
    """System prompt for the agent. Can be a string or list of strings/prompt configs.

    By default, this is appended to Claude Code's builtin system prompt.
    Set `include_builtin_system_prompt: false` to use only your custom prompt.

    Docs: https://phil65.github.io/agentpool/YAML%20Configuration/system_prompts_configuration/
    """

    include_builtin_system_prompt: bool = Field(default=True, title="Include Builtin System Prompt")
    """Whether to include Claude Code's builtin system prompt.

    - true (default): `system_prompt` is appended to the builtin
    - false: Only use `system_prompt`, discard the builtin
    """

    max_turns: int | None = Field(default=None, title="Max Turns", ge=1, examples=[5, 10, 20])
    """Maximum number of conversation turns before stopping."""

    max_budget_usd: float | None = Field(
        default=None,
        title="Max Budget (USD)",
        ge=0.0,
        examples=[1.0, 5.0, 10.0],
    )
    """Maximum budget in USD before stopping.

    When set, the agent will stop once the estimated cost exceeds this limit.
    """

    max_thinking_tokens: int | Literal["adaptive"] | None = Field(
        default=None,
        title="Max Thinking Tokens",
        ge=1000,
        examples=[5000, 10000, "adaptive"],
    )
    """Maximum tokens for extended thinking mode.

    When set, enables Claude's extended thinking capability for more
    complex reasoning tasks.
    """

    permission_mode: PermissionMode | None = Field(
        default=None,
        title="Permission Mode",
        examples=["default", "acceptEdits", "plan", "bypassPermissions"],
    )
    """Permission handling mode:

    - "default": Ask for permission on each tool use
    - "acceptEdits": Auto-accept file edits but ask for other operations
    - "plan": Plan-only mode, no execution
    - "bypassPermissions": Skip all permission checks (use with caution)
    """

    output_type: OutputTypeField = None

    env_vars: dict[str, str] | None = Field(
        default=None,
        title="Environment Variables",
        examples=[{"ANTHROPIC_API_KEY": "", "DEBUG": "1"}],
    )
    """Environment variables to set for the agent process.

    Note: Set ANTHROPIC_API_KEY to empty string to force subscription usage.
    """

    add_dir: list[str] | None = Field(
        default=None,
        title="Additional Directories",
        examples=[["/tmp", "/var/log"], ["/home/user/data"]],
    )
    """Additional directories to allow tool access to."""

    builtin_subagents: dict[str, AgentDefinition] | None = Field(
        default=None,
        title="Built-in Subagents",
        examples=[{"sonnet": {"description": "Sonnet agent", "model": "sonnet"}}],
    )
    """Built-in subagents configuration."""

    builtin_tools: list[str] | None = Field(
        default=None,
        title="Built-in Tools",
        examples=[["Bash", "Edit", "Read"], ["Read", "Write", "LSP"], ["Bash", "Chrome"]],
    )
    """Available tools from Claude Code's built-in set.

    Empty list disables all tools. If not specified, all tools are available.
    Different from allowed_tools which filters an already-available set.

    Special tools:
    - "LSP": Enable Language Server Protocol support for code intelligence
      (go to definition, find references, symbol info, etc.)
    - "Chrome": Enable Claude in Chrome integration for browser control
      (opens, navigates, interacts with browser tabs)

    Both LSP and Chrome require additional setup in your environment.
    """

    fallback_model: AnthropicMaxModelName | str | None = Field(
        default=None,
        title="Fallback Model",
        examples=["claude-sonnet-4-5", "claude-haiku-3-5"],
    )
    """Fallback model when default is overloaded."""

    setting_sources: list[SettingSource] | None = Field(
        default=None,
        title="Setting Sources",
        examples=[["user", "project"], ["local"], ["user", "project", "local"]],
    )
    """Setting sources to load configuration from.

    Controls which Claude Code settings files are loaded:
    - "user": User-level settings (~/.config/claude/settings.json)
    - "project": Project-level settings (.claude/settings.json in project root)
    - "local": Local settings (.claude/settings.local.json, git-ignored)

    If not specified, Claude Code will load all available settings.
    """

    use_subscription: bool = Field(default=False, title="Use Claude Subscription")
    """Force usage of Claude subscription instead of API key.

    When True, sets ANTHROPIC_API_KEY to empty string, forcing Claude Code
    to use your Claude.ai subscription for authentication instead of an API key.

    This is useful when:
    - You have a Claude Pro/Team subscription with higher rate limits
    - You want to use subscription credits instead of API credits
    - You're using features only available to subscribers

    Note: Requires an active Claude subscription and logged-in session.
    """

    tools: list[AnyToolConfig | str] = Field(
        default_factory=list,
        title="Tools",
        examples=[
            [
                {"type": "subagent"},
                "webbrowser:open",
                {"type": "import", "import_path": "webbrowser:open"},
            ],
        ],
    )
    """Tools and toolsets to expose to this Claude Code agent via MCP bridge.

    Supports both single tools and toolsets. These will be started as an
    in-process MCP server and made available to Claude Code.

    Docs: https://phil65.github.io/agentpool/YAML%20Configuration/tool_configuration/
    """

    def get_agent[TDeps](
        self,
        event_handlers: Sequence[AnyEventHandlerType] | None = None,
        input_provider: InputProvider | None = None,
        pool: AgentPool[Any] | None = None,
        deps_type: type[TDeps] | None = None,  # type: ignore[valid-type]
    ) -> ClaudeCodeAgent[TDeps, Any]:
        from agentpool.agents.claude_code_agent import ClaudeCodeAgent

        return ClaudeCodeAgent[TDeps, Any].from_config(
            self,
            event_handlers=event_handlers,
            input_provider=input_provider,
            agent_pool=pool,
            deps_type=deps_type,
        )

    def get_tool_providers(self) -> list[ResourceProvider]:
        """Get all resource providers for this agent's tools.

        Processes the unified tools list, separating:
        - Toolsets: Each becomes its own ResourceProvider
        - Single tools: Aggregated into a single StaticResourceProvider

        Returns:
            List of ResourceProvider instances
        """
        from agentpool.tools.base import FunctionTool
        from agentpool_config.toolsets import BaseToolsetConfig

        providers: list[ResourceProvider] = []
        static_tools: list[Tool] = []

        for tool_config in self.tools:
            try:
                match tool_config:
                    case BaseToolsetConfig():
                        providers.append(tool_config.get_provider())
                    case str():
                        static_tools.append(FunctionTool.from_callable(tool_config))
                    case BaseToolConfig():
                        static_tools.append(tool_config.get_tool())
            except Exception:
                logger.exception("Failed to load tool", config=tool_config)
                continue

        if static_tools:
            providers.append(StaticResourceProvider(name="tools", tools=static_tools))

        return providers

    def get_subagent_configs(self) -> dict[str, CCAgentDefinition]:
        from clawd_code_sdk.models import (
            AgentDefinition as CCAgentDefinition,
            McpHttpServerConfig,
            McpSSEServerConfig,
            McpStdioServerConfig,
        )

        dct: dict[str, CCAgentDefinition] = {}

        for k, v in (self.builtin_subagents or {}).items():
            mcp_dct: dict[str, McpServerConfig] = {}
            for server_name, server_config in (v.mcp_servers or {}).items():
                match server_config:
                    case StdioMCPServerConfig(command=command, args=args):
                        mcp_dct[server_name] = McpStdioServerConfig(command=command, args=args)
                    case StreamableHTTPMCPServerConfig(url=url):
                        mcp_dct[server_name] = McpHttpServerConfig(url=str(url))
                    case SSEMCPServerConfig(url=url):
                        mcp_dct[server_name] = McpSSEServerConfig(url=str(url))
            dumped = v.model_dump()
            dumped["mcp_servers"] = mcp_dct
            dct[k] = CCAgentDefinition(**dumped)

        return dct
