"""The main Agent. Can do all sort of crazy things."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import timedelta
from pathlib import Path
import time
from typing import TYPE_CHECKING, Any, ClassVar, Self, TypedDict, TypeVar, overload
from uuid import uuid4

import logfire
from pydantic_ai import Agent as PydanticAgent, CallToolsNode, ModelRequestNode, RunContext
from pydantic_ai.models import Model
from pydantic_ai.tools import ToolDefinition

from agentpool.agents.base_agent import BaseAgent
from agentpool.agents.context import AgentContext
from agentpool.agents.events import RunStartedEvent, StreamCompleteEvent
from agentpool.agents.exceptions import UnknownCategoryError, UnknownModeError
from agentpool.agents.native_agent.helpers import process_tool_event
from agentpool.log import get_logger
from agentpool.messaging import ChatMessage, MessageHistory
from agentpool.storage import StorageManager
from agentpool.tools import ToolManager
from agentpool.tools.base import FunctionTool
from agentpool.tools.exceptions import ToolError
from agentpool.utils.result_utils import to_type
from agentpool.utils.streams import merge_queue_into_iterator


if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine, Sequence
    from types import TracebackType

    from exxec import ExecutionEnvironment
    from pydantic_ai import BaseToolCallPart, UsageLimits, UserContent
    from pydantic_ai.builtin_tools import AbstractBuiltinTool
    from pydantic_ai.models import Model
    from pydantic_ai.output import OutputSpec
    from pydantic_ai.settings import ModelSettings
    from slashed import BaseCommand
    from tokonomics.model_discovery import ProviderType
    from tokonomics.model_discovery.model_info import ModelInfo
    from toprompt import AnyPromptType
    from upathtools import JoinablePathLike

    from agentpool.agents.events import RichAgentStreamEvent
    from agentpool.agents.modes import ModeCategory
    from agentpool.common_types import (
        AnyEventHandlerType,
        EndStrategy,
        ModelType,
        ProcessorCallback,
        SessionIdType,
        StrPath,
        ToolType,
    )
    from agentpool.delegation import AgentPool
    from agentpool.hooks import AgentHooks
    from agentpool.messaging import MessageNode
    from agentpool.models.agents import NativeAgentConfig, ToolMode
    from agentpool.prompts.prompts import PromptType
    from agentpool.resource_providers import ResourceProvider
    from agentpool.sessions import SessionData
    from agentpool.tools import Tool
    from agentpool.ui.base import InputProvider
    from agentpool_config.knowledge import Knowledge
    from agentpool_config.mcp_server import MCPServerConfig
    from agentpool_config.nodes import ToolConfirmationMode
    from agentpool_config.session import MemoryConfig, SessionQuery


logger = get_logger(__name__)
# OutputDataT = TypeVar('OutputDataT', default=str, covariant=True)
NoneType = type(None)

TResult = TypeVar("TResult")
VALID_MODES = ["always", "never", "per_tool"]


class AgentKwargs(TypedDict, total=False):
    """Keyword arguments for configuring an Agent instance."""

    description: str | None
    model: ModelType
    system_prompt: str | Sequence[str]
    tools: Sequence[ToolType] | None
    toolsets: Sequence[ResourceProvider] | None
    mcp_servers: Sequence[str | MCPServerConfig] | None
    skills_paths: Sequence[JoinablePathLike] | None
    retries: int
    output_retries: int | None
    end_strategy: EndStrategy
    # context: AgentContext[Any] | None  # x
    session: SessionIdType | SessionQuery | MemoryConfig | bool
    input_provider: InputProvider | None
    event_handlers: Sequence[AnyEventHandlerType] | None
    env: ExecutionEnvironment | None

    hooks: AgentHooks | None
    model_settings: ModelSettings | None
    usage_limits: UsageLimits | None
    providers: Sequence[ProviderType] | None
    storage: StorageManager | None


class Agent[TDeps = None, OutputDataT = str](BaseAgent[TDeps, OutputDataT]):
    """The main agent class.

    Generically typed with: Agent[Type of Dependencies, Type of Result]
    """

    AGENT_TYPE: ClassVar = "native"

    def __init__(  # noqa: PLR0915
        # we dont use AgentKwargs here so that we can work with explicit ones in the ctor
        self,
        name: str = "agentpool",
        *,
        deps_type: type[TDeps] | None = None,
        model: ModelType,
        output_type: OutputSpec[OutputDataT] = str,  # type: ignore[assignment]
        # context: AgentContext[TDeps] | None = None,
        session: SessionIdType | SessionQuery | MemoryConfig | bool = None,
        system_prompt: AnyPromptType | Sequence[AnyPromptType] = (),
        description: str | None = None,
        display_name: str | None = None,
        tools: Sequence[ToolType] | None = None,
        toolsets: Sequence[ResourceProvider] | None = None,
        mcp_servers: Sequence[str | MCPServerConfig] | None = None,
        resources: Sequence[PromptType | str] = (),
        skills_paths: Sequence[JoinablePathLike] | None = None,
        retries: int = 1,
        output_retries: int | None = None,
        end_strategy: EndStrategy = "early",
        input_provider: InputProvider | None = None,
        parallel_init: bool = True,
        model_settings: ModelSettings | None = None,
        event_handlers: Sequence[AnyEventHandlerType] | None = None,
        agent_pool: AgentPool[Any] | None = None,
        tool_mode: ToolMode | None = None,
        knowledge: Knowledge | None = None,
        agent_config: NativeAgentConfig | None = None,
        env: ExecutionEnvironment | StrPath | None = None,
        hooks: AgentHooks | None = None,
        tool_confirmation_mode: ToolConfirmationMode = "per_tool",
        builtin_tools: Sequence[AbstractBuiltinTool] | None = None,
        usage_limits: UsageLimits | None = None,
        providers: Sequence[ProviderType] | None = None,
        commands: Sequence[BaseCommand] | None = None,
        history_processors: Sequence[Callable[..., Any]] | None = None,
        storage: StorageManager | None = None,
    ) -> None:
        """Initialize agent.

        Args:
            name: Identifier for the agent (used for logging and lookups)
            deps_type: Type of dependencies to use
            model: The default model to use (defaults to GPT-5)
            output_type: The default output type to use (defaults to str)
            context: Agent context with configuration
            session: Memory configuration.
                - None: Default memory config
                - False: Disable message history (max_messages=0)
                - int: Max tokens for memory
                - str/UUID: Session identifier
                - MemoryConfig: Full memory configuration
                - MemoryProvider: Custom memory provider
                - SessionQuery: Session query

            system_prompt: System prompts for the agent
            description: Description of the Agent ("what it can do")
            display_name: Human-readable display name (falls back to name)
            tools: List of tools to register with the agent
            toolsets: List of toolset resource providers for the agent
            mcp_servers: MCP servers to connect to
            resources: Additional resources to load
            skills_paths: Local directories to search for agent-specific skills
            retries: Default number of retries for failed operations
            output_retries: Max retries for result validation (defaults to retries)
            end_strategy: Strategy for handling tool calls that are requested alongside
                          a final result
            input_provider: Provider for human input (tool confirmation / HumanProviders)
            parallel_init: Whether to initialize resources in parallel
            model_settings: Settings for the AI model
            event_handlers: Sequence of event handlers to register with the agent
            agent_pool: AgentPool instance for managing agent resources
            tool_mode: Tool execution mode (None or "codemode")
            knowledge: Knowledge sources for this agent
            agent_config: Agent configuration
            env: Execution environment for code/command execution and filesystem access
            hooks: AgentHooks instance for intercepting agent behavior at run and tool events
            tool_confirmation_mode: Tool confirmation mode
            builtin_tools: PydanticAI builtin tools (WebSearchTool, CodeExecutionTool, etc.)
            usage_limits: Per-request usage limits (applied to each run() call independently,
                not cumulative across the session)
            providers: Model providers for model discovery (e.g., ["openai", "anthropic"]).
                Defaults to ["models.dev"] if not specified.
            commands: Slash commands
            history_processors: Pre-resolved history processor callables
            storage: Optional per-agent StorageManager. Falls back to pool.storage if not provided.
        """
        from agentpool.agents.interactions import Interactions
        from agentpool.agents.native_agent.hook_manager import NativeAgentHookManager
        from agentpool.agents.sys_prompts import SystemPrompts
        from agentpool.models.manifest import AgentsManifest
        from agentpool.prompts.conversion_manager import ConversionManager
        from agentpool_commands.pool import CompactCommand
        from agentpool_config.session import MemoryConfig

        self.model_settings = model_settings
        memory_cfg = (
            session if isinstance(session, MemoryConfig) else MemoryConfig.from_value(session)
        )
        # Collect MCP servers from config
        all_mcp_servers = list(mcp_servers) if mcp_servers else []
        if agent_config and agent_config.mcp_servers:
            all_mcp_servers.extend(agent_config.get_mcp_servers())
        # Add CompactCommand - only makes sense for Native Agent (has own history)
        # Other agents (ClaudeCode, ACP, AGUI) don't control their history directly
        all_commands = list(commands) if commands else []
        all_commands.append(CompactCommand())
        # Call base class with shared parameters
        super().__init__(
            name=name,
            description=description,
            display_name=display_name,
            deps_type=deps_type,
            enable_logging=memory_cfg.enable,
            mcp_servers=all_mcp_servers,
            agent_pool=agent_pool,
            event_configs=agent_config.triggers if agent_config else [],
            env=env,
            input_provider=input_provider,
            output_type=to_type(output_type),  # type: ignore[arg-type]
            event_handlers=event_handlers,
            commands=all_commands,
            hooks=hooks,
            storage=storage,
        )
        self.tool_confirmation_mode: ToolConfirmationMode = tool_confirmation_mode
        # Store builtin tools for pydantic-ai
        self._builtin_tools = list(builtin_tools) if builtin_tools else []
        # Override tools with Agent-specific ToolManager (with tools and tool_mode)
        self.tools = ToolManager(tools, tool_mode=tool_mode)
        for toolset_provider in toolsets or []:
            self.tools.add_provider(toolset_provider)
        aggregating_provider = self.mcp.get_aggregating_provider()
        self.tools.add_provider(aggregating_provider)
        # Override conversation with Agent-specific MessageHistory (with storage, etc.)
        resources = list(resources)
        if knowledge:
            resources.extend(knowledge.get_resources())
        manifest = agent_pool.manifest if agent_pool else AgentsManifest()
        effective_storage = self.storage or StorageManager()
        self.conversation = MessageHistory(
            storage=effective_storage,
            converter=ConversionManager(config=manifest.conversion),
            session_config=memory_cfg,
            resources=resources,
        )
        if isinstance(model, str):
            self._model, settings = self._resolve_model_string(model)
            if settings:
                self.model_settings = settings
        else:
            self._model = model
        self._retries = retries
        self._end_strategy: EndStrategy = end_strategy
        self._output_retries = output_retries
        self.parallel_init = parallel_init
        self.talk = Interactions(self)
        # Set up system prompts
        all_prompts: list[AnyPromptType] = []
        if isinstance(system_prompt, (list, tuple)):
            all_prompts.extend(system_prompt)
        elif system_prompt:
            all_prompts.append(system_prompt)
        prompt_manager = self.agent_pool.prompt_manager if self.agent_pool else None
        self.sys_prompts = SystemPrompts(all_prompts, prompt_manager=prompt_manager)
        self._formatted_system_prompt: str | None = None  # Set in __aenter__
        self._hook_manager = NativeAgentHookManager(
            agent_name=self.name,
            agent_hooks=hooks,
            injection_manager=self._injection_manager,
        )
        self._default_usage_limits = usage_limits
        self._providers = list(providers) if providers else None  # model discovery
        self._history_processors = list(history_processors) if history_processors else []

    @classmethod
    def from_config(
        cls,
        config: NativeAgentConfig,
        *,
        event_handlers: Sequence[AnyEventHandlerType] | None = None,
        input_provider: InputProvider | None = None,
        agent_pool: AgentPool[Any] | None = None,
        deps_type: type[TDeps] | None = None,
    ) -> Self:
        """Create a native Agent from a config object.

        This is the preferred way to instantiate an Agent from configuration.
        Handles system prompt resolution, model resolution, toolsets setup, etc.

        Args:
            config: Native agent configuration
            name: Optional name override (used for manifest lookups, defaults to config.name)
            event_handlers: Optional event handlers (merged with config handlers)
            input_provider: Optional input provider for user interactions
            agent_pool: Optional agent pool for coordination
            deps_type: Optional dependency type

        Returns:
            Configured Agent instance
        """
        from agentpool.models.manifest import AgentsManifest
        from agentpool.utils.result_utils import to_type
        from agentpool_config.system_prompts import (
            FilePromptConfig,
            FunctionPromptConfig,
            LibraryPromptConfig,
            StaticPromptConfig,
        )
        from agentpool_toolsets.builtin.workers import WorkersTools

        name = config.name or "native_agent"
        # Get manifest from pool or create empty one
        manifest = agent_pool.manifest if agent_pool is not None else AgentsManifest()
        # Normalize system_prompt to a list for iteration
        sys_prompts: list[str] = []
        if (sys_prompt := config.system_prompt) is not None:
            prompts_to_process = [sys_prompt] if isinstance(sys_prompt, str) else sys_prompt
            for prompt in prompts_to_process:
                match prompt:
                    case (str() as sys_prompt) | StaticPromptConfig(content=sys_prompt):
                        sys_prompts.append(sys_prompt)
                    case FilePromptConfig(path=path, variables=variables):
                        template_path = Path(path)
                        if not template_path.is_absolute() and config.config_file_path:
                            template_path = Path(config.config_file_path).parent / path
                        template_content = template_path.read_text("utf-8")
                        if variables:
                            from jinja2 import Template

                            template = Template(template_content)
                            content = template.render(**variables)
                        else:
                            content = template_content
                        sys_prompts.append(content)
                    case LibraryPromptConfig(reference=reference):
                        if agent_pool is None:
                            raise ValueError(
                                f"Cannot resolve library prompt {reference!r}: no agent pool"
                            )
                        try:
                            content = agent_pool.prompt_manager.get.sync(reference)
                            sys_prompts.append(content)
                        except Exception as e:
                            msg = f"Failed to load library prompt {reference!r} for {name!r}"
                            logger.exception(msg)
                            raise ValueError(msg) from e
                    case FunctionPromptConfig(function=function, arguments=arguments):
                        content = function(**arguments)
                        sys_prompts.append(content)

        # Prepare toolsets list
        toolsets_list = config.get_toolsets()
        if config_tool_provider := config.get_tool_provider():
            toolsets_list.append(config_tool_provider)
        # Convert workers config to a toolset
        if workers := config.get_workers():
            toolsets_list.append(WorkersTools(workers=workers, name="workers"))
        # Resolve output type from config
        resolved_output_type = to_type(t, manifest.responses) if (t := config.output_type) else str
        # Merge event handlers
        config_handlers = config.get_event_handlers()
        merged_handlers: list[AnyEventHandlerType] = [*config_handlers, *(event_handlers or [])]
        resolved_model = manifest.resolve_model(config.model)
        return cls(
            model=resolved_model.get_model(),
            model_settings=resolved_model.get_model_settings(),
            system_prompt=sys_prompts,
            name=name,
            display_name=config.display_name,
            deps_type=deps_type,
            env=config.get_execution_environment(),
            description=config.description,
            retries=config.retries,
            session=config.get_session_config(),
            output_retries=config.output_retries,
            end_strategy=config.end_strategy,
            agent_config=config,
            input_provider=input_provider,
            output_type=resolved_output_type,  # type: ignore[arg-type]
            event_handlers=merged_handlers or None,
            agent_pool=agent_pool,
            tool_mode=config.tool_mode,
            knowledge=config.knowledge,
            toolsets=toolsets_list,
            hooks=config.hooks.get_agent_hooks() if config.hooks else None,
            tool_confirmation_mode=config.requires_tool_confirmation,
            builtin_tools=config.get_builtin_tools() or None,
            usage_limits=config.usage_limits,
            providers=config.model_providers,
            history_processors=config.get_history_processors() or None,
        )

    async def __aenter__(self) -> Self:
        """Enter async context and set up MCP servers."""
        # Collect all coroutines that need to be run
        coros: list[Coroutine[Any, Any, Any]] = [
            super().__aenter__(),
            *self.conversation.get_initialization_tasks(),
        ]
        try:
            if self.parallel_init and coros:
                await asyncio.gather(*coros)
            else:
                for coro in coros:
                    await coro
            # Format system prompt once at startup (enables caching)
            self._formatted_system_prompt = await self.sys_prompts.format_system_prompt(self)
        except Exception as e:
            raise RuntimeError("Failed to initialize agent") from e
        else:
            return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Exit async context."""
        await super().__aexit__(exc_type, exc_val, exc_tb)

    @overload
    @classmethod
    def from_callback(
        cls,
        callback: Callable[..., Awaitable[TResult]],
        *,
        name: str | None = None,
        **kwargs: Any,
    ) -> Agent[None, TResult]: ...

    @overload
    @classmethod
    def from_callback(
        cls,
        callback: Callable[..., TResult],
        *,
        name: str | None = None,
        **kwargs: Any,
    ) -> Agent[None, TResult]: ...

    @classmethod
    def from_callback(
        cls,
        callback: ProcessorCallback[Any],
        *,
        name: str | None = None,
        **kwargs: Any,
    ) -> Agent[None, Any]:
        """Create an agent from a processing callback.

        Args:
            callback: Function to process messages. Can be:
                - sync or async
                - with or without context
                - must return str for pipeline compatibility
            name: Optional name for the agent
            kwargs: Additional arguments for agent
        """
        from llmling_models import function_to_model

        from agentpool.utils.inspection import get_fn_name
        from agentpool.utils.signatures import get_return_type

        name = name or get_fn_name(callback) or "processor"
        model = function_to_model(callback)
        output_type = get_return_type(callback)
        return Agent(model=model, name=name, output_type=output_type or str, **kwargs)

    @property
    def name(self) -> str:
        """Get agent name."""
        return self._name or "agentpool"

    @name.setter
    def name(self, value: str) -> None:
        """Set agent name."""
        self._name = value

    def _resolve_model_string(self, model: str) -> tuple[Model, ModelSettings | None]:
        """Resolve a model string, checking variants first.

        Args:
            model: Model identifier or variant name

        Returns:
            Tuple of (Model instance, ModelSettings or None)
            Settings are only returned for variants.
        """
        from llmling_models import infer_model

        # Check if it's a variant
        if self.agent_pool and model in self.agent_pool.manifest.model_variants:
            config = self.agent_pool.manifest.model_variants[model]
            return config.get_model(), config.get_model_settings()
        # Regular model string - no settings
        return infer_model(model), None

    def to_structured[NewOutputDataT](
        self,
        output_type: type[NewOutputDataT],
    ) -> Agent[TDeps, NewOutputDataT]:
        """Convert this agent to a structured agent.

        Warning: This method mutates the agent in place and breaks caching.
        Changing output type modifies tool definitions sent to the API.

        Args:
            output_type: Type for structured responses.

        Returns:
            Self (same instance, not a copy)
        """
        self.log.debug("Setting result type", output_type=output_type)
        self._output_type = to_type(output_type)  # type: ignore[assignment]
        return self  # type: ignore

    @property
    def model_name(self) -> str | None:
        """Get the model name in a consistent format (provider:model_name)."""
        return self._model.model_id if self._model else None

    def to_tool(
        self,
        *,
        name: str | None = None,
        description: str | None = None,
        reset_history_on_run: bool = True,
        pass_message_history: bool = False,
        parent: Agent[Any, Any] | None = None,
        **_kwargs: Any,
    ) -> FunctionTool[OutputDataT]:
        """Create a tool from this agent.

        Args:
            name: Optional tool name override
            description: Optional tool description override
            reset_history_on_run: Clear agent's history before each run
            pass_message_history: Pass parent's message history to agent
            parent: Optional parent agent for history/context sharing
        """

        async def wrapped_tool(prompt: str) -> Any:
            if pass_message_history and not parent:
                raise ToolError("Parent agent required for message history sharing")

            if reset_history_on_run:
                await self.conversation.clear()

            history = None
            if pass_message_history and parent:
                history = parent.conversation.get_history()
                old = self.conversation.get_history()
                self.conversation.set_history(history)
            result = await self.run(prompt)
            if history:
                self.conversation.set_history(old)
            return result.data

        # Set the correct return annotation dynamically
        wrapped_tool.__annotations__ = {"prompt": str, "return": self._output_type or Any}
        normalized_name = self.name.replace("_", " ").title()
        docstring = f"Get expert answer from specialized agent: {normalized_name}"
        if desc := (description or self.description):
            docstring = f"{docstring}\n\n{desc}"
        tool_name = name or f"ask_{self.name}"
        wrapped_tool.__doc__ = docstring
        wrapped_tool.__name__ = tool_name
        return FunctionTool.from_callable(wrapped_tool, source="agent")

    async def get_agentlet[AgentOutputType](
        self,
        model: ModelType | None,
        output_type: type[AgentOutputType] | None,
        input_provider: InputProvider | None = None,
    ) -> PydanticAgent[AgentContext[TDeps], Any]:
        """Create pydantic-ai agent from current state."""
        from agentpool.agents.native_agent.tool_wrapping import wrap_tool

        tools = await self.tools.get_tools(state="enabled")
        final_type = to_type(output_type) if output_type not in [None, str] else self._output_type
        actual_model = model or self._model
        if isinstance(actual_model, str):
            model_, _settings = self._resolve_model_string(actual_model)
        else:
            model_ = actual_model

        context_for_tools = self.get_context(input_provider=input_provider)

        # Collect pydantic_ai.tools.Tool instances using Tool.to_pydantic_ai()
        pydantic_ai_tools = []
        for tool in tools:
            wrapped = wrap_tool(tool, context_for_tools, hooks=self._hook_manager)
            pydantic_ai_tool = tool.to_pydantic_ai(function_override=wrapped)
            pydantic_ai_tools.append(pydantic_ai_tool)

        return PydanticAgent(
            name=self.name,
            model=model_,
            model_settings=self.model_settings,
            instructions=self._formatted_system_prompt,
            retries=self._retries,
            end_strategy=self._end_strategy,
            output_retries=self._output_retries,
            deps_type=AgentContext[TDeps],
            output_type=final_type,
            tools=pydantic_ai_tools,
            builtin_tools=self._builtin_tools,
            history_processors=self._history_processors or None,
        )

    async def _stream_events(
        self,
        prompts: list[UserContent],
        *,
        user_msg: ChatMessage[Any],
        message_history: MessageHistory,
        effective_parent_id: str | None,
        store_history: bool = True,
        message_id: str | None = None,
        session_id: str | None = None,
        parent_id: str | None = None,
        input_provider: InputProvider | None = None,
        wait_for_connections: bool | None = None,
        deps: TDeps | None = None,
    ) -> AsyncIterator[RichAgentStreamEvent[OutputDataT]]:
        from pydantic_graph import End

        from agentpool.agents.native_agent.helpers import extract_text_from_messages

        message_id = message_id or str(uuid4())
        run_id = str(uuid4())
        start_time = time.perf_counter()
        history_list = message_history.get_history()
        assert self.session_id is not None  # Initialized by BaseAgent.run_stream()
        yield RunStartedEvent(session_id=self.session_id, run_id=run_id, agent_name=self.name)
        agentlet = await self.get_agentlet(None, self._output_type, input_provider)
        response_msg: ChatMessage[Any] | None = None
        # Prepend pending context parts (prompts are already pydantic-ai UserContent format)
        async with agentlet.iter(
            prompts,
            deps=agent_deps,
            message_history=[m for run in history_list for m in run.to_pydantic_ai()],
            usage_limits=self._default_usage_limits,
        ) as agent_run:
            pending_tcs: dict[str, BaseToolCallPart] = {}
            try:
                async for node in agent_run:
                    if self._cancelled:
                        self.log.info("Stream cancelled by user")
                        break
                    match node:
                        case End():
                            break
                        # Stream events from model request or tool call nodes
                        case ModelRequestNode() | CallToolsNode():
                            async with (
                                node.stream(agent_run.ctx) as stream,
                                merge_queue_into_iterator(stream, self._event_queue) as merged,  # type: ignore[arg-type]
                            ):
                                async for event in merged:
                                    if self._cancelled:
                                        break
                                    yield event
                                    if combined := process_tool_event(
                                        self.name,
                                        event,  # ty: ignore[invalid-argument-type]
                                        pending_tcs,
                                        message_id,
                                    ):
                                        yield combined
            except asyncio.CancelledError:
                self.log.info("Stream cancelled via task cancellation")
                self._cancelled = True

            # Build response message
            response_time = time.perf_counter() - start_time
            if self._cancelled:
                partial_content = extract_text_from_messages(
                    agent_run.all_messages(), include_interruption_note=True
                )
                response_msg = ChatMessage(
                    content=partial_content,
                    role="assistant",
                    name=self.name,
                    message_id=message_id,
                    session_id=self.session_id,
                    parent_id=user_msg.message_id,
                    response_time=response_time,
                    finish_reason="stop",
                )
                yield StreamCompleteEvent(message=response_msg)
                return

            if agent_run.result:
                response_msg = await ChatMessage.from_run_result(
                    agent_run.result,
                    agent_name=self.name,
                    message_id=message_id,
                    session_id=self.session_id,
                    parent_id=user_msg.message_id,
                    response_time=response_time,
                )
            else:
                raise RuntimeError("Stream completed without producing a result")

        # Send additional enriched completion event
        yield StreamCompleteEvent(message=response_msg)

    def register_worker(
        self,
        worker: MessageNode[Any, Any],
        *,
        name: str | None = None,
        reset_history_on_run: bool = True,
        pass_message_history: bool = False,
    ) -> Tool:
        """Register another agent as a worker tool."""
        return self.tools.register_worker(
            worker,
            name=name,
            reset_history_on_run=reset_history_on_run,
            pass_message_history=pass_message_history,
            parent=self if pass_message_history else None,
        )

    async def set_model(self, model: Model | str) -> None:
        """Set the model for this agent."""
        if isinstance(model, str):
            await self._set_mode(model, "model")
        else:
            # Direct Model instance assignment (no signal emission)
            self._model = model
            assert self.model_name is not None
            await self.update_state(config_id="model", value_id=self.model_name)

    async def _interrupt(self) -> None:
        """Cancel the current stream task."""
        if (task := self._current_stream_task) and not task.done():
            task.cancel()

    @asynccontextmanager
    async def temporary_state[T](
        self,
        *,
        output_type: type[T] | None = None,
        tools: list[ToolType] | None = None,
        replace_tools: bool = False,
        history: list[AnyPromptType] | SessionQuery | None = None,
        replace_history: bool = False,
        pause_routing: bool = False,
        model: ModelType | None = None,
    ) -> AsyncIterator[Self | Agent[T]]:
        """Temporarily modify agent state.

        Args:
            output_type: Temporary output type to use
            tools: Temporary tools to make available
            replace_tools: Whether to replace existing tools
            history: Conversation history (prompts or query)
            replace_history: Whether to replace existing history
            pause_routing: Whether to pause message routing
            model: Temporary model override
        """
        from pydantic_ai.models import Model

        old_model = self._model
        old_settings = self.model_settings
        if output_type:
            old_type = self._output_type
            self.to_structured(output_type)
        async with AsyncExitStack() as stack:
            if tools is not None:  # Tools
                await stack.enter_async_context(
                    self.tools.temporary_tools(tools, exclusive=replace_tools)
                )

            if history is not None:  # History
                await stack.enter_async_context(
                    self.conversation.temporary_state(history, replace_history=replace_history)
                )

            if pause_routing:  # Routing
                await stack.enter_async_context(self.connections.paused_routing())
            match model:
                case str():
                    self._model, settings = self._resolve_model_string(model)
                    if settings:
                        self.model_settings = settings
                case Model():
                    self._model = model
            try:
                yield self
            finally:  # Restore model and settings
                if model is not None:
                    if old_model:
                        self._model = old_model
                    self.model_settings = old_settings
                if output_type:
                    self.to_structured(old_type)

    async def get_available_models(self) -> list[ModelInfo] | None:
        """Get available models for this agent.

        Uses tokonomics model discovery to fetch models from configured providers.
        Defaults to models.dev if no providers specified.

        Returns:
            List of tokonomics ModelInfo, or None if discovery fails
        """
        from tokonomics.model_discovery import get_all_models

        delta = timedelta(days=200)
        if self._providers:
            return await get_all_models(providers=self._providers, max_age=delta)
        return await get_all_models(providers=["models.dev"], max_age=delta)

    async def get_modes(self) -> list[ModeCategory]:
        """Get available mode categories for this agent."""
        from agentpool.agents.native_agent.helpers import (
            get_model_category,
            get_permission_category,
        )

        categories: list[ModeCategory] = []
        # Use native ToolConfirmationMode value directly
        mode_category = get_permission_category(self.tool_confirmation_mode)
        categories.append(mode_category)
        if models := await self.get_available_models():
            current_model = self.model_name or (models[0].id if models else "")
            model_category = get_model_category(current_model, models)
            categories.append(model_category)
        return categories

    async def _set_mode(self, mode_id: str, category_id: str) -> None:
        """Handle permissions and model mode switching."""
        if category_id == "mode":
            # Use native ToolConfirmationMode values directly
            if mode_id not in VALID_MODES:
                raise UnknownModeError(mode_id, VALID_MODES)
            self.tool_confirmation_mode = mode_id  # type: ignore[assignment]
            await self.update_state(config_id="mode", value_id=mode_id)

        elif category_id == "model":
            # Set the model directly
            self._model, settings = self._resolve_model_string(mode_id)
            if settings:
                self.model_settings = settings
            await self.update_state(config_id="model", value_id=mode_id)
        else:
            raise UnknownCategoryError(category_id, ["mode", "model"])

    async def list_sessions(
        self,
        *,
        cwd: str | None = None,
        limit: int | None = None,
    ) -> list[SessionData]:
        """List sessions from storage.

        For native agents, queries the storage manager for all sessions
        associated with this agent. Fetches conversation titles from storage.

        Args:
            cwd: Filter sessions by working directory (optional)
            limit: Maximum number of sessions to return (optional)

        Returns:
            List of SessionData objects
        """
        storage = self.storage
        if not storage:
            return []
        try:
            session_ids = await storage.list_session_ids(agent_name=self.name)
            result: list[SessionData] = []
            for session_id in session_ids:
                if session_data := await storage.load_session(session_id):
                    # Filter by cwd if specified
                    if cwd is not None and session_data.cwd != cwd:
                        continue
                    # Fetch title from conversation storage if not in metadata
                    if not session_data.title and (
                        title := await storage.get_session_title(session_data.session_id)
                    ):
                        session_data = session_data.with_metadata(title=title)
                    result.append(session_data)
                    if limit is not None and len(result) >= limit:
                        break

        except Exception:
            self.log.exception("Failed to list sessions")
            return []
        else:
            return result

    async def load_session(self, session_id: str) -> SessionData | None:
        """Load and restore a session from storage.

        Loads session data and restores the conversation history for this agent.

        Args:
            session_id: Unique identifier for the session to load

        Returns:
            SessionData if session was found and loaded, None otherwise
        """
        storage = self.storage
        if not storage:
            return None
        try:
            session_data = await storage.load_session(session_id)
            if not session_data:
                return None
            # Load conversation history if available from storage providers
            if storage.providers:
                provider = storage.providers[0]
                if provider.can_load_history:
                    messages = await provider.get_session_messages(session_id=session_id)
                    self.conversation.chat_messages.clear()
                    self.conversation.chat_messages.extend(messages)
                    msg = "Session loaded with conversation history"
                    self.log.info(msg, session_id=session_id, message_count=len(messages))
                else:
                    self.log.info("Session loaded (no history support)", session_id=session_id)
            else:
                self.log.info("Session loaded (no storage providers)", session_id=session_id)

        except Exception:
            self.log.exception("Failed to load session", session_id=session_id)
            return None
        else:
            return session_data


if __name__ == "__main__":
    import logging

    logfire.configure()
    logfire.instrument_pydantic_ai()
    logging.basicConfig(handlers=[logfire.LogfireLoggingHandler()])
    sys_prompt = "Open browser with google,"
    _model = "openai:gpt-5-nano"

    async def handle_events(ctx: AgentContext[Any], event: RichAgentStreamEvent[Any]) -> None:
        print(f"[EVENT] {type(event).__name__}: {event}")

    agent = Agent(model=_model, tools=["webbrowser.open"], event_handlers=[handle_events])
    result = agent.run.sync(sys_prompt)
