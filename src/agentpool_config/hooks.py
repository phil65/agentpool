"""Hook configuration models for agent lifecycle events."""

from typing import TYPE_CHECKING, Annotated, Any, Literal

from exxec_config import ExecutionEnvironmentConfig
from pydantic import ConfigDict, Field
from schemez import Schema


if TYPE_CHECKING:
    from exxec import ExecutionEnvironment

    from agentpool.hooks.base import Hook, HookEvent


class BaseHookConfig(Schema):
    """Base configuration for hooks."""

    type: str = Field(init=False, title="Hook type")
    """Hook type discriminator."""

    matcher: str | None = Field(
        default=None,
        examples=["Write|Edit", "Bash.*", "*"],
        title="Pattern matcher",
    )
    """Regex pattern to match tool names. None or '*' matches all."""

    timeout: float = Field(
        default=60.0,
        gt=0,
        examples=[30.0, 60.0, 120.0],
        title="Timeout seconds",
    )
    """Maximum execution time in seconds."""

    enabled: bool = Field(default=True, title="Hook enabled")
    """Whether this hook is active."""

    def get_hook(self, event: HookEvent) -> Hook:
        """Create runtime hook from this config.

        Args:
            event: The lifecycle event this hook handles.

        Returns:
            Runtime hook instance.
        """
        raise NotImplementedError


class CommandHookConfig(BaseHookConfig):
    """Hook that executes a shell command.

    The command receives hook input as JSON via stdin and should return
    JSON output via stdout. Exit code 0 = success, exit code 2 = block.

    By default, command hooks run in the agent's execution environment
    (e.g. Docker, E2B sandbox) so they operate on the same filesystem
    as the agent's tools. Use the ``environment`` field to override with
    a specific environment, or set it to a local path to force local execution.
    """

    model_config = ConfigDict(json_schema_extra={"x-doc-title": "Command Hook"})

    type: Literal["command"] = Field("command", init=False)
    """Command hook type."""

    command: str = Field(
        examples=[
            "/path/to/script.sh",
            "$PROJECT_DIR/hooks/validate.py",
            "python -m myproject.hooks.check",
        ],
        title="Shell command",
    )
    """Shell command to execute. Supports $PROJECT_DIR variable."""

    env: dict[str, str] | None = Field(
        default=None,
        examples=[{"DEBUG": "1", "LOG_LEVEL": "info"}],
        title="Environment variables",
    )
    """Additional environment variables for the command."""

    environment: ExecutionEnvironmentConfig | str | None = Field(
        default=None,
        title="Execution environment",
        examples=["docker"],
    )
    """Per-hook execution environment override.

    If set, the hook command runs in this environment instead of the agent's.
    Accepts an ExecutionEnvironmentConfig or a string path for local execution.
    If None (default), uses the agent's execution environment.
    """

    def get_hook(self, event: HookEvent) -> Hook:
        """Create runtime command hook."""
        from agentpool.hooks import CommandHook

        return CommandHook(
            event=event,
            command=self.command,
            matcher=self.matcher,
            timeout=self.timeout,
            enabled=self.enabled,
            env=self.env,
            execution_env=self._resolve_execution_env(),
        )

    def _resolve_execution_env(self) -> ExecutionEnvironment | None:
        """Resolve the per-hook execution environment if configured.

        Returns:
            ExecutionEnvironment instance, or None to use agent's env.
        """
        from exxec.local_provider import LocalExecutionEnvironment
        from exxec_config import BaseExecutionEnvironmentConfig

        match self.environment:
            case BaseExecutionEnvironmentConfig() as cfg:
                return cfg.get_provider()
            case str() as path:
                return LocalExecutionEnvironment(cwd=path)
            case None:
                return None


class CallableHookConfig(BaseHookConfig):
    """Hook that executes a Python callable.

    The callable receives hook input as a dictionary and should return
    a HookResult dictionary or None.
    """

    model_config = ConfigDict(json_schema_extra={"x-doc-title": "Callable Hook"})

    type: Literal["callable"] = Field("callable", init=False)
    """Callable hook type."""

    import_path: str = Field(
        examples=[
            "myproject.hooks.validate_tool",
            "myapp.security.audit_command",
        ],
        title="Import path",
    )
    """Dotted import path to the callable."""

    arguments: dict[str, Any] = Field(
        default_factory=dict,
        examples=[{"strict": True, "allowed_paths": ["/tmp"]}],
        title="Arguments",
    )
    """Additional keyword arguments passed to the callable."""

    def get_hook(self, event: HookEvent) -> Hook:
        """Create runtime callable hook."""
        from agentpool.hooks import CallableHook

        return CallableHook(
            event=event,
            fn=self.import_path,
            matcher=self.matcher,
            timeout=self.timeout,
            enabled=self.enabled,
            arguments=self.arguments,
        )


class PromptHookConfig(BaseHookConfig):
    """Hook that uses an LLM to evaluate the action.

    The prompt is sent to a fast LLM which returns a structured decision.
    Use $TOOL_NAME, $TOOL_INPUT, $INPUT placeholders in the prompt.
    """

    model_config = ConfigDict(json_schema_extra={"x-doc-title": "Prompt Hook"})

    type: Literal["prompt"] = Field("prompt", init=False)
    """Prompt-based hook type."""

    prompt: str = Field(
        examples=[
            "Evaluate if this tool call is safe: $INPUT",
            "Check if $TOOL_NAME with input $TOOL_INPUT follows security policies.",
        ],
        title="Evaluation prompt",
    )
    """Prompt template for LLM evaluation. Supports placeholders."""

    model: str | None = Field(
        default=None,
        examples=["openai:gpt-4o-mini", "google-gla:gemini-2.0-flash"],
        title="Model",
    )
    """Model to use for evaluation. Defaults to a fast model if not specified."""

    def get_hook(self, event: HookEvent) -> Hook:
        """Create runtime prompt hook."""
        from agentpool.hooks import PromptHook

        return PromptHook(
            event=event,
            prompt=self.prompt,
            matcher=self.matcher,
            timeout=self.timeout,
            enabled=self.enabled,
            model=self.model,
        )


HookConfig = Annotated[
    CommandHookConfig | CallableHookConfig | PromptHookConfig,
    Field(discriminator="type"),
]
"""Union of all hook configuration types."""


class HooksConfig(Schema):
    """Configuration for agent lifecycle hooks.

    Hooks allow intercepting and customizing agent behavior at key points
    in the execution lifecycle. They can add context, block operations,
    modify inputs, or trigger side effects.

    Currently supported events:
    - pre_run / post_run: Before/after agent.run() processes a prompt
    - pre_tool_use / post_tool_use: Before/after a tool is called
    """

    # Message flow events
    pre_run: list[HookConfig] = Field(
        default_factory=list,
        title="Pre-run hooks",
    )
    """Hooks executed before agent.run() processes a prompt."""

    post_run: list[HookConfig] = Field(
        default_factory=list,
        title="Post-run hooks",
    )
    """Hooks executed after agent.run() completes."""

    # Tool execution events
    pre_tool_use: list[HookConfig] = Field(
        default_factory=list,
        title="Pre-tool-use hooks",
    )
    """Hooks executed before a tool is called. Can block or modify the call."""

    post_tool_use: list[HookConfig] = Field(
        default_factory=list,
        title="Post-tool-use hooks",
    )
    """Hooks executed after a tool completes."""

    def get_agent_hooks(self) -> AgentHooks:
        """Create runtime AgentHooks from this configuration.

        Returns:
            AgentHooks instance with all hooks instantiated.
        """
        from agentpool.hooks import AgentHooks

        return AgentHooks(
            pre_run=[cfg.get_hook("pre_run") for cfg in self.pre_run],
            post_run=[cfg.get_hook("post_run") for cfg in self.post_run],
            pre_tool_use=[cfg.get_hook("pre_tool_use") for cfg in self.pre_tool_use],
            post_tool_use=[cfg.get_hook("post_tool_use") for cfg in self.post_tool_use],
        )


# Import for type checking only - avoid circular imports
if TYPE_CHECKING:
    from agentpool.hooks import AgentHooks
