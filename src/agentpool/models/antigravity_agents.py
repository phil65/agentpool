"""Configuration models for Google Antigravity agents."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, Self, assert_never

from pydantic import ConfigDict, Field, model_validator
from schemez import Schema

from agentpool.models.agents import AnyToolConfig  # noqa: TC001
from agentpool.models.fields import EnvVarsField, OutputTypeField, SystemPromptField  # noqa: TC001
from agentpool_config.nodes import BaseAgentConfig


if TYPE_CHECKING:
    from collections.abc import Sequence

    from agentpool.agents.antigravity_agent import AntigravityAgent
    from agentpool.common_types import AnyEventHandlerType
    from agentpool.delegation import AgentPool
    from agentpool.resource_providers import ResourceProvider
    from agentpool.tools.base import Tool
    from agentpool.ui.base import InputProvider


type AntigravityThinkingLevel = Literal["minimal", "low", "medium", "high", "xhigh"]
"""Extended thinking depth for Gemini models in Antigravity."""

type AntigravityServiceTier = Literal["standard", "priority", "flex"]
"""Inference service tier for Gemini API requests."""

type AntigravityBehavior = Literal["autonomous", "interactive", "minimal"]
"""Operational execution behavior for the Antigravity harness."""

type AntigravityPolicyMode = Literal[
    "allow_all",
    "confirm_run_command",
    "safe_defaults",
    "auto",
    "deny_all",
]
"""Tool execution approval policy preset."""

type AntigravityBudgetScope = Literal["LIFETIME", "FORWARD_LOOKING"]
"""Evaluation scope for Antigravity session budget caps."""

type AntigravityBuiltinTool = Literal[
    "list_directory",
    "search_directory",
    "find_file",
    "view_file",
    "create_file",
    "edit_file",
    "run_command",
    "ask_question",
    "invoke_subagent",
    "run_workflow",
    "generate_image",
    "search_web",
    "read_url_content",
    "schedule",
    "finish",
]
"""Identifiers for Antigravity's built-in harness tools."""


class AntigravityBudgetConfig(Schema):
    """Session-level budget limits and caps for an Antigravity agent."""

    max_model_calls: int | None = Field(default=None, ge=1)
    """Maximum number of model invocations permitted within the scope."""

    max_tool_calls: int | None = Field(default=None, ge=1)
    """Maximum number of tool invocations permitted within the scope."""

    max_input_tokens: int | None = Field(default=None, ge=1)
    """Maximum net uncached input tokens permitted within the scope."""

    max_output_tokens: int | None = Field(default=None, ge=1)
    """Maximum output tokens (candidates + thoughts) permitted within the scope."""

    max_total_tokens: int | None = Field(default=None, ge=1)
    """Maximum total net tokens permitted within the scope."""

    scope: AntigravityBudgetScope = "LIFETIME"
    """Whether limits apply across the full session lifetime or forward from config/resume."""


class AntigravityAgentConfig(BaseAgentConfig):
    """Configuration for Google Antigravity SDK agents.

    Wraps Google's ``google-antigravity`` Python SDK (optional extra ``agentpool[antigravity]``),
    running Antigravity's local harness with built-in coding tools, policies, multimodal inputs,
    structured output, and AgentPool toolsets bridged via MCP.

    Example:
        ```yaml
        agents:
          coder:
            type: antigravity
            model: gemini-4-hydrogen
            thinking_level: high
            api_key: ${GEMINI_API_KEY}
            policy_mode: confirm_run_command
            enable_sandbox: true
            system_prompt: "You are a careful Python engineer."
            tools:
              - type: agent_management

          vertex_coder:
            type: antigravity
            vertex: true
            project: my-gcp-project
            location: global
            model: gemini-4-hydrogen
            policy_mode: allow_all
        ```
    """

    model_config = ConfigDict(
        json_schema_extra={
            "title": "Antigravity Agent Configuration",
            "x-icon": "simple-icons:google",
        }
    )

    type: Literal["antigravity"] = Field(default="antigravity", init=False)
    """Top-level discriminator for agent type."""

    model: str | None = Field(
        default=None,
        title="Model",
        examples=["gemini-4-hydrogen", "gemini-3.1-pro-preview", "gemini-2.5-pro"],
    )
    """Model name to use. Defaults to the Antigravity SDK default (``gemini-4-hydrogen``)."""

    thinking_level: AntigravityThinkingLevel | None = Field(
        default=None,
        title="Thinking Level",
        examples=["minimal", "low", "medium", "high", "xhigh"],
    )
    """Extended thinking level for Gemini models."""

    service_tier: AntigravityServiceTier | None = Field(
        default=None,
        title="Service Tier",
        examples=["standard", "priority", "flex"],
    )
    """Inference service tier for Gemini requests."""

    max_output_tokens: int | None = Field(default=None, gt=0, title="Max Output Tokens")
    """Maximum output tokens per model response."""

    api_key: str | None = Field(default=None, title="API Key", examples=["${GEMINI_API_KEY}"])
    """Gemini API key or Vertex AI Express key (``${VAR}`` references are expanded).

    Falls back to the ``GEMINI_API_KEY`` environment variable when using the Gemini API.
    """

    vertex: bool | None = Field(default=None, title="Use Vertex AI")
    """Use Vertex AI backend instead of the Gemini Developer API.

    When omitted, auto-detected from ``GOOGLE_GENAI_USE_VERTEXAI`` /
    ``GOOGLE_GENAI_USE_ENTERPRISE``.
    """

    project: str | None = Field(default=None, title="GCP Project", examples=["my-gcp-project"])
    """Google Cloud project ID for Vertex AI standard mode (default: ``GOOGLE_CLOUD_PROJECT``)."""

    location: str | None = Field(
        default=None,
        title="GCP Location",
        examples=["global", "us-central1"],
    )
    """Google Cloud location for Vertex AI standard mode (default: ``GOOGLE_CLOUD_LOCATION``)."""

    base_url: str | None = Field(
        default=None,
        title="Base URL",
        examples=["http://localhost:11434/v1"],
    )
    """Optional custom endpoint base URL (for gateways/proxies or OpenAI-compatible servers)."""

    http_headers: dict[str, str] = Field(default_factory=dict, title="HTTP Headers")
    """Optional custom HTTP headers for model API requests (``${VAR}`` references are expanded)."""

    openai_compatible: bool = Field(default=False, title="OpenAI-Compatible Endpoint")
    """Use ``LocalOpenAIAgentConfig`` for local OpenAI-compatible servers (e.g. Ollama)."""

    system_prompt: SystemPromptField = None
    """System instructions for the agent (appended to Antigravity's defaults unless
    ``include_builtin_system_prompt`` is ``false``)."""

    include_builtin_system_prompt: bool = Field(
        default=True,
        title="Include Builtin System Prompt",
    )
    """Whether to keep Antigravity's default system instructions.

    - ``true`` (default): ``system_prompt`` is appended via ``TemplatedSystemInstructions``.
    - ``false``: ``system_prompt`` completely replaces defaults via ``CustomSystemInstructions``.
    """

    behavior: AntigravityBehavior = Field(
        default="autonomous",
        title="Agent Behavior",
        examples=["autonomous", "interactive", "minimal"],
    )
    """Operational execution behavior for the Antigravity harness."""

    policy_mode: AntigravityPolicyMode = Field(
        default="allow_all",
        title="Tool Policy Mode",
        examples=["allow_all", "confirm_run_command", "safe_defaults", "auto", "deny_all"],
    )
    """Tool execution approval policy preset.

    - ``allow_all``: Approve all tool calls without confirmation (default for programmatic use).
    - ``confirm_run_command``: Allow tools except ``run_command``, which asks the input provider.
    - ``safe_defaults``: Allow read-only tools and ask the input provider for all other calls.
    - ``auto``: Use Antigravity's built-in safety assessor, asking the input provider when flagged.
    - ``deny_all``: Deny all tool calls.
    """

    workspace_only: bool = Field(default=False, title="Restrict File Tools to Workspaces")
    """Restrict built-in file tools to paths inside configured ``workspaces``."""

    enabled_tools: list[AntigravityBuiltinTool] | None = Field(
        default=None,
        title="Enabled Builtin Tools",
        examples=[["view_file", "edit_file", "create_file", "run_command"]],
    )
    """Explicit allowlist of built-in tools. Mutually exclusive with ``disabled_tools``."""

    disabled_tools: list[AntigravityBuiltinTool] | None = Field(
        default=None,
        title="Disabled Builtin Tools",
        examples=[["generate_image", "run_command"]],
    )
    """Explicit denylist of built-in tools. Mutually exclusive with ``enabled_tools``."""

    enable_subagents: bool = Field(default=True, title="Enable Subagents")
    """Whether the Antigravity harness may spawn and delegate to subagents."""

    max_subagent_depth: int | None = Field(default=None, ge=1, title="Max Subagent Depth")
    """Maximum subagent recursion depth (defaults to 1 when omitted)."""

    enable_daemons: bool = Field(default=False, title="Enable Daemon Commands")
    """Allow ``run_command`` to spawn long-running background daemon processes."""

    command_timeout: float | None = Field(default=None, gt=0, title="Command Timeout (s)")
    """Maximum execution duration in seconds for ``run_command``."""

    enable_sandbox: bool = Field(default=False, title="Enable OS Command Sandbox")
    """Execute ``run_command`` inside Antigravity's OS-level sandbox (exebox) when available."""

    tool_output_max_tokens: int | None = Field(
        default=None,
        ge=0,
        title="Tool Output Truncation Limit",
    )
    """Maximum estimated tokens for a single tool response before truncating (0 disables)."""

    mcp_max_output_bytes: int | None = Field(default=None, gt=0, title="MCP Max Output Bytes")
    """Maximum bytes of an MCP tool output before spilling to disk (SDK default: 4096)."""

    workspaces: list[str] = Field(default_factory=list, title="Workspaces")
    """Workspace directories exposed to Antigravity (defaults to the agent's ``env.cwd``)."""

    skills_paths: list[str] = Field(default_factory=list, title="Skills Paths")
    """Filesystem directories containing ``SKILL.md`` files for Antigravity to load."""

    save_dir: str | None = Field(default=None, title="Session Save Directory")
    """Directory where Antigravity persists trajectories for session resume.

    Defaults to an AgentPool-managed data directory so sessions can be resumed.
    """

    app_data_dir: str | None = Field(default=None, title="App Data Directory")
    """Optional absolute path for Antigravity harness app data."""

    compaction_token_threshold: int | None = Field(
        default=None,
        gt=0,
        title="Compaction Token Threshold",
    )
    """Token ceiling before Antigravity compacts older conversation history."""

    budget: AntigravityBudgetConfig | None = Field(default=None, title="Budget Limits")
    """Optional session-level token and call count budget caps."""

    env_vars: EnvVarsField = Field(default_factory=dict)
    """Extra environment variables passed to the localharness subprocess (``${VAR}`` expanded)."""

    tools: list[AnyToolConfig | str] = Field(
        default_factory=list,
        title="Tools",
        examples=[[{"type": "agent_management"}, "webbrowser:open"]],
    )
    """AgentPool tools and toolsets to expose to Antigravity via an in-process MCP bridge."""

    output_type: OutputTypeField = None
    """Structured output type enforced via Antigravity's ``response_schema`` / ``finish`` tool."""

    debug: bool = Field(default=False, title="Enable SDK Debug Logging")
    """Enable verbose Antigravity SDK logging and server-side tracing."""

    sdk_options: dict[str, Any] = Field(
        default_factory=dict,
        title="SDK options",
        examples=[{"retry_config": {"api_retry": {"max_retries": 5}}}],
    )
    """Fields of the Antigravity SDK's agent config passed through as they are.

    ``LocalAgentConfig`` (or ``LocalOpenAIAgentConfig`` with ``openai_compatible``), for
    what agentpool has no setting of its own, by the SDK's field names: e.g.
    ``retry_config``, ``subagents``, ``inline_skills``, ``triggers``, ``models``. Values
    are converted to the SDK's types. Fields agentpool sets itself (model, instructions,
    tools, policies, hooks, MCP servers, ...) are refused: use their own settings.
    """

    @model_validator(mode="after")
    def _validate_config(self) -> Self:
        if self.enabled_tools is not None and self.disabled_tools is not None:
            msg = "`enabled_tools` and `disabled_tools` are mutually exclusive"
            raise ValueError(msg)
        if self.openai_compatible and not self.base_url:
            msg = "`openai_compatible: true` requires `base_url`"
            raise ValueError(msg)
        if self.vertex and self.api_key and (self.project or self.location):
            msg = "Vertex AI cannot combine `api_key` (Express Mode) with `project`/`location`"
            raise ValueError(msg)
        if self.max_subagent_depth is not None and not self.enable_subagents:
            msg = "`max_subagent_depth` requires `enable_subagents: true`"
            raise ValueError(msg)
        return self

    def get_api_key(self) -> str | None:
        """Return ``api_key`` with ``${VAR}`` references expanded."""
        from agentpool.utils.env_refs import expand_env_refs

        if self.api_key is None:
            return None
        return expand_env_refs({"api_key": self.api_key})["api_key"]

    def get_http_headers(self) -> dict[str, str]:
        """Return ``http_headers`` with ``${VAR}`` references expanded."""
        from agentpool.utils.env_refs import expand_env_refs

        return expand_env_refs(self.http_headers)

    def get_env_vars(self) -> dict[str, str]:
        """Return ``env_vars`` with ``${VAR}`` references expanded."""
        from agentpool.utils.env_refs import expand_env_refs

        return expand_env_refs(self.env_vars)

    def get_tool_providers(self) -> list[ResourceProvider]:
        """Resolve configured tools and toolsets into ResourceProvider instances."""
        from agentpool import log
        from agentpool.resource_providers import StaticResourceProvider
        from agentpool.tools.base import FunctionTool
        from agentpool_config import BaseToolConfig
        from agentpool_config.toolsets import BaseToolsetConfig

        logger = log.get_logger(__name__)
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
                    case _ as unreachable:
                        assert_never(unreachable)
            except Exception:
                logger.exception("Failed to load tool", config=tool_config)
                continue

        if static_tools:
            providers.append(StaticResourceProvider(tools=static_tools))

        return providers

    def get_agent[TDeps](
        self,
        event_handlers: Sequence[AnyEventHandlerType] | None = None,
        input_provider: InputProvider | None = None,
        pool: AgentPool[Any] | None = None,
        deps_type: type[TDeps] | None = None,  # type: ignore[valid-type]
    ) -> AntigravityAgent[TDeps, Any]:
        from agentpool.agents.antigravity_agent import AntigravityAgent

        return AntigravityAgent[TDeps, Any].from_config(
            self,
            event_handlers=event_handlers,
            input_provider=input_provider,
            agent_pool=pool,
            deps_type=deps_type,
        )
