"""Models for agent configuration."""

from __future__ import annotations

from collections.abc import Callable, Sequence  # noqa: TC003
import inspect
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal, assert_never
from uuid import UUID

from llmling_models_config import AnyModelConfig  # noqa: TC002
from pydantic import ConfigDict, Field, model_validator
from pydantic_ai import EndStrategy, UsageLimits  # noqa: TC002
from schemez import InlineSchemaDef
from tokonomics.model_discovery import ProviderType  # noqa: TC002
from tokonomics.model_names import ModelId  # noqa: TC002
from toprompt import render_prompt

from agentpool import log
from agentpool.models.fields import OutputTypeField, SystemPromptField  # noqa: TC001
from agentpool.prompts.prompts import PromptMessage, StaticPrompt
from agentpool.resource_providers import StaticResourceProvider
from agentpool_config import NativeAgentToolConfig
from agentpool_config.agentpool_tools import BashToolConfig
from agentpool_config.builtin_tools import BaseBuiltinToolConfig
from agentpool_config.knowledge import Knowledge  # noqa: TC001
from agentpool_config.nodes import BaseAgentConfig
from agentpool_config.session import MemoryConfig, SessionQuery
from agentpool_config.tools import BaseToolConfig, ImportToolConfig
from agentpool_config.toolsets import BaseToolsetConfig, ToolsetConfig
from agentpool_config.workers import (
    AgentWorkerConfig,
    TeamWorkerConfig,
    WorkerConfig,
)


if TYPE_CHECKING:
    from agentpool.agents.native_agent import Agent
    from agentpool.common_types import AnyEventHandlerType
    from agentpool.delegation import AgentPool
    from agentpool.prompts.prompts import BasePrompt
    from agentpool.resource_providers import ResourceProvider
    from agentpool.tools.base import Tool
    from agentpool.ui.base import InputProvider

# Unified type for all tool configurations (single tools + toolsets)
AnyToolConfig = Annotated[NativeAgentToolConfig | ToolsetConfig, Field(discriminator="type")]
ToolMode = Literal["codemode"]

_MAX_PROCESSOR_PARAMS = 2

logger = log.get_logger(__name__)


def _validate_processor_signature(processor: Callable[..., Any]) -> None:
    """Validate that a history processor has a correct signature.

    Valid signatures:
    - sync: (messages) -> msgs
    - sync with ctx: (ctx, messages) -> msgs
    - async: async (messages) -> msgs
    - async with ctx: async (ctx, messages) -> msgs

    Raises:
        ValueError: If signature is not valid.
    """
    sig = inspect.signature(processor)
    params = list(sig.parameters.values())
    if len(params) not in (1, _MAX_PROCESSOR_PARAMS):
        msg = (
            f"History processor must take 1 or {_MAX_PROCESSOR_PARAMS} arguments, got {len(params)}"
        )
        raise ValueError(msg)
    if len(params) == _MAX_PROCESSOR_PARAMS:
        last_param_name = params[1].name.lower()
        if last_param_name not in ("messages", "msgs", "history"):
            msg = (
                f"Second parameter of history processor must be "
                f"messages/msgs/history, got {params[1].name}"
            )
            raise ValueError(msg)


class NativeAgentConfig(BaseAgentConfig):
    """Configuration for a single agent in the system.

    Defines an agent's complete configuration including its model, environment,
    and behavior settings.

    Docs: https://phil65.github.io/agentpool/YAML%20Configuration/agent_configuration/
    """

    model_config = ConfigDict(
        json_schema_extra={
            "x-icon": "octicon:hubot-16",
            "x-doc-title": "Agent Configuration",
        }
    )

    type: Literal["native"] = Field(default="native", init=False)
    """Top-level discriminator for agent type."""

    model: AnyModelConfig | ModelId | str = Field(
        ...,
        examples=["openai:gpt-5-nano"],
        title="Model configuration or name",
        json_schema_extra={
            "documentation_url": "https://phil65.github.io/agentpool/YAML%20Configuration/model_configuration/"
        },
    )
    """The model to use for this agent. Can be either a simple model name
    string (e.g. 'openai:gpt-5') or a structured model definition.

    Docs: https://phil65.github.io/agentpool/YAML%20Configuration/model_configuration/
    """

    tools: Sequence[AnyToolConfig | str] = Field(
        default_factory=list,
        examples=[
            ["webbrowser:open", "builtins:print"],
            [
                ImportToolConfig(import_path="webbrowser:open", name="web_browser"),
                BashToolConfig(timeout=30.0),
            ],
        ],
        title="Tool configurations",
        json_schema_extra={
            "documentation_url": "https://phil65.github.io/agentpool/YAML%20Configuration/tool_configuration/"
        },
    )
    """A list of tools and toolsets to register with this agent.

    Supports both single tools (bash, import, web_search, etc.) and
    toolsets (file_access, process_management, code, etc.).

    Docs: https://phil65.github.io/agentpool/YAML%20Configuration/tool_configuration/
    """

    session: str | SessionQuery | MemoryConfig | None = Field(
        default=None,
        examples=["main_session", "user_123"],
        title="Session configuration",
        json_schema_extra={
            "documentation_url": "https://phil65.github.io/agentpool/YAML%20Configuration/session_configuration/"
        },
    )
    """Session configuration for conversation recovery.

    Docs: https://phil65.github.io/agentpool/YAML%20Configuration/session_configuration/
    """

    output_type: OutputTypeField = None

    retries: int = Field(default=1, ge=0, examples=[1, 3], title="Model retries")
    """Number of retries for failed operations (maps to pydantic-ai's retries)"""

    output_retries: int | None = Field(default=None, examples=[1, 3], title="Output retries")
    """Max retries for result validation"""

    end_strategy: EndStrategy = Field(
        default="early",
        examples=["early", "exhaust"],
        title="Tool execution strategy",
    )
    """The strategy for handling multiple tool calls when a final result is found"""

    avatar: str | None = Field(
        default=None,
        examples=["https://example.com/avatar.png", "/assets/robot.jpg"],
        title="Avatar image",
    )
    """URL or path to agent's avatar image"""

    system_prompt: SystemPromptField = None
    """System prompt for the agent. Can be a string or list of strings/prompt configs.

    Docs: https://phil65.github.io/agentpool/YAML%20Configuration/system_prompts_configuration/
    """

    # context_sources: list[ContextSource] = Field(default_factory=list)
    # """Initial context sources to load"""

    knowledge: Knowledge | None = Field(
        default=None,
        title="Knowledge sources",
        json_schema_extra={
            "documentation_url": "https://phil65.github.io/agentpool/YAML%20Configuration/knowledge_configuration/"
        },
    )
    """Knowledge sources for this agent.

    Docs: https://phil65.github.io/agentpool/YAML%20Configuration/knowledge_configuration/
    """

    workers: list[WorkerConfig | str] = Field(
        default_factory=list,
        examples=[
            [AgentWorkerConfig(name="web_agent", reset_history_on_run=True)],
            [TeamWorkerConfig(name="analysis_team")],
            ["web_agent", "code_analyzer"],
        ],
        title="Worker agents",
        json_schema_extra={
            "documentation_url": "https://phil65.github.io/agentpool/YAML%20Configuration/worker_configuration/"
        },
    )
    """Worker agents which will be available as tools.

    Can be worker config objects or plain strings (agent names, resolved as AgentWorkerConfig).

    Docs: https://phil65.github.io/agentpool/YAML%20Configuration/worker_configuration/
    """

    usage_limits: UsageLimits | None = Field(default=None, title="Usage limits")
    """Usage limits for this agent."""

    model_providers: list[ProviderType] | None = Field(
        default=None,
        examples=[["models.dev"], ["anthropic", "openai"]],
        title="Model providers",
    )
    """List of model providers to use for model discovery.

    When set, the agent's get_available_models() will return models from these
    providers. Common values: "openai", "anthropic", "gemini", "mistral", etc.
    If not set, defaults to ["models.dev"].
    """

    tool_mode: ToolMode | None = Field(
        default=None,
        examples=["codemode"],
        title="Tool execution mode",
    )
    """Tool execution mode:
    - None: Default mode - tools are called directly
    - "codemode": Tools are wrapped in a Python execution environment
    """

    @model_validator(mode="before")
    @classmethod
    def validate_output_type(cls, data: dict[str, Any]) -> dict[str, Any]:
        """Convert result type and apply its settings."""
        output_type = data.get("output_type")
        if isinstance(output_type, dict):
            # Extract response-specific settings
            tool_name = output_type.pop("result_tool_name", None)
            tool_description = output_type.pop("result_tool_description", None)
            retries = output_type.pop("output_retries", None)

            # Convert remaining dict to ResponseDefinition
            if "type" not in output_type["response_schema"]:
                output_type["response_schema"]["type"] = "inline"
            data["output_type"]["response_schema"] = InlineSchemaDef(**output_type)

            # Apply extracted settings to agent config
            if tool_name:
                data["result_tool_name"] = tool_name
            if tool_description:
                data["result_tool_description"] = tool_description
            if retries is not None:
                data["output_retries"] = retries

        return data

    @model_validator(mode="before")
    @classmethod
    def handle_model_types(cls, data: dict[str, Any]) -> dict[str, Any]:
        """Convert model inputs to appropriate format."""
        if isinstance((model := data.get("model")), str):
            data["model"] = {"type": "string", "identifier": model}
        return data

    def get_agent[TDeps](
        self,
        event_handlers: Sequence[AnyEventHandlerType] | None = None,
        input_provider: InputProvider | None = None,
        pool: AgentPool[Any] | None = None,
        deps_type: type[TDeps] | None = None,  # type: ignore[valid-type]
    ) -> Agent[TDeps, Any]:
        from agentpool.agents.native_agent import Agent

        return Agent[TDeps].from_config(
            self,
            event_handlers=event_handlers,
            input_provider=input_provider,
            agent_pool=pool,
            deps_type=deps_type,
        )

    def get_workers(self) -> list[WorkerConfig]:
        """Resolve workers list, converting plain strings to AgentWorkerConfig."""
        resolved: list[WorkerConfig] = []
        for worker in self.workers:
            cfg = AgentWorkerConfig(name=worker) if isinstance(worker, str) else worker
            resolved.append(cfg)
        return resolved

    def get_tool_providers(self) -> list[ResourceProvider]:
        """Get all resource providers for this agent's tools.

        Processes the unified tools list, separating:
        - Toolsets: Each becomes its own ResourceProvider
        - Single tools: Aggregated into a single StaticResourceProvider

        Returns:
            List of ResourceProvider instances
        """
        from agentpool.tools.base import FunctionTool

        providers: list[ResourceProvider] = []
        static_tools: list[Tool] = []

        for tool_config in self.tools:
            # Skip builtin tools - they're handled via get_builtin_tools()
            match tool_config:
                case BaseBuiltinToolConfig():
                    continue
                case BaseToolsetConfig():
                    providers.append(tool_config.get_provider())
                case str():
                    static_tools.append(FunctionTool.from_callable(tool_config))
                case BaseToolConfig():
                    static_tools.append(tool_config.get_tool())

        # Wrap all single tools in one provider
        if static_tools:
            providers.append(StaticResourceProvider(name="tools", tools=static_tools))

        return providers

    # Keep old methods for backward compatibility during transition
    def get_toolsets(self) -> list[ResourceProvider]:
        """Get toolset providers. Deprecated: use get_tool_providers() instead."""
        return [
            p
            for p in self.get_tool_providers()
            if not isinstance(p, StaticResourceProvider) or p.name != "tools"
        ]

    def get_tool_provider(self) -> ResourceProvider | None:
        """Get single tools provider. Deprecated: use get_tool_providers() instead."""
        providers = self.get_tool_providers()
        return next(
            (p for p in providers if isinstance(p, StaticResourceProvider) and p.name == "tools"),
            None,
        )

    def get_builtin_tools(self) -> list[Any]:
        """Get pydantic-ai builtin tools from config.

        Returns:
            List of AbstractNativeTool instances (WebSearchTool, etc.)
        """
        return [i.get_builtin_tool() for i in self.tools if isinstance(i, BaseBuiltinToolConfig)]

    def get_session_config(self) -> MemoryConfig:
        """Get resolved memory configuration."""
        match self.session:
            case str() | UUID():
                return MemoryConfig(session=SessionQuery(name=str(self.session)))
            case SessionQuery():
                return MemoryConfig(session=self.session)
            case MemoryConfig():
                return self.session
            case None:
                return MemoryConfig()
            case _ as unreachable:
                assert_never(unreachable)

    def get_history_processors(self) -> list[Callable[..., Any]]:
        """Resolve history processor import paths to callables.

        Returns:
            List of resolved and validated processor callables.

        Raises:
            ValueError: If a processor cannot be imported or has invalid signature.
        """
        from agentpool.utils.importing import import_callable

        session_config = self.get_session_config()
        processor_paths = session_config.history_processors
        if not processor_paths:
            return []
        resolved: list[Callable[..., Any]] = []
        for path in processor_paths:
            try:
                processor = import_callable(path)
                _validate_processor_signature(processor)
                resolved.append(processor)
            except Exception as e:
                msg = f"Failed to resolve history processor '{path}': {e}"
                raise ValueError(msg) from e
        return resolved

    def get_system_prompts(self) -> list[BasePrompt]:
        """Get all system prompts as BasePrompts."""
        from agentpool_config.system_prompts import (
            FilePromptConfig,
            FunctionPromptConfig,
            LibraryPromptConfig,
            StaticPromptConfig,
        )

        prompts: list[BasePrompt] = []
        # Normalize system_prompt to a list
        if self.system_prompt is None:
            return prompts
        prompt_list = (
            [self.system_prompt] if isinstance(self.system_prompt, str) else self.system_prompt
        )
        for prompt in prompt_list:
            match prompt:
                case (str() as content) | StaticPromptConfig(content=content):
                    # Convert string to StaticPrompt
                    msgs = [PromptMessage(role="system", content=content)]
                    static = StaticPrompt(name="system", description="System prompt", messages=msgs)
                    prompts.append(static)
                case FilePromptConfig(path=path):
                    template_path = Path(path)
                    if not template_path.is_absolute() and self.config_file_path:
                        base_path = Path(self.config_file_path).parent
                        template_path = base_path / path
                    template_content = template_path.read_text("utf-8")
                    # Create a template-based prompt (for now as StaticPrompt with placeholder)
                    static_prompt = StaticPrompt(
                        name="system",
                        description=f"File prompt: {path}",
                        messages=[PromptMessage(role="system", content=template_content)],
                    )
                    prompts.append(static_prompt)
                case LibraryPromptConfig(reference=ref):
                    # Create placeholder for library prompts (resolved by manifest)
                    msg = PromptMessage(role="system", content=f"[LIBRARY:{ref}]")
                    static = StaticPrompt(name="system", description=f"Ref: {ref}", messages=[msg])
                    prompts.append(static)
                case FunctionPromptConfig(arguments=arguments, function=function):
                    # Import and call the function to get prompt content
                    content = function(**arguments)
                    static_prompt = StaticPrompt(
                        name="system",
                        description=f"Function prompt: {function}",
                        messages=[PromptMessage(role="system", content=content)],
                    )
                    prompts.append(static_prompt)
                case _ as unreachable:
                    assert_never(unreachable)
        return prompts

    def render_system_prompts(self, context: dict[str, Any] | None = None) -> list[str]:
        """Render system prompts with context."""
        from agentpool_config.system_prompts import (
            FilePromptConfig,
            FunctionPromptConfig,
            LibraryPromptConfig,
            StaticPromptConfig,
        )

        context = context or {"name": self.name, "id": 1, "model": self.model}
        rendered_prompts: list[str] = []
        # Normalize system_prompt to a list
        if self.system_prompt is None:
            return rendered_prompts
        prompt_list = (
            [self.system_prompt] if isinstance(self.system_prompt, str) else self.system_prompt
        )
        for prompt in prompt_list:
            match prompt:
                case (str() as content) | StaticPromptConfig(content=content):
                    rendered_prompts.append(render_prompt(content, {"agent": context}))
                case FilePromptConfig(path=path, variables=variables):
                    # Load and render Jinja template from file
                    template_path = Path(path)
                    if not template_path.is_absolute() and self.config_file_path:
                        base_path = Path(self.config_file_path).parent
                        template_path = base_path / path

                    template_content = template_path.read_text("utf-8")
                    template_ctx = {"agent": context, **variables}
                    rendered_prompts.append(render_prompt(template_content, template_ctx))
                case LibraryPromptConfig(reference=reference):
                    # This will be handled by the manifest's get_agent method
                    # For now, just add a placeholder
                    rendered_prompts.append(f"[LIBRARY:{reference}]")
                case FunctionPromptConfig(function=function, arguments=arguments):
                    # Import and call the function to get prompt content
                    content = function(**arguments)
                    rendered_prompts.append(render_prompt(content, {"agent": context}))

        return rendered_prompts


if __name__ == "__main__":
    model = "openai:gpt-5-nano"
    agent_cfg = NativeAgentConfig(name="test_agent", model=model)
    print(agent_cfg)
