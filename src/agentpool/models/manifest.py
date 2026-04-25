"""Models for agent configuration."""

from __future__ import annotations

from functools import cached_property
from typing import TYPE_CHECKING, Annotated, Any, Self

from llmling_models_config import AnyModelConfig, StringModelConfig
from pydantic import ConfigDict, Field, model_validator
from schemez import Schema
from upathtools_config import FilesystemConfigType
from upathtools_config.base import URIFileSystemConfig

from agentpool import log
from agentpool.models.acp_agents import ACPAgentConfigTypes
from agentpool.models.agents import NativeAgentConfig
from agentpool.models.agui_agents import AGUIAgentConfig
from agentpool.models.claude_code_agents import ClaudeCodeAgentConfig
from agentpool.models.codex_agents import CodexAgentConfig
from agentpool.models.file_agents import FileAgentConfig
from agentpool_config.commands import CommandConfig, StaticCommandConfig
from agentpool_config.compaction import CompactionConfig
from agentpool_config.converters import ConversionConfig
from agentpool_config.mcp_server import BaseMCPServerConfig, MCPServerConfig
from agentpool_config.observability import ObservabilityConfig
from agentpool_config.output_types import StructuredResponseConfig
from agentpool_config.pool_server import MCPPoolServerConfig
from agentpool_config.storage import StorageConfig
from agentpool_config.system_prompts import PromptLibraryConfig
from agentpool_config.task import Job
from agentpool_config.teams import TeamConfig


if TYPE_CHECKING:
    from upathtools import JoinablePathLike

    from agentpool.messaging.compaction import CompactionPipeline
    from agentpool.models.acp_agents import BaseACPAgentConfig
    from agentpool_config.nodes import NodeConfig
logger = log.get_logger(__name__)


# Model union with discriminator for typed configs
_FileSystemConfigUnion = Annotated[
    FilesystemConfigType | URIFileSystemConfig,
    Field(discriminator="type"),
]

# Final type allowing models or URI shorthand string
ResourceConfig = _FileSystemConfigUnion | str

# Unified agent config type with top-level discriminator
AnyAgentConfig = Annotated[
    NativeAgentConfig
    | AGUIAgentConfig
    | ClaudeCodeAgentConfig
    | CodexAgentConfig
    | ACPAgentConfigTypes,
    Field(discriminator="type"),
]


class AgentsManifest(Schema):
    """Complete agent configuration manifest defining all available agents.

    This is the root configuration that:
    - Defines available response types (both inline and imported)
    - Configures all agent instances and their settings
    - Sets up custom role definitions and capabilities
    - Manages environment configurations

    A single manifest can define multiple agents that can work independently
    or collaborate through the orchestrator.
    """

    INHERIT: str | list[str] | None = None
    """Inheritance references."""

    name: str | None = None
    """Optional name for this manifest.

    Useful for identification when working with multiple configurations.
    """

    resources: dict[str, ResourceConfig] = Field(
        default_factory=dict,
        examples=[
            {"docs": "file://./docs", "data": "s3://bucket/data"},
            {
                "api": {
                    "type": "uri",
                    "uri": "https://api.example.com",
                    "cached": True,
                }
            },
        ],
    )
    """Resource configurations defining available filesystems.

    Supports both full config and URI shorthand:
        resources:
          docs: "file://./docs"  # shorthand
          data:  # full config
            type: "uri"
            uri: "s3://bucket/data"
            cached: true
    """

    agents: dict[str, AnyAgentConfig] = Field(
        default_factory=dict,
        json_schema_extra={
            "documentation_url": "https://phil65.github.io/agentpool/YAML%20Configuration/agent_configuration/"
        },
    )
    """Mapping of agent IDs to their configurations.

    All agent types are unified under this single dict, discriminated by the 'type' field:
    - type: "native" (default) - pydantic-ai based agents
    - type: "agui" - AG-UI protocol agents
    - type: "claude_code" - Claude Agent SDK agents
    - type: "acp" - ACP protocol agents (further discriminated by 'provider')

    Example:
        ```yaml
        agents:
          assistant:
            type: native
            model: openai:gpt-4
            system_prompt: "You are a helpful assistant."

          coder:
            type: claude_code
            cwd: /path/to/project
            model: claude-sonnet-4-5

          orchestrator:
            type: acp
            provider: claude
            model: sonnet

          remote:
            type: agui
            endpoint: http://localhost:8000/agent/run
        ```

    Docs: https://phil65.github.io/agentpool/YAML%20Configuration/agent_configuration/
    """

    default_agent: str | None = None
    """Name of the default/main agent.

    When set, this agent is used as the primary entry point for conversations.
    If not set, falls back to the first agent in the agents dict.

    Example:
        ```yaml
        agents:
          assistant:
            type: native
            model: openai:gpt-4
          reviewer:
            type: native
            model: openai:gpt-4

        default_agent: assistant
        ```
    """

    file_agents: dict[str, str | FileAgentConfig] = Field(
        default_factory=dict,
        examples=[
            {
                "code_reviewer": ".claude/agents/reviewer.md",
                "debugger": "https://example.com/agents/debugger.md",
                "custom": {"type": "opencode", "path": "./agents/custom.md"},
            }
        ],
    )
    """Mapping of agent IDs to file-based agent definitions.

    Supports both simple path strings (auto-detect format) and explicit config
    with type discriminator.
    Files must have YAML frontmatter in Claude Code, OpenCode, or AgentPool format.
    The markdown body becomes the system prompt.

    Formats:
      - claude: name, description, tools (comma-separated), model, permissionMode
      - opencode: description, mode, model, temperature, maxSteps, tools (dict)
      - native: Full NativeAgentConfig fields in frontmatter

    Example:
        ```yaml
        file_agents:
          reviewer: .claude/agents/reviewer.md  # auto-detect
          debugger:
            type: opencode  # explicit type
            path: ./agents/debugger.md
        ```
    """

    teams: dict[str, TeamConfig] = Field(
        default_factory=dict,
        json_schema_extra={
            "documentation_url": "https://phil65.github.io/agentpool/YAML%20Configuration/team_configuration/"
        },
    )
    """Mapping of team IDs to their configurations.

    Docs: https://phil65.github.io/agentpool/YAML%20Configuration/team_configuration/
    """

    storage: StorageConfig = Field(
        default_factory=StorageConfig,
        json_schema_extra={
            "documentation_url": "https://phil65.github.io/agentpool/YAML%20Configuration/storage_configuration/"
        },
    )
    """Storage provider configuration.

    Docs: https://phil65.github.io/agentpool/YAML%20Configuration/storage_configuration/
    """

    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    """Observability provider configuration."""

    conversion: ConversionConfig = Field(default_factory=ConversionConfig)
    """Document conversion configuration."""

    responses: dict[str, StructuredResponseConfig] = Field(
        default_factory=dict,
        json_schema_extra={
            "documentation_url": "https://phil65.github.io/agentpool/YAML%20Configuration/response_configuration/"
        },
    )
    """Mapping of response names to their definitions.

    Docs: https://phil65.github.io/agentpool/YAML%20Configuration/response_configuration/
    """

    model_variants: dict[str, AnyModelConfig] = Field(
        default_factory=dict,
        examples=[
            {
                "thinking_high": {
                    "type": "anthropic",
                    "model": "claude-sonnet-4-5",
                    "max_thinking_tokens": 10000,
                },
                "fast_gpt": {
                    "type": "string",
                    "model": "openai:gpt-4o-mini",
                    "temperature": 0.3,
                },
            }
        ],
    )
    """Named model variants with pre-configured settings.

    Define reusable model configurations that can be referenced by name
    in agent configs. Each variant specifies a base model and its settings.

    Note: Currently only applies to native agents.

    Example:
        ```yaml
        model_variants:
          thinking_high:
            type: anthropic
            model: claude-sonnet-4-5
            max_thinking_tokens: 10000

          fast_gpt:
            type: string
            model: openai:gpt-4o-mini
            temperature: 0.3
        ```

    Then use in agents:
        ```yaml
        agents:
          assistant:
            model: thinking_high  # References the variant
        ```
    """

    jobs: dict[str, Job[Any]] = Field(default_factory=dict)
    """Pre-defined jobs, ready to be used by nodes."""

    mcp_servers: list[str | MCPServerConfig] = Field(
        default_factory=list,
        examples=[
            ["uvx some-server"],
            [{"type": "streamable-http", "url": "http://mcp.example.com"}],
        ],
        json_schema_extra={
            "documentation_url": "https://phil65.github.io/agentpool/YAML%20Configuration/mcp_configuration/"
        },
    )
    """List of MCP server configurations:

    These MCP servers are used to provide tools and other resources to the nodes.

    Docs: https://phil65.github.io/agentpool/YAML%20Configuration/mcp_configuration/
    """
    pool_server: MCPPoolServerConfig = Field(default_factory=MCPPoolServerConfig)
    """Pool server configuration.

    This MCP server configuration is used for the pool MCP server,
    which exposes pool functionality to other applications / clients."""

    prompts: PromptLibraryConfig = Field(
        default_factory=PromptLibraryConfig,
        json_schema_extra={
            "documentation_url": "https://phil65.github.io/agentpool/YAML%20Configuration/prompt_configuration/"
        },
    )
    """Prompt library configuration.

    This configuration defines the prompt library, which is used to provide prompts to the nodes.

    Docs: https://phil65.github.io/agentpool/YAML%20Configuration/prompt_configuration/
    """

    commands: dict[str, CommandConfig | str] = Field(
        default_factory=dict,
        examples=[
            {"check_disk": "df -h", "analyze": "Analyze the current situation"},
            {
                "status": {
                    "type": "static",
                    "content": "Show system status",
                }
            },
        ],
    )
    """Global command shortcuts for prompt injection.

    Supports both shorthand string syntax and full command configurations:
        commands:
          df: "check disk space"  # shorthand -> StaticCommandConfig
          analyze:  # full config
            type: file
            path: "./prompts/analysis.md"
    """

    compaction: CompactionConfig | None = None
    """Compaction configuration for message history management.

    Controls how conversation history is compacted/summarized to manage context size.
    Can use a preset or define custom steps:
        compaction:
          preset: balanced  # or: minimal, summarizing

    Or custom steps:
        compaction:
          steps:
            - type: filter_thinking
            - type: summarize
              model: openai:gpt-4o-mini
              threshold: 15
    """

    config_file_path: str | None = Field(default=None, exclude=True)
    """Path to the configuration file this manifest was loaded from.

    Set automatically by `from_file()`. Used for resolving relative paths.
    Excluded from serialization.
    """

    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "x-icon": "octicon:file-code-16",
            "x-doc-title": "Manifest Overview",
            "documentation_url": "https://phil65.github.io/agentpool/YAML%20Configuration/manifest_configuration/",
            "patternProperties": {
                # Allow YAML anchors (dot prefix)
                r"^\.": {
                    "description": "YAML anchor or hidden field",
                },
                # Allow internal metadata (underscore prefix)
                r"^_": {
                    "description": "Internal metadata field",
                },
                # Allow custom extensions (x- prefix)
                r"^x-": {
                    "description": "Custom extension field",
                },
            },
        },
    )

    @model_validator(mode="before")
    @classmethod
    def set_default_agent_type(cls, data: dict[str, Any]) -> dict[str, Any]:
        """Set default type='native' for agents without a type field."""
        agents = data.get("agents", {})
        for config in agents.values():
            if isinstance(config, dict) and "type" not in config:
                config["type"] = "native"
        return data

    def resolve_model(self, model: AnyModelConfig | str) -> AnyModelConfig:
        """Resolve a model specification to a model config.

        If model is a string, checks model_variants first, then wraps in StringModelConfig.
        If model is already an AnyModelConfig, returns it unchanged.

        Args:
            model: Model identifier, variant name, or config

        Returns:
            AnyModelConfig
        """
        if isinstance(model, str):
            if model in self.model_variants:
                return self.model_variants[model]
            return StringModelConfig(identifier=model)
        # Already a config
        return model

    def clone_agent_config(
        self,
        name: str,
        new_name: str | None = None,
        *,
        template_context: dict[str, Any] | None = None,
        **overrides: Any,
    ) -> str:
        """Create a copy of an agent configuration.

        Args:
            name: Name of agent to clone
            new_name: Optional new name (auto-generated if None)
            template_context: Variables for template rendering
            **overrides: Configuration overrides for the clone

        Returns:
            Name of the new agent

        Raises:
            KeyError: If original agent not found
            ValueError: If new name already exists or if overrides invalid
        """
        if name not in self.agents:
            raise KeyError(f"Agent {name} not found")

        actual_name = new_name or f"{name}_copy_{len(self.agents)}"
        if actual_name in self.agents:
            raise ValueError(f"Agent {actual_name} already exists")

        config = self.agents[name].model_copy(deep=True)
        for key, value in overrides.items():
            if not hasattr(config, key):
                raise ValueError(f"Invalid override: {key}")
            setattr(config, key, value)

        # Handle template rendering if context provided
        if template_context and "name" in template_context and "name" not in overrides:
            config.model_copy(update={"name": template_context["name"]})

        # Note: system_prompts will be rendered during agent creation, not here
        # config.system_prompts remains as PromptConfig objects
        self.agents[actual_name] = config
        return actual_name

    @cached_property
    def _loaded_file_agents(self) -> dict[str, NativeAgentConfig]:
        """Load and cache file-based agent configurations.

        Parses markdown files in Claude Code, OpenCode, or AgentPool format
        and converts them to NativeAgentConfig. Results are cached.
        """
        from agentpool.models.file_parsing import parse_file_agent_reference

        loaded: dict[str, NativeAgentConfig] = {}
        for name, reference in self.file_agents.items():
            try:
                config = parse_file_agent_reference(reference)
                # Ensure name is set from the key
                if config.name is None:
                    config = config.model_copy(update={"name": name})
                loaded[name] = config
            except Exception as e:
                path = reference if isinstance(reference, str) else reference.path
                logger.exception("Failed to load file agent %r from %s", name, path)

                raise ValueError(f"Failed to load file agent {name!r} from {path}: {e}") from e
        return loaded

    @property
    def node_names(self) -> list[str]:
        """Get list of all agent and team names."""
        return list(self.agents.keys()) + list(self.file_agents.keys()) + list(self.teams.keys())

    @property
    def nodes(self) -> dict[str, NodeConfig]:
        """Get all agent and team configurations."""
        return {**self.agents, **self._loaded_file_agents, **self.teams}

    @property
    def acp_agents(self) -> dict[str, BaseACPAgentConfig]:
        """Get ACP agents filtered from unified agents dict."""
        from agentpool.models.acp_agents import BaseACPAgentConfig

        return {k: v for k, v in self.agents.items() if isinstance(v, BaseACPAgentConfig)}

    @property
    def agui_agents(self) -> dict[str, AGUIAgentConfig]:
        """Get AG-UI agents filtered from unified agents dict."""
        return {k: v for k, v in self.agents.items() if isinstance(v, AGUIAgentConfig)}

    @property
    def claude_code_agents(self) -> dict[str, ClaudeCodeAgentConfig]:
        """Get Claude Code agents filtered from unified agents dict."""
        return {k: v for k, v in self.agents.items() if isinstance(v, ClaudeCodeAgentConfig)}

    @property
    def native_agents(self) -> dict[str, NativeAgentConfig]:
        """Get native agents filtered from unified agents dict."""
        return {k: v for k, v in self.agents.items() if isinstance(v, NativeAgentConfig)}

    def get_mcp_servers(self) -> list[MCPServerConfig]:
        """Get processed MCP server configurations.

        Converts string entries to appropriate MCP server configs based on heuristics:
        - URLs ending with "/sse" -> SSE server
        - URLs starting with http(s):// -> HTTP server
        - Everything else -> stdio command

        Returns:
            List of MCPServerConfig instances

        Raises:
            ValueError: If string entry is empty
        """
        return [
            BaseMCPServerConfig.from_string(cfg) if isinstance(cfg, str) else cfg
            for cfg in self.mcp_servers
        ]

    def get_command_configs(self) -> dict[str, CommandConfig]:
        """Get processed command configurations.

        Converts string entries to StaticCommandConfig instances.

        Returns:
            Dict mapping command names to CommandConfig instances
        """
        result: dict[str, CommandConfig] = {}
        for name, config in self.commands.items():
            if isinstance(config, str):
                result[name] = StaticCommandConfig(name=name, content=config)
            else:
                if config.name is None:  # Set name if not provided
                    config.name = name
                result[name] = config
        return result

    def get_compaction_pipeline(self) -> CompactionPipeline | None:
        """Get the configured compaction pipeline, if any.

        Returns:
            CompactionPipeline instance or None if not configured
        """
        if self.compaction is None:
            return None
        return self.compaction.build()

    # @model_validator(mode="after")
    # def validate_response_types(self) -> AgentsManifest:
    #     """Ensure all agent output_types exist in responses or are inline."""
    #     for agent_id, agent in self.agents.items():
    #         if (
    #             isinstance(agent.output_type, str)
    #             and agent.output_type not in self.responses
    #         ):
    #
    #             raise ValueError(f"'{agent.output_type=}' for '{agent_id=}' not found")
    #     return self

    @classmethod
    def from_file(cls, path: JoinablePathLike) -> Self:
        """Load agent configuration from YAML file.

        Args:
            path: Path to the configuration file

        Returns:
            Loaded agent definition

        Raises:
            ValueError: If loading fails
        """
        import yamling

        try:
            data = yamling.load_yaml_file(path, resolve_inherit=True)
            agent_def = cls.model_validate(data)
            path_str = str(path)

            def update_with_path(nodes: dict[str, Any]) -> dict[str, Any]:
                return {
                    name: config.model_copy(update={"config_file_path": path_str})
                    for name, config in nodes.items()
                }

            return agent_def.model_copy(
                update={
                    "config_file_path": path_str,
                    "agents": update_with_path(agent_def.agents),
                    "teams": update_with_path(agent_def.teams),
                }
            )
        except Exception as exc:
            raise ValueError(f"Failed to load agent config from {path}") from exc

    @classmethod
    def from_resolved(
        cls,
        explicit_path: JoinablePathLike | None = None,
        *,
        fallback_config: JoinablePathLike | None = None,
        project_dir: JoinablePathLike | None = None,
        include_global: bool = True,
        include_project: bool = True,
    ) -> Self:
        """Load agent configuration with layered inheritance.

        Resolves configuration from multiple sources in precedence order:
        1. Global config (~/.config/agentpool/agentpool.yml)
        2. Custom config (AGENTPOOL_CONFIG env var)
        3. Project config (agentpool.yml in project/git root)
        4. Explicit config (highest precedence)

        The fallback_config is only used if NO other config defines any agents.

        Args:
            explicit_path: Explicit config path (highest precedence)
            fallback_config: Fallback config used ONLY if no agents defined elsewhere
            project_dir: Directory to search for project config (defaults to cwd)
            include_global: Whether to include global user config
            include_project: Whether to include project config

        Returns:
            Loaded and merged agent definition

        Raises:
            ValueError: If explicit_path is provided but cannot be loaded,
                       or if merged config is invalid
        """
        from agentpool_config.resolution import resolve_config

        resolved = resolve_config(
            explicit_path=explicit_path,
            fallback_config=fallback_config,
            project_dir=project_dir,
            include_global=include_global,
            include_project=include_project,
        )

        try:
            agent_def = cls.model_validate(resolved.data)
            path_str = resolved.primary_path

            def update_with_path(nodes: dict[str, Any]) -> dict[str, Any]:
                if path_str is None:
                    return dict(nodes)
                return {
                    name: config.model_copy(update={"config_file_path": path_str})
                    for name, config in nodes.items()
                }

            return agent_def.model_copy(
                update={
                    "config_file_path": path_str,
                    "agents": update_with_path(agent_def.agents),
                    "teams": update_with_path(agent_def.teams),
                }
            )
        except Exception as exc:
            sources = ", ".join(resolved.source_paths) or "no sources"
            raise ValueError(f"Failed to load merged config from {sources}") from exc

    def get_output_type(self, agent_name: str) -> type[Any] | None:
        """Get the resolved result type for an agent.

        Returns None if no result type is configured or agent doesn't support output_type.
        """
        agent_config = self.agents[agent_name]
        # Only NativeAgentConfig and ClaudeCodeAgentConfig have output_type
        if not isinstance(agent_config, NativeAgentConfig | ClaudeCodeAgentConfig):
            return None
        if not agent_config.output_type:
            return None
        logger.debug("Building response model", type=agent_config.output_type)
        if isinstance(agent_config.output_type, str):
            response_def = self.responses[agent_config.output_type]
            return response_def.response_schema.get_schema()
        return agent_config.output_type.response_schema.get_schema()

    @model_validator(mode="after")
    def validate_extra_fields(self) -> Self:
        """Validate and warn about unknown extra fields.

        Allowed prefixes:
        - `.` (dot): YAML anchors
        - `_` (underscore): Internal metadata
        - `x-` (x-prefix): Custom extensions

        Unknown fields trigger a WARNING but do not raise ValidationError.
        """
        if hasattr(self, "model_extra") and self.model_extra:
            for key in self.model_extra:
                # Check if key starts with allowed prefixes
                if key.startswith((".", "_", "x-")):
                    continue  # Silently allow these

                # Warn about unknown fields
                logger.warning(
                    "Unknown field '%s' in manifest. This field will be IGNORED.",
                    key,
                    stacklevel=2,
                )
        return self


if __name__ == "__main__":
    from llmling_models_config import InputModelConfig

    model = InputModelConfig()
    agent_cfg = NativeAgentConfig(name="test_agent", model=model)
    manifest = AgentsManifest(agents=dict(test_agent=agent_cfg))
    print(AgentsManifest.generate_test_data(mode="maximal").model_dump_yaml())
