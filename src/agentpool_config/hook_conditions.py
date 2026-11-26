"""Condition models for hook filtering."""

from typing import Annotated, Any, Literal

from pydantic import ConfigDict, Field
from schemez import Schema


class BaseHookCondition(Schema):
    """Base class for hook conditions."""

    type: str = Field(init=False, title="Condition type")
    """Discriminator for condition types."""

    model_config = ConfigDict(frozen=True)


# === Tool Hook Conditions ===


class ToolNameCondition(BaseHookCondition):
    """Match tool by name pattern."""

    model_config = ConfigDict(json_schema_extra={"title": "Tool Name Condition"})

    type: Literal["tool_name"] = Field("tool_name", init=False)
    """Tool name pattern condition."""

    pattern: str = Field(
        examples=["Bash", "Write|Edit", "mcp__.*", ".*"],
        title="Name pattern",
    )
    """Regex pattern to match tool names."""


class ArgumentCondition(BaseHookCondition):
    """Check tool argument values."""

    model_config = ConfigDict(json_schema_extra={"title": "Argument Condition"})

    type: Literal["argument"] = Field("argument", init=False)
    """Argument value condition."""

    key: str = Field(
        examples=["command", "file_path", "options.force"],
        title="Argument key",
    )
    """Argument name to check. Supports dot notation for nested access."""

    pattern: str | None = Field(
        default=None,
        examples=["rm\\s+-rf", "^/tmp/.*", ".*\\.py$"],
        title="Regex pattern",
    )
    """Regex pattern to match argument value."""

    contains: str | None = Field(
        default=None,
        examples=["sudo", "DROP TABLE", "../"],
        title="Substring match",
    )
    """Substring that must be present in argument value."""

    equals: Any | None = Field(
        default=None,
        examples=[True, "/etc/passwd", 0],
        title="Exact value",
    )
    """Exact value the argument must equal."""

    case_sensitive: bool = Field(default=True, title="Case sensitive")
    """Whether string matching is case-sensitive."""


class ArgumentExistsCondition(BaseHookCondition):
    """Check if an argument is present."""

    model_config = ConfigDict(json_schema_extra={"title": "Argument Exists Condition"})

    type: Literal["argument_exists"] = Field("argument_exists", init=False)
    """Argument existence condition."""

    key: str = Field(
        examples=["force", "recursive", "timeout"],
        title="Argument key",
    )
    """Argument name that must exist (or not exist)."""

    exists: bool = Field(default=True, title="Must exist")
    """If True, argument must exist. If False, argument must not exist."""


class Jinja2HookCondition(BaseHookCondition):
    """Flexible Jinja2 template condition for hooks."""

    model_config = ConfigDict(json_schema_extra={"title": "Jinja2 Hook Condition"})

    type: Literal["jinja2"] = Field("jinja2", init=False)
    """Jinja2 template condition."""

    template: str = Field(
        examples=[
            "{{ tool_name == 'Bash' and 'sudo' in tool_input.get('command', '') }}",
            "{{ tool_input.get('path', '').startswith('/tmp') }}",
            "{{ 'password' in tool_input | string | lower }}",
        ],
        title="Jinja2 template",
    )
    """Jinja2 template that evaluates to true/false.

    Available variables depend on hook event:
    - tool_name: Name of the tool (tool hooks)
    - tool_input: Tool arguments dict (tool hooks)
    - tool_output: Tool result (post_tool_use only)
    - duration_ms: Execution time in ms (post_tool_use only)
    - prompt: User prompt (run hooks)
    - result: Run result (post_run only)
    - agent_name: Name of the agent
    - event: Hook event name
    """


class DurationCondition(BaseHookCondition):
    """Check tool execution duration (post_tool_use only)."""

    model_config = ConfigDict(json_schema_extra={"title": "Duration Condition"})

    type: Literal["duration"] = Field("duration", init=False)
    """Duration threshold condition."""

    min_ms: float | None = Field(
        default=None,
        ge=0,
        examples=[1000, 5000],
        title="Minimum duration (ms)",
    )
    """Minimum duration in milliseconds. Triggers if duration >= min_ms."""

    max_ms: float | None = Field(
        default=None,
        ge=0,
        examples=[100, 500],
        title="Maximum duration (ms)",
    )
    """Maximum duration in milliseconds. Triggers if duration <= max_ms."""


class OutputCondition(BaseHookCondition):
    """Check tool output content (post_tool_use only)."""

    model_config = ConfigDict(json_schema_extra={"title": "Output Condition"})

    type: Literal["output"] = Field("output", init=False)
    """Output content condition."""

    pattern: str | None = Field(
        default=None,
        examples=["error", "Exception", "failed"],
        title="Regex pattern",
    )
    """Regex pattern to match in output."""

    contains: str | None = Field(
        default=None,
        examples=["success", "error", "warning"],
        title="Substring match",
    )
    """Substring that must be present in output."""

    case_sensitive: bool = Field(default=False, title="Case sensitive")
    """Whether string matching is case-sensitive."""


class OutputSizeCondition(BaseHookCondition):
    """Check tool output size (post_tool_use only)."""

    model_config = ConfigDict(json_schema_extra={"title": "Output Size Condition"})

    type: Literal["output_size"] = Field("output_size", init=False)
    """Output size condition."""

    min_chars: int | None = Field(
        default=None,
        ge=0,
        examples=[1000, 10000],
        title="Minimum size",
    )
    """Minimum output length in characters."""

    max_chars: int | None = Field(
        default=None,
        ge=0,
        examples=[100, 500],
        title="Maximum size",
    )
    """Maximum output length in characters."""


# === Run Hook Conditions ===


class PromptCondition(BaseHookCondition):
    """Check prompt content (pre_run/post_run)."""

    model_config = ConfigDict(json_schema_extra={"title": "Prompt Condition"})

    type: Literal["prompt"] = Field("prompt", init=False)
    """Prompt content condition."""

    pattern: str | None = Field(
        default=None,
        examples=["^(help|\\?)$", ".*password.*"],
        title="Regex pattern",
    )
    """Regex pattern to match in prompt."""

    contains: str | None = Field(
        default=None,
        examples=["delete", "remove all"],
        title="Substring match",
    )
    """Substring that must be present in prompt."""

    case_sensitive: bool = Field(default=False, title="Case sensitive")
    """Whether string matching is case-sensitive."""


# === Combinators ===


class AndHookCondition(BaseHookCondition):
    """Require all conditions to be met."""

    model_config = ConfigDict(json_schema_extra={"title": "AND Hook Condition"})

    type: Literal["and"] = Field("and", init=False)
    """AND combinator for conditions."""

    conditions: list[HookCondition] = Field(
        min_length=1,
        title="Conditions",
    )
    """All conditions must be true."""


class OrHookCondition(BaseHookCondition):
    """Require any condition to be met."""

    model_config = ConfigDict(json_schema_extra={"title": "OR Hook Condition"})

    type: Literal["or"] = Field("or", init=False)
    """OR combinator for conditions."""

    conditions: list[HookCondition] = Field(
        min_length=1,
        title="Conditions",
    )
    """At least one condition must be true."""


class NotHookCondition(BaseHookCondition):
    """Negate a condition."""

    model_config = ConfigDict(json_schema_extra={"title": "NOT Hook Condition"})

    type: Literal["not"] = Field("not", init=False)
    """NOT combinator for conditions."""

    condition: HookCondition = Field(title="Condition")
    """Condition to negate."""


# === Union Types ===


# Conditions valid for pre_tool_use
PreToolCondition = Annotated[
    ToolNameCondition
    | ArgumentCondition
    | ArgumentExistsCondition
    | Jinja2HookCondition
    | AndHookCondition
    | OrHookCondition
    | NotHookCondition,
    Field(discriminator="type"),
]

# Conditions valid for post_tool_use (includes duration/output checks)
PostToolCondition = Annotated[
    ToolNameCondition
    | ArgumentCondition
    | ArgumentExistsCondition
    | Jinja2HookCondition
    | DurationCondition
    | OutputCondition
    | OutputSizeCondition
    | AndHookCondition
    | OrHookCondition
    | NotHookCondition,
    Field(discriminator="type"),
]

# Conditions valid for pre_run/post_run
RunCondition = Annotated[
    PromptCondition | Jinja2HookCondition | AndHookCondition | OrHookCondition | NotHookCondition,
    Field(discriminator="type"),
]

# General hook condition (all types)
HookCondition = Annotated[
    ToolNameCondition
    | ArgumentCondition
    | ArgumentExistsCondition
    | Jinja2HookCondition
    | DurationCondition
    | OutputCondition
    | OutputSizeCondition
    | PromptCondition
    | AndHookCondition
    | OrHookCondition
    | NotHookCondition,
    Field(discriminator="type"),
]


# Update forward references for recursive types
AndHookCondition.model_rebuild()
OrHookCondition.model_rebuild()
NotHookCondition.model_rebuild()
