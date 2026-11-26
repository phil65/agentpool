"""Jinja2 environment configuration."""

from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from schemez import Schema

from agentpool_config import ToolConfig


if TYPE_CHECKING:
    from jinja2 import Template
    from jinjarope import Environment

UndefinedBehaviour = Literal["default", "strict", "debug", "chainable", "lax"]
NewLineType = Literal["\n", "\r\n", "\r"]


class Jinja2EnvironmentConfig(Schema):
    """Configuration for Jinja2 environment.

    See: https://jinja.palletsprojects.com/en/3.1.x/api/#jinja2.Environment
    """

    model_config = ConfigDict(json_schema_extra={"title": "Jinja2 Environment Configuration"})

    block_start_string: str = Field(default="{%", title="Block start string", examples=["{%", "<%"])
    """String denoting the beginning of a block (default: '{%')."""

    block_end_string: str = Field(default="%}", title="Block end string", examples=["%}", "%>"])
    """String denoting the end of a block (default: '%}')."""

    variable_start_string: str = Field(
        default="{{",
        title="Variable start string",
        examples=["{{", "${"],
    )
    """String denoting the beginning of a variable (default: '{{')."""

    variable_end_string: str = Field(
        default="}}",
        title="Variable end string",
        examples=["}}", "}"],
    )
    """String denoting the end of a variable (default: '}}')."""

    comment_start_string: str = Field(
        default="{#",
        title="Comment start string",
        examples=["{#", "<!--"],
    )
    """String denoting the beginning of a comment (default: '{#')."""

    comment_end_string: str = Field(
        default="#}",
        title="Comment end string",
        examples=["#}", "-->"],
    )
    """String denoting the end of a comment (default: '#}')."""

    line_statement_prefix: str | None = Field(
        default=None,
        title="Line statement prefix",
        examples=["#", "%"],
    )
    """Prefix that begins a line-based statement (e.g., '#' for line statements)."""

    line_comment_prefix: str | None = Field(
        default=None,
        title="Line comment prefix",
        examples=["##", "//"],
    )
    """Prefix that begins a line-based comment."""

    trim_blocks: bool = Field(default=False, title="Trim blocks")
    """Remove first newline after a block (affects whitespace control)."""

    lstrip_blocks: bool = Field(default=False, title="Left strip blocks")
    """Remove leading spaces and tabs from the start of a line to a block."""

    newline_sequence: NewLineType = Field(
        default="\n",
        title="Newline sequence",
        examples=["\n", "\r\n", "\r"],
    )
    """Sequence that starts a newline (default: '\n')."""

    keep_trailing_newline: bool = Field(default=False, title="Keep trailing newline")
    """Preserve the trailing newline when rendering templates."""

    extensions: list[str] = Field(
        default_factory=list,
        title="Jinja2 extensions",
        examples=[["jinja2.ext.do", "jinja2.ext.loopcontrols"], ["jinja2.ext.i18n"]],
    )
    """List of Jinja2 extensions to load (e.g., 'jinja2.ext.do')."""

    undefined: UndefinedBehaviour = Field(
        default="default",
        title="Undefined behavior",
        examples=["strict", "debug", "chainable"],
    )
    """Behavior when accessing undefined variables (default, strict, debug, chainable)."""

    filters: list[ToolConfig] = Field(default_factory=list, title="Custom filters")
    """Custom filters as list of tool configurations."""

    tests: list[ToolConfig] = Field(default_factory=list, title="Custom tests")
    """Custom tests as list of tool configurations."""

    globals: dict[str, BaseModel] = Field(default_factory=dict, title="Global variables")
    """Global variables available to all templates."""

    def create_environment(self, *, enable_async: bool = False) -> Environment:
        """Create configured Jinja2 environment.

        Args:
            enable_async: Whether to enable async features
        """
        from jinjarope import Environment

        return Environment(enable_async=enable_async, **self.create_environment_kwargs())

    def create_environment_kwargs(self) -> dict[str, Any]:
        """Convert config to Jinja2 environment kwargs.

        Creates a dictionary of kwargs for jinja2.Environment with proper
        conversion of special values.

        Returns:
            Dict of kwargs for jinja2.Environment constructor

        Raises:
            ValueError: If filter or test imports fail
        """
        # Basic config
        kwargs = self.model_dump(exclude={"filters", "tests"})

        try:
            # Convert filters - use tool name as filter name
            tools = [cfg.get_tool() for cfg in self.filters]
            kwargs["filters"] = {tool.name: tool.get_callable() for tool in tools}
            tools = [cfg.get_tool() for cfg in self.tests]
            kwargs["tests"] = {tool.name: tool.get_callable() for tool in tools}

        except Exception as exc:
            raise ValueError(f"Failed to import Jinja2 filters/tests: {exc}") from exc

        return kwargs


class Jinja2Template(Schema):
    """Template with environment configuration."""

    model_config = ConfigDict(json_schema_extra={"title": "Jinja2 Template Configuration"})

    template: str = Field(
        title="Template string",
        examples=["Hello {{ name }}!", "{% for item in items %}{{ item }}{% endfor %}"],
    )
    """The template string to render."""

    environment: Jinja2EnvironmentConfig = Field(
        default_factory=Jinja2EnvironmentConfig,
        title="Environment configuration",
    )
    """Environment configuration for this template."""

    def get_template(self, *, enable_async: bool = False) -> Template:
        """Get compiled Jinja2 template.

        Args:
            enable_async: Whether to enable async features

        Returns:
            Compiled template ready for rendering
        """
        env = self.environment.create_environment(enable_async=enable_async)
        return env.from_string(self.template)

    def render(self, **kwargs: Any) -> str:
        """Render template with provided variables.

        Args:
            **kwargs: Template variables

        Returns:
            Rendered template string
        """
        template = self.get_template()
        return template.render(**kwargs)

    async def render_async(self, **kwargs: Any) -> str:
        """Render template asynchronously.

        Args:
            **kwargs: Template variables

        Returns:
            Rendered template string
        """
        template = self.get_template(enable_async=True)
        return await template.render_async(**kwargs)
