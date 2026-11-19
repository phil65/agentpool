"""Models for toolsets."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Annotated, Any, Literal, assert_never, cast

from exxec_config import ExecutionEnvironmentConfig
from llmling_models_config import AnyModelConfig
from pydantic import ConfigDict, EmailStr, Field, HttpUrl, SecretStr
from schemez import Schema
from searchly_config import (
    NewsSearchProviderConfig,
    NewsSearchProviderName,
    WebSearchProviderConfig,
    WebSearchProviderName,
    get_config_class,
)
from tokonomics.model_names import ModelId
from upathtools import UPath, core
from upathtools_config import FilesystemConfigType
from upathtools_config.base import FileSystemConfig

from agentpool_config.converters import ConversionConfig
from agentpool_config.tools import ImportToolConfig
from agentpool_config.workers import AgentWorkerConfig, WorkerConfig


if TYPE_CHECKING:
    from agentpool.resource_providers import ResourceProvider
    from agentpool_toolsets.search_toolset import SearchTools


MarkupType = Literal["yaml", "json", "toml"]
# Tool name literals for statically-defined toolsets
SubagentToolName = Literal[
    "list_available_nodes",
    "task",
]
ExecutionEnvironmentToolName = Literal[
    "execute_code",
    "bash",
    "start_process",
    "get_process_output",
    "wait_for_process",
    "kill_process",
    "release_process",
    "list_processes",
]

SkillsToolName = Literal["load_skill", "list_skills", "read_skill_file"]
CodeToolName = Literal["format_code", "ast_grep"]
PlanToolName = Literal["get_plan", "add_plan_entry", "update_plan_entry", "remove_plan_entry"]
PlanToolMode = Literal["granular", "declarative"]


class BaseToolsetConfig(Schema):
    """Base configuration for toolsets."""

    model_config = ConfigDict(
        json_schema_extra={
            "x-icon": "octicon:package-16",
            "x-doc-title": "Toolset Configuration",
        }
    )

    namespace: str | None = Field(default=None, examples=["web", "files"], title="Tool namespace")
    """Optional namespace prefix for tool names"""

    def get_provider(self) -> ResourceProvider:
        """Implemented by subclasses, should return a ResourceProvider instance."""
        raise NotImplementedError("Subclasses must implement this method")


class OpenAPIToolsetConfig(BaseToolsetConfig):
    """Configuration for OpenAPI toolsets."""

    model_config = ConfigDict(
        json_schema_extra={
            "x-icon": "octicon:globe-16",
            "x-doc-title": "OpenAPI Toolset",
        }
    )

    type: Literal["openapi"] = Field("openapi", init=False)
    """OpenAPI toolset."""

    spec: UPath = Field(
        examples=["https://api.example.com/openapi.json", "/path/to/spec.yaml"],
        title="OpenAPI specification",
    )
    """URL or path to the OpenAPI specification document."""

    base_url: HttpUrl | None = Field(
        default=None,
        examples=["https://api.example.com", "http://localhost:8080"],
        title="Base URL override",
    )
    """Optional base URL for API requests, overrides the one in spec."""

    def get_provider(self) -> ResourceProvider:
        """Create OpenAPI tools provider from this config."""
        from agentpool_toolsets.openapi import OpenAPITools

        base_url = str(self.base_url) if self.base_url else ""
        return OpenAPITools(spec=self.spec, base_url=base_url)


class EntryPointToolsetConfig(BaseToolsetConfig):
    """Configuration for entry point toolsets."""

    model_config = ConfigDict(
        json_schema_extra={
            "x-icon": "octicon:plug-16",
            "x-doc-title": "Entry Point Toolset",
        }
    )

    type: Literal["entry_points"] = Field("entry_points", init=False)
    """Entry point toolset."""

    module: str = Field(
        examples=["myapp.tools", "external_package.plugins"],
        title="Module path",
    )
    """Python module path to load tools from via entry points."""

    def get_provider(self) -> ResourceProvider:
        """Create provider from this config."""
        from agentpool_toolsets.entry_points import EntryPointTools

        return EntryPointTools(module=self.module)


class ComposioToolSetConfig(BaseToolsetConfig):
    """Configuration for Composio toolsets."""

    model_config = ConfigDict(
        json_schema_extra={
            "x-icon": "octicon:apps-16",
            "x-doc-title": "Composio Toolset",
        }
    )

    type: Literal["composio"] = Field("composio", init=False)
    """Composio Toolsets."""

    api_key: SecretStr | None = Field(default=None, title="Composio API key")
    """Composio API Key."""

    user_id: EmailStr = Field(
        default="user@example.com",
        examples=["user@example.com", "admin@company.com"],
        title="User ID",
    )
    """User ID for composio tools."""

    toolsets: list[str] = Field(
        default_factory=list,
        examples=[["github", "slack"], ["gmail", "calendar"]],
        title="Toolset list",
    )
    """List of toolsets to load."""

    def get_provider(self) -> ResourceProvider:
        """Create provider from this config."""
        from agentpool_toolsets.composio_toolset import ComposioTools

        key = self.api_key.get_secret_value() if self.api_key else os.getenv("COMPOSIO_API_KEY")
        return ComposioTools(user_id=self.user_id, toolsets=self.toolsets, api_key=key)


class SubagentToolsetConfig(BaseToolsetConfig):
    """Configuration for subagent interaction tools."""

    model_config = ConfigDict(
        json_schema_extra={
            "x-icon": "octicon:share-16",
            "x-doc-title": "Subagent Toolset",
        }
    )

    type: Literal["subagent"] = Field("subagent", init=False)
    """Subagent interaction toolset (task, list_available_nodes)."""

    tools: dict[SubagentToolName, bool] | None = Field(
        default=None,
        title="Tool filter",
    )
    """Optional tool filter to enable/disable specific tools."""

    batch_stream_deltas: bool = Field(
        default=False,
        title="Batch stream deltas",
    )
    """Batch consecutive text/thinking deltas for fewer UI updates."""

    def get_provider(self) -> ResourceProvider:
        """Create subagent tools provider."""
        from agentpool_toolsets.builtin.subagent_tools import SubagentTools

        provider = SubagentTools(
            name="subagent_tools",
            batch_stream_deltas=self.batch_stream_deltas,
        )
        if self.tools is not None:
            from agentpool.resource_providers import FilteringResourceProvider

            return FilteringResourceProvider(provider, cast(dict[str, bool], self.tools))
        return provider


class WorkersToolsetConfig(BaseToolsetConfig):
    """Configuration for worker agent tools.

    Workers are agents or teams registered as tools for the parent agent.
    This provides a predefined set of worker tools based on configuration.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "x-icon": "octicon:people-16",
            "x-doc-title": "Workers Toolset",
        }
    )

    type: Literal["workers"] = Field("workers", init=False)
    """Workers toolset (predefined agent/team tools)."""

    workers: list[WorkerConfig | str] = Field(default_factory=list, title="Worker configurations")
    """List of workers to register as tools. Can be config objects or plain agent names."""

    def get_workers(self) -> list[WorkerConfig]:
        """Resolve workers list, converting plain strings to AgentWorkerConfig."""
        resolved: list[WorkerConfig] = []
        for worker in self.workers:
            if isinstance(worker, str):
                resolved.append(AgentWorkerConfig(name=worker))
            else:
                resolved.append(worker)
        return resolved

    def get_provider(self) -> ResourceProvider:
        """Create workers tools provider."""
        from agentpool_toolsets.builtin.workers import WorkersTools

        return WorkersTools(workers=self.get_workers(), name="workers")


class ProcessManagementToolsetConfig(BaseToolsetConfig):
    """Configuration for process management toolset (code + process management)."""

    model_config = ConfigDict(
        json_schema_extra={
            "x-icon": "octicon:terminal-16",
            "x-doc-title": "Process management Toolset",
        }
    )

    type: Literal["process_management"] = Field("process_management", init=False)
    """Process management toolset."""

    environment: ExecutionEnvironmentConfig | None = Field(
        default=None,
        title="Process management",
    )
    """Optional Process management configuration (defaults to local)."""

    tools: dict[ExecutionEnvironmentToolName, bool] | None = Field(
        default=None,
        title="Tool filter",
    )
    """Optional tool filter to enable/disable specific tools."""

    def get_provider(self) -> ResourceProvider:
        """Create Process management tools provider."""
        from agentpool_toolsets.builtin import ProcessManagementTools

        env = self.environment.get_provider() if self.environment else None
        provider = ProcessManagementTools(env=env, name="process_management")
        if self.tools is not None:
            from agentpool.resource_providers import FilteringResourceProvider

            return FilteringResourceProvider(provider, cast(dict[str, bool], self.tools))
        return provider


class SkillsToolsetConfig(BaseToolsetConfig):
    """Configuration for skills toolset.

    Provides tools to list skills, load their instructions, and read their
    bundled files. Skills come from the pool's skills registry, discovered from
    configured directories (e.g., ~/.claude/skills/, .agents/skills/), and from
    the agent's MCP servers that publish skills through the MCP Skills extension.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "x-icon": "octicon:mortar-board-16",
            "x-doc-title": "Skills Toolset",
        }
    )

    type: Literal["skills"] = Field("skills", init=False)
    """Skills toolset."""

    tools: dict[SkillsToolName, bool] | None = Field(
        default=None,
        title="Tool filter",
    )
    """Optional tool filter to enable/disable specific tools."""

    def get_provider(self) -> ResourceProvider:
        """Create skills tools provider."""
        from agentpool_toolsets.builtin import SkillsTools

        provider = SkillsTools(name="skills")
        if self.tools is not None:
            from agentpool.resource_providers import FilteringResourceProvider

            return FilteringResourceProvider(provider, cast(dict[str, bool], self.tools))
        return provider


class CodeToolsetConfig(BaseToolsetConfig):
    """Configuration for code toolset."""

    model_config = ConfigDict(
        json_schema_extra={
            "x-icon": "octicon:code-16",
            "x-doc-title": "Code Toolset",
        }
    )

    type: Literal["code"] = Field("code", init=False)
    """Code toolset."""

    tools: dict[CodeToolName, bool] | None = Field(
        default=None,
        title="Tool filter",
    )
    """Optional tool filter to enable/disable specific tools."""

    environment: ExecutionEnvironmentConfig | None = Field(
        default=None,
        title="Execution environment",
    )
    """Optional execution environment. If None, falls back to agent's env at runtime."""

    def get_provider(self) -> ResourceProvider:
        """Create code tools provider."""
        from agentpool_toolsets.builtin.code import CodeTools

        env = self.environment.get_provider() if self.environment else None
        provider = CodeTools(env=env, name="code")
        if self.tools is not None:
            from agentpool.resource_providers import FilteringResourceProvider

            return FilteringResourceProvider(provider, cast(dict[str, bool], self.tools))
        return provider


class FSSpecToolsetConfig(BaseToolsetConfig):
    """Configuration for file access toolset (supports local and remote filesystems)."""

    model_config = ConfigDict(
        json_schema_extra={
            "x-icon": "octicon:file-directory-16",
            "x-doc-title": "File Access Toolset",
        }
    )

    type: Literal["file_access"] = Field("file_access", init=False)
    """File access toolset."""

    fs: str | FilesystemConfigType | None = Field(
        default=None,
        examples=[
            "file:///",
            "s3://my-bucket",
            {"type": "github", "org": "sveltejs", "repo": "svelte"},
            {
                "type": "union",
                "filesystems": {"docs": {"type": "github", "org": "org", "repo": "repo"}},
            },
        ],
        title="Filesystem",
    )
    """Filesystem URI string or configuration object. If None, use agent default FS.

    Supports:
    - URI strings: "file:///", "s3://bucket", "github://org/repo"
    - Full configs: {"type": "github", "org": "...", "repo": "..."}
    - Composed filesystems: {"type": "union", "filesystems": {...}}
    """

    model: str | ModelId | AnyModelConfig | None = Field(
        default=None,
        examples=["openai:gpt-5-nano"],
        title="Model for edit sub-agent",
    )

    storage_options: dict[str, str] = Field(
        default_factory=dict,
        examples=[
            {"region": "us-east-1", "profile": "default"},
            {"token": "ghp_123456789", "timeout": "30"},
        ],
        title="Storage options",
    )
    """Additional options for URI-based filesystems (ignored when using config object)."""

    conversion: ConversionConfig | None = Field(default=None, title="Conversion config")
    """Optional conversion configuration for markdown conversion."""

    max_file_size_kb: int = Field(
        default=64,
        ge=1,
        le=10240,
        title="Maximum file size",
    )
    """Maximum file size in kilobytes for read/write operations (default: 64KB)."""

    max_grep_output_kb: int = Field(
        default=64,
        ge=1,
        le=10240,
        title="Maximum grep output size",
    )
    """Maximum grep output size in kilobytes (default: 64KB)."""

    use_subprocess_grep: bool = Field(
        default=True,
        title="Use subprocess grep",
    )
    """Use ripgrep/grep subprocess if available (faster than Python regex)."""

    enable_diagnostics: bool = Field(
        default=False,
        title="Enable diagnostics",
    )
    """Run LSP CLI diagnostics (type checking, linting) after file writes."""

    large_file_tokens: int = Field(
        default=12_000,
        ge=1000,
        le=100_000,
        title="Large file threshold",
    )
    """Token threshold for switching to structure map instead of full content."""

    map_max_tokens: int = Field(
        default=2048,
        ge=256,
        le=16_000,
        title="Structure map max tokens",
    )
    """Maximum tokens for structure map output when reading large files."""

    edit_tool: Literal["simple", "batch", "agentic"] = Field(
        default="simple",
        title="Edit tool variant",
    )
    """Which edit tool to expose: "simple" (single replacement),
    "batch" (multiple replacements), or "agentic" (LLM-driven editing)."""

    max_image_size: int | None = Field(
        default=2000,
        ge=100,
        le=8192,
        title="Maximum image dimension",
    )
    """Max width/height for images in pixels. Larger images are auto-resized
    for better model compatibility. Set to None to disable resizing."""

    max_image_bytes: int | None = Field(
        default=None,
        ge=102400,
        le=20971520,
        title="Maximum image file size",
    )
    """Max file size for images in bytes. Images exceeding this are compressed
    using progressive quality/dimension reduction. Default: 4.5MB (Anthropic limit).
    Set to None to use the default 4.5MB limit."""

    def get_provider(self) -> ResourceProvider:
        """Create FSSpec filesystem tools provider."""
        from agentpool.prompts.conversion_manager import ConversionManager
        from agentpool_toolsets.fsspec_toolset import FSSpecTools

        model = (
            self.model
            if isinstance(self.model, str) or self.model is None
            else self.model.get_model()
        )
        match self.fs:
            case None:
                fs = None
            case str():
                # URI string - use fsspec directly
                fs, _url_path = core.url_to_fs(self.fs, **self.storage_options)
            case FileSystemConfig():
                # Full config object - use create_fs()
                fs = self.fs.create_fs()
            case _ as unreachable:
                assert_never(unreachable)
        converter = ConversionManager(self.conversion) if self.conversion else None
        return FSSpecTools(
            fs,
            converter=converter,
            edit_model=model,
            max_file_size_kb=self.max_file_size_kb,
            max_grep_output_kb=self.max_grep_output_kb,
            use_subprocess_grep=self.use_subprocess_grep,
            enable_diagnostics=self.enable_diagnostics,
            large_file_tokens=self.large_file_tokens,
            map_max_tokens=self.map_max_tokens,
            edit_tool=self.edit_tool,
            max_image_size=self.max_image_size,
            max_image_bytes=self.max_image_bytes,
        )


class VFSToolsetConfig(BaseToolsetConfig):
    """Configuration for VFS registry filesystem toolset."""

    model_config = ConfigDict(
        json_schema_extra={
            "x-icon": "octicon:file-symlink-directory-16",
            "x-doc-title": "VFS Toolset",
        }
    )

    type: Literal["vfs"] = Field("vfs", init=False)
    """VFS registry filesystem toolset."""

    def get_provider(self) -> ResourceProvider:
        """Create VFS registry filesystem tools provider."""
        from agentpool_toolsets.vfs_toolset import VFSTools

        return VFSTools(name="vfs")


class SearchToolsetConfig(BaseToolsetConfig):
    """Configuration for web/news search toolset."""

    model_config = ConfigDict(
        json_schema_extra={
            "x-icon": "octicon:search-16",
            "x-doc-title": "Search Toolset",
        }
    )

    type: Literal["search"] = Field("search", init=False)
    """Search toolset."""

    web_search: WebSearchProviderConfig | WebSearchProviderName | None = Field(
        default=None, title="Web search"
    )
    """Web search provider configuration."""

    news_search: NewsSearchProviderConfig | NewsSearchProviderName | None = Field(
        default=None, title="News search"
    )
    """News search provider configuration."""

    def get_provider(self) -> SearchTools:
        """Create search tools provider."""
        from searchly import BaseSearchProviderConfig, NewsSearchProvider, WebSearchProvider

        from agentpool_toolsets.search_toolset import SearchTools

        match self.web_search:
            case str():
                kls = get_config_class(self.web_search)
                web: WebSearchProvider | NewsSearchProvider | None = kls().get_provider()  # type: ignore[call-arg]
            case BaseSearchProviderConfig():
                web = self.web_search.get_provider()
            case None:
                web = None
        match self.news_search:
            case str():
                kls = get_config_class(self.news_search)
                news: WebSearchProvider | NewsSearchProvider | None = kls().get_provider()  # type: ignore[call-arg]
            case BaseSearchProviderConfig():
                news = self.news_search.get_provider()
            case None:
                news = None
        assert isinstance(web, WebSearchProvider) or web is None
        assert isinstance(news, NewsSearchProvider) or news is None
        return SearchTools(web_search=web, news_search=news)


class NotificationsToolsetConfig(BaseToolsetConfig):
    """Configuration for Apprise-based notifications toolset."""

    model_config = ConfigDict(
        json_schema_extra={
            "x-icon": "octicon:bell-16",
            "x-doc-title": "Notifications Toolset",
        }
    )

    type: Literal["notifications"] = Field("notifications", init=False)
    """Notifications toolset."""

    channels: dict[str, str | list[str]] = Field(
        default_factory=dict,
        examples=[
            {
                "team_slack": "slack://TokenA/TokenB/TokenC/",
                "personal": "tgram://bottoken/ChatID",
                "ops_alerts": ["slack://ops/", "mailto://ops@company.com"],
            }
        ],
        title="Notification channels",
    )
    """Named notification channels. Values can be a single Apprise URL or list of URLs."""

    def get_provider(self) -> ResourceProvider:
        """Create notifications tools provider."""
        from agentpool_toolsets.notifications import NotificationsTools

        return NotificationsTools(channels=self.channels)


class CustomToolsetConfig(BaseToolsetConfig):
    """Configuration for custom toolsets."""

    model_config = ConfigDict(
        json_schema_extra={
            "x-icon": "octicon:gear-16",
            "x-doc-title": "Custom Toolset",
        }
    )

    type: Literal["custom"] = Field("custom", init=False)
    """Custom toolset."""

    import_path: str = Field(
        examples=["myapp.toolsets.CustomTools", "external.providers:MyProvider"],
        title="Import path",
    )
    """Dotted import path to the custom toolset implementation class."""

    kw_args: dict[str, Any] = Field(default_factory=dict, title="Provider parameters")
    """Additional parameters to pass to provider constructor.

    These are unpacked as keyword arguments. If "name" is present, it will
    be used to override the default provider name.
    """

    def get_provider(self) -> ResourceProvider:
        """Create custom provider from import path."""
        from agentpool.resource_providers import ResourceProvider
        from agentpool.utils.importing import import_class

        provider_cls = import_class(self.import_path)
        if not issubclass(provider_cls, ResourceProvider):
            raise ValueError(f"{self.import_path} must be a ResourceProvider subclass")  # noqa: TRY004
        kwargs = self.kw_args.copy()
        name = kwargs.pop("name", provider_cls.__name__)
        try:
            return provider_cls(name=name, **kwargs)
        except TypeError as e:
            # Provide a more helpful error message about parameter mismatch
            raise TypeError(
                f"Failed to initialize provider '{self.import_path}' with params: {self.kw_args}\n"
                f"Original error: {e}"
            ) from e


class AggregatingToolsetConfig(BaseToolsetConfig):
    """Configuration for aggregating multiple toolsets."""

    model_config = ConfigDict(
        json_schema_extra={
            "x-icon": "octicon:package-16",
            "x-doc-title": "Aggregating Toolset",
        }
    )

    type: Literal["aggregating"] = Field("aggregating", init=False)
    """Aggregating toolset."""

    toolsets: list[ToolsetConfig] = Field(title="Toolsets to aggregate")
    """List of toolsets to aggregate."""

    tool_mode: Literal["codemode"] | None = Field(None, title="Tool execution mode")
    """Optional tool mode. Set to 'codemode' to wrap all tools in Python execution."""

    def get_provider(self) -> ResourceProvider:
        """Create aggregating provider."""
        from agentpool.resource_providers import AggregatingResourceProvider

        providers = [p.get_provider() for p in self.toolsets]
        return AggregatingResourceProvider(
            providers=providers, name="aggregating", tool_mode=self.tool_mode
        )


class CodeModeToolsetConfig(BaseToolsetConfig):
    """Configuration for code mode tools.

    DEPRECATED: Use AggregatingToolsetConfig with tool_mode='codemode' instead.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "x-icon": "octicon:code-square-16",
            "x-doc-title": "Code Mode Toolset (Deprecated)",
        }
    )

    type: Literal["code_mode"] = Field("code_mode", init=False)
    """Code mode toolset."""

    toolsets: list[ToolsetConfig] = Field(title="Wrapped toolsets")
    """List of toolsets to expose as a codemode toolset."""

    def get_provider(self) -> ResourceProvider:
        """Create Codemode toolset.

        NOTE: This now delegates to AggregatingResourceProvider with tool_mode='codemode'.
        """
        from agentpool.resource_providers import AggregatingResourceProvider

        providers = [p.get_provider() for p in self.toolsets]
        return AggregatingResourceProvider(
            providers=providers, name="codemode", tool_mode="codemode"
        )


class RemoteCodeModeToolsetConfig(BaseToolsetConfig):
    """Configuration for code mode tools."""

    model_config = ConfigDict(
        json_schema_extra={
            "x-icon": "octicon:cloud-offline-16",
            "x-doc-title": "Remote Code Mode Toolset",
        }
    )

    type: Literal["remote_code_mode"] = Field("remote_code_mode", init=False)
    """Code mode toolset."""

    environment: ExecutionEnvironmentConfig = Field(title="Execution environment")
    """Execution environment configuration."""

    toolsets: list[ToolsetConfig] = Field(title="Wrapped toolsets")
    """List of toolsets to expose as a codemode toolset."""

    def get_provider(self) -> ResourceProvider:
        """Create Codemode toolset."""
        from agentpool.resource_providers.codemode import RemoteCodeModeResourceProvider

        providers = [p.get_provider() for p in self.toolsets]
        return RemoteCodeModeResourceProvider(
            providers=providers,
            execution_config=self.environment,
        )


class ImportToolsToolsetConfig(BaseToolsetConfig):
    """Configuration for importing individual functions as tools.

    Allows adding arbitrary Python callables as agent tools via import paths.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "x-icon": "octicon:package-dependencies-16",
            "x-doc-title": "Import Tools Toolset",
        }
    )

    type: Literal["import_tools"] = Field("import_tools", init=False)
    """Import tools toolset."""

    tools: list[ImportToolConfig] = Field(
        title="Tools to import",
        examples=[
            [
                {"import_path": "os.listdir", "name": "list_files"},
                {"import_path": "webbrowser.open", "description": "Open URL in browser"},
            ]
        ],
    )
    """List of tool configurations to import."""

    def get_provider(self) -> ResourceProvider:
        """Create static provider with imported tools."""
        from agentpool.resource_providers import StaticResourceProvider

        tools = [tool_config.get_tool() for tool_config in self.tools]
        name = self.namespace or "import_tools"
        return StaticResourceProvider(name=name, tools=tools)


class ConfigCreationToolsetConfig(BaseToolsetConfig):
    """Configuration for config creation with schema validation."""

    model_config = ConfigDict(
        json_schema_extra={
            "x-icon": "octicon:file-code-16",
            "x-doc-title": "Config Creation Toolset",
        }
    )

    type: Literal["config_creation"] = Field("config_creation", init=False)
    """Config creation toolset."""

    schema_path: UPath = Field(
        examples=["schema/config-schema.json", "https://example.com/schema.json"],
        title="JSON Schema path",
    )
    """Path or URL to the JSON schema for validation."""

    markup: MarkupType = Field(default="yaml", title="Markup language")
    """Markup language for the configuration (yaml, json, toml)."""

    def get_provider(self) -> ResourceProvider:
        """Create config creation toolset."""
        from agentpool_toolsets.config_creation import ConfigCreationTools

        name = self.namespace or "config_creation"
        return ConfigCreationTools(schema_path=self.schema_path, markup=self.markup, name=name)


class PlanToolsetConfig(BaseToolsetConfig):
    """Configuration for plan management toolset.

    Provides tools for managing agent execution plans and task tracking.
    Agents can create, update, and track progress on plan entries.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "x-icon": "octicon:tasklist-16",
            "x-doc-title": "Plan Toolset",
        }
    )

    type: Literal["plan"] = Field("plan", init=False)
    """Plan toolset."""

    mode: PlanToolMode = Field(
        default="declarative",
        title="Plan tool mode",
    )
    """Tool mode:
    - 'declarative': Single set_plan tool with full list (default, recommended)
      - Fewer calls, better UX with parallel updates
    - 'granular': Separate tools (get/add/update/remove)
      - For simpler models or fine-grained control
    """

    tools: dict[PlanToolName, bool] | None = Field(
        default=None,
        title="Tool filter",
    )
    """Optional tool filter to enable/disable specific tools."""

    def get_provider(self) -> ResourceProvider:
        """Create plan tools provider."""
        from agentpool.resource_providers import PlanProvider

        provider = PlanProvider(mode=self.mode)
        if self.tools is not None:
            from agentpool.resource_providers import FilteringResourceProvider

            return FilteringResourceProvider(provider, cast(dict[str, bool], self.tools))
        return provider


class DebugToolsetConfig(BaseToolsetConfig):
    """Configuration for debug/introspection toolset.

    Provides tools for agent self-inspection and runtime debugging:
    - Code execution with access to runtime context (ctx, run_ctx, me)
    - In-memory log inspection and management
    - Platform path discovery
    - Agent and pool state inspection
    """

    model_config = ConfigDict(
        json_schema_extra={
            "x-icon": "octicon:bug-16",
            "x-doc-title": "Debug Toolset",
        }
    )

    type: Literal["debug"] = Field("debug", init=False)
    """Debug toolset."""

    def get_provider(self) -> ResourceProvider:
        """Create debug tools provider."""
        from agentpool_toolsets.builtin.debug import DebugTools

        return DebugTools(name=self.namespace or "debug")


class MCPDiscoveryToolsetConfig(BaseToolsetConfig):
    """Configuration for MCP discovery toolset.

    Enables dynamic discovery and use of MCP servers without preloading tools.
    Uses semantic search over 1000+ indexed servers for intelligent matching.

    Requires the `mcp-discovery` extra: `pip install agentpool[mcp-discovery]`
    """

    model_config = ConfigDict(
        json_schema_extra={
            "x-icon": "octicon:search-16",
            "x-doc-title": "MCP Discovery Toolset",
        }
    )

    type: Literal["mcp_discovery"] = Field("mcp_discovery", init=False)
    """MCP discovery toolset."""

    registry_url: str = Field(
        default="https://registry.modelcontextprotocol.io",
        title="Registry URL",
    )
    """Base URL for the MCP registry API."""

    allowed_servers: list[str] | None = Field(
        default=None,
        title="Allowed servers",
    )
    """If set, only these server names can be used."""

    blocked_servers: list[str] | None = Field(
        default=None,
        title="Blocked servers",
    )
    """Server names that cannot be used."""

    def get_provider(self) -> ResourceProvider:
        """Create MCP discovery tools provider."""
        from agentpool_toolsets.mcp_discovery.toolset import MCPDiscoveryToolset

        return MCPDiscoveryToolset(
            name=self.namespace or "mcp_discovery",
            registry_url=self.registry_url,
            allowed_servers=self.allowed_servers,
            blocked_servers=self.blocked_servers,
        )


class CronToolsetConfig(BaseToolsetConfig):
    """Configuration for cron scheduling toolset.

    Gives agents the ability to schedule, list, and remove recurring or
    one-shot jobs at runtime. Jobs are persisted to a JSON file.

    Requires the ``bot`` extra: ``pip install agentpool[bot]``
    """

    model_config = ConfigDict(
        json_schema_extra={
            "x-icon": "octicon:clock-16",
            "x-doc-title": "Cron Toolset",
        }
    )

    type: Literal["cron"] = Field("cron", init=False)
    """Cron scheduling toolset."""

    store_path: str = Field(
        default="~/.agentpool/cron_jobs.json",
        title="Job store path",
        examples=["~/.agentpool/cron_jobs.json", "/tmp/cron.json"],
    )
    """Path to the JSON file where scheduled jobs are persisted."""

    def get_provider(self) -> ResourceProvider:
        """Create cron tools provider."""
        from pathlib import Path

        from agentpool_bot.cron.service import CronService
        from agentpool_toolsets.cron import CronTools

        resolved = Path(self.store_path).expanduser()
        service = CronService(store_path=resolved)
        return CronTools(service=service, name=self.namespace or "cron")


ToolsetConfig = Annotated[
    OpenAPIToolsetConfig
    | EntryPointToolsetConfig
    | ComposioToolSetConfig
    | ProcessManagementToolsetConfig
    | SkillsToolsetConfig
    | CodeToolsetConfig
    | FSSpecToolsetConfig
    | VFSToolsetConfig
    | SubagentToolsetConfig
    | WorkersToolsetConfig
    | AggregatingToolsetConfig
    | CodeModeToolsetConfig
    | RemoteCodeModeToolsetConfig
    | SearchToolsetConfig
    | NotificationsToolsetConfig
    | ConfigCreationToolsetConfig
    | ImportToolsToolsetConfig
    | PlanToolsetConfig
    | DebugToolsetConfig
    | MCPDiscoveryToolsetConfig
    | CronToolsetConfig
    | CustomToolsetConfig,
    Field(discriminator="type"),
]
