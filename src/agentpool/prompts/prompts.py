"""Prompt models for AgentPool."""

from collections.abc import Awaitable, Callable
from dataclasses import field
import inspect
import os
from typing import TYPE_CHECKING, Annotated, Any, Literal, Self, assert_never

from pydantic import ConfigDict, Field
from pydantic_ai import BinaryContent, ImageUrl, SystemPromptPart, UserPromptPart
from schemez import Schema
from slashed import Command
from upathtools import UPath, to_upath

from agentpool.log import get_logger
from agentpool.mcp_server import MCPClient
from agentpool.utils import importing
from agentpool.utils.inspection import execute
from agentpool.utils.signatures import get_forwardref_signature


if TYPE_CHECKING:
    from fastmcp.prompts import Prompt as FastMCPPrompt
    from fastmcp.prompts.function_prompt import FunctionPrompt
    from mcp.types import Prompt as MCPPrompt, PromptArgument
    from pydantic_ai import ModelRequestPart
    from slashed import CommandContext

    from agentpool.agents.staged_content import StagedContent

logger = get_logger(__name__)

MessageContentType = Literal["text", "resource", "image_url", "image_base64"]
# Our internal role type (could include more roles)
MessageRole = Literal["system", "user", "assistant", "tool"]


class MessageContent(Schema):
    """Content item in a message."""

    type: MessageContentType
    content: str  # The actual content (text/uri/url/base64)
    alt_text: str | None = None  # For images or resource descriptions

    def to_pydantic(self) -> UserPromptPart:
        """Convert MessageContent to Pydantic model."""
        if self.type == "text":
            return UserPromptPart(self.content)
        if self.type == "image_url":
            return UserPromptPart([ImageUrl(self.content)])
        if self.type == "image_base64":
            bin_content = BinaryContent(self.content.encode(), media_type="image/jpeg")
            return UserPromptPart([bin_content])

        raise ValueError("Unsupported content type")


class PromptParameter(Schema):
    """Prompt argument with validation information."""

    name: str
    """Name of the argument as used in the prompt."""

    description: str | None = None
    """Human-readable description of the argument."""

    required: bool = False
    """Whether this argument must be provided when formatting the prompt."""

    default: Any | None = None
    """Default value if argument is optional."""

    def to_mcp_argument(self) -> PromptArgument:
        """Convert to MCP PromptArgument."""
        from mcp.types import PromptArgument

        return PromptArgument(name=self.name, description=self.description, required=self.required)


class PromptMessage(Schema):
    """A message in a prompt template."""

    role: MessageRole
    content: str | MessageContent | list[MessageContent] = ""

    def get_text_content(self) -> str:
        """Get text content of message."""
        match self.content:
            case str():
                return self.content
            case MessageContent() if self.content.type == "text":
                return self.content.content
            case list() if self.content:
                # Join text content items with space
                text_items = [
                    item.content
                    for item in self.content
                    if isinstance(item, MessageContent) and item.type == "text"
                ]
                return " ".join(text_items) if text_items else ""
            case _:
                return ""

    def to_pydantic_parts(self) -> list[ModelRequestPart]:
        match self.role:
            case "system":
                return [SystemPromptPart(str(self.content))]
            case "user":
                match self.content:
                    case str():
                        return [UserPromptPart(self.content)]
                    case MessageContent():
                        return [self.content.to_pydantic()]
                    case list():
                        return [i.to_pydantic() for i in self.content]
        return []


class BasePrompt(Schema):
    """Base class for all prompts."""

    name: str | None = Field(None, exclude=True)
    """Technical identifier (automatically set from config key during registration)."""

    description: str
    """Human-readable description of what this prompt does."""

    title: str | None = None
    """Title of the prompt."""

    metadata: dict[str, Any] = Field(default_factory=dict)
    """Additional metadata for storing custom prompt information."""
    # messages: list[PromptMessage]

    async def format(self, arguments: dict[str, Any] | None = None) -> list[PromptMessage]:
        """Format this prompt with given arguments.

        Args:
            arguments: Optional argument values

        Returns:
            List of formatted messages

        Raises:
            ValueError: If required arguments are missing
        """
        raise NotImplementedError

    def to_mcp_prompt(self) -> MCPPrompt:
        """Convert to MCP Prompt."""
        raise NotImplementedError


class StaticPrompt(BasePrompt):
    """Static prompt defined by message list."""

    messages: list[PromptMessage]
    """List of messages that make up this prompt."""

    type: Literal["text"] = Field("text", init=False)
    """Discriminator field identifying this as a static text prompt."""

    arguments: list[PromptParameter] = Field(default_factory=list)
    """List of arguments that this prompt accepts."""

    def validate_arguments(self, provided: dict[str, Any]) -> None:
        """Validate that required arguments are provided."""
        required = {arg.name for arg in self.arguments if arg.required}
        missing = required - set(provided)
        if missing:
            raise ValueError(f"Missing required arguments: {', '.join(missing)}")

    def to_mcp_prompt(self) -> MCPPrompt:
        """Convert to MCP Prompt."""
        from mcp.types import Prompt as MCPPrompt

        if self.name is None:
            raise ValueError("Prompt name not set. This should be set during registration.")
        args = [arg.to_mcp_argument() for arg in self.arguments]
        return MCPPrompt(name=self.name, description=self.description, arguments=args)

    def to_fastmcp_prompt(self) -> FastMCPPrompt:
        from fastmcp.prompts import (
            Prompt as FastMCPPrompt,
            PromptArgument as FastMCPArgument,
        )

        params = [
            FastMCPArgument(name=p.name, description=p.description, required=p.required)
            for p in self.arguments
        ]
        return FastMCPPrompt(
            name=self.name or "",
            title=self.title,
            description=self.description,
            arguments=params,
            icons=None,
            tags=set(),
        )

    async def format(self, arguments: dict[str, Any] | None = None) -> list[PromptMessage]:
        """Format static prompt messages with arguments."""
        args = arguments or {}
        self.validate_arguments(args)

        # Add default values for optional arguments
        for arg in self.arguments:
            if arg.name not in args and not arg.required:
                args[arg.name] = arg.default if arg.default is not None else ""

        # Format all messages
        formatted_messages = []
        for msg in self.messages:
            match msg.content:
                case str():
                    content: MessageContent | list[MessageContent] = MessageContent(
                        type="text", content=msg.content.format(**args)
                    )
                case MessageContent() if msg.content.type == "text":
                    msg_content = msg.content.content.format(**args)
                    content = MessageContent(type="text", content=msg_content)
                case list():
                    content = [
                        MessageContent(
                            type=item.type,
                            content=item.content.format(**args)
                            if item.type == "text"
                            else item.content,
                            alt_text=item.alt_text,
                        )
                        for item in msg.content
                        if isinstance(item, MessageContent)
                    ]
                case _:
                    content = msg.content

            formatted_messages.append(PromptMessage(role=msg.role, content=content))

        return formatted_messages


class DynamicPrompt(BasePrompt):
    """Dynamic prompt loaded from callable."""

    import_path: str | Callable[..., str | Awaitable[str]]
    """Dotted import path to the callable that generates the prompt."""

    template: str | None = None
    """Optional template string for formatting the callable's output."""

    type: Literal["function"] = Field("function", init=False)
    """Discriminator field identifying this as a function-based prompt."""

    @property
    def messages(self) -> list[PromptMessage]:
        """Get the template messages for this prompt.

        Note: These are template messages - actual content will be populated
        during format() when the callable is executed.
        """
        template = self.template or "{result}"
        sys_content = MessageContent(type="text", content=f"Content from {self.name}:")
        user_content = MessageContent(type="text", content=template)
        return [
            PromptMessage(role="system", content=sys_content),
            PromptMessage(role="user", content=user_content),
        ]

    def to_fastmcp_prompt(self) -> FunctionPrompt:
        from fastmcp.prompts.function_prompt import FunctionPrompt

        return FunctionPrompt.from_function(self.fn, title=self.title, description=self.description)

    @property
    def fn(self) -> Callable[..., str | Awaitable[str]]:
        if isinstance(self.import_path, str):
            return importing.import_callable(self.import_path)
        return self.import_path

    async def format(self, arguments: dict[str, Any] | None = None) -> list[PromptMessage]:
        """Format this prompt with given arguments."""
        args = arguments or {}
        try:
            result: Any = await execute(self.fn, **args)
            template = self.template or "{result}"
            msg = template.format(result=result)
            content = MessageContent(type="text", content=msg)
            msg = f"Content from {self.name}:"
            sys_content = MessageContent(type="text", content=msg)
            return [
                PromptMessage(role="system", content=sys_content),
                PromptMessage(role="user", content=content),
            ]
        except Exception as exc:
            raise ValueError(f"Failed to execute prompt callable: {exc}") from exc

    @classmethod
    def from_callable(
        cls,
        fn: Callable[..., Any] | str,
        *,
        name_override: str | None = None,
        description_override: str | None = None,
        template_override: str | None = None,
    ) -> DynamicPrompt:
        """Create a prompt from a callable.

        Args:
            fn: Function or import path to create prompt from
            name_override: Optional override for prompt name
            description_override: Optional override for prompt description
            template_override: Optional override for message template

        Returns:
            DynamicPrompt instance

        Raises:
            ValueError: If callable cannot be imported or is invalid
        """
        from docstring_parser import parse as parse_docstring

        if isinstance(fn, str):
            fn = importing.import_callable(fn)

        name = name_override or getattr(fn, "__name__", "unknown")
        if docstring := inspect.getdoc(fn):
            parsed = parse_docstring(docstring)
            description = description_override or parsed.short_description
        else:
            description = description_override or f"Prompt from {name}"

        from agentpool.utils.inspection import get_fn_qualname

        path = f"{fn.__module__}.{get_fn_qualname(fn)}"
        return cls(
            name=name,
            description=description or "",
            import_path=path,
            template=template_override,
            metadata={"source": "function", "import_path": path},
        )

    def to_mcp_prompt(self) -> MCPPrompt:
        """Convert to MCP Prompt."""
        from docstring_parser import parse as parse_docstring
        from mcp.types import Prompt as MCPPrompt

        sig = get_forwardref_signature(self.fn)
        # hints = get_type_hints(self.fn, include_extras=True, localns=locals())
        if docstring := inspect.getdoc(self.fn):
            parsed = parse_docstring(docstring)
            # Create mapping of param names to descriptions
            arg_docs = {
                param.arg_name: param.description
                for param in parsed.params
                if param.arg_name and param.description
            }
        else:
            arg_docs = {}

        # Create arguments
        arguments = []
        for param_name, param in sig.parameters.items():
            if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
                continue

            required = param.default == param.empty
            arg = PromptParameter(
                name=param_name,
                description=arg_docs.get(param_name),
                required=required,
                default=None if param.default is param.empty else param.default,
            )
            arguments.append(arg)

        if self.name is None:
            msg = "Prompt name not set. This should be set during registration."
            raise ValueError(msg)
        args = [arg.to_mcp_argument() for arg in arguments]
        return MCPPrompt(name=self.name, description=self.description, arguments=args)


class MCPClientPrompt(BasePrompt):
    """A prompt that can be rendered from an MCP server."""

    client: MCPClient
    """The client used to render the prompt."""

    name: str
    """The name of the prompt."""

    arguments: list[dict[str, Any]] = field(default_factory=list)
    """A list of arguments for the prompt."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def to_mcp_prompt(self) -> MCPPrompt:
        """Convert to MCP Prompt."""
        from mcp.types import Prompt as MCPPrompt, PromptArgument

        args = [
            PromptArgument(name=i["name"], description=i["description"], required=i["required"])
            for i in self.arguments
        ]
        return MCPPrompt(name=self.name, description=self.description, arguments=args)

    @classmethod
    def from_fastmcp(cls, client: MCPClient, prompt: MCPPrompt) -> Self:
        """Convert MCP prompt to our Prompt class."""
        arguments = [
            {
                "name": arg.name,
                "description": arg.description,
                "required": arg.required or False,
            }
            for arg in prompt.arguments or []
        ]

        return cls(
            name=prompt.name,
            description=prompt.description or "",
            arguments=arguments,
            client=client,
        )

    def __repr__(self) -> str:
        return f"Prompt(name={self.name!r}, description={self.description!r})"

    async def get_components(
        self, arguments: dict[str, str] | None = None
    ) -> list[SystemPromptPart | UserPromptPart]:
        """Get prompt as pydantic-ai message components.

        Args:
            arguments: Arguments to pass to the prompt template

        Returns:
            List of message parts ready for agent usage

        Raises:
            RuntimeError: If prompt fetch fails
            ValueError: If prompt contains unsupported message types
        """
        from agentpool.mcp_server.conversions import content_block_as_text

        try:
            result = await self.client.get_prompt(self.name, arguments)
        except Exception as e:
            msg = f"Failed to get prompt {self.name!r}: {e}"
            raise RuntimeError(msg) from e

        # Convert MCP messages to pydantic-ai parts
        parts: list[SystemPromptPart | UserPromptPart] = []
        for message in result.messages:
            # Extract text content from MCP message
            text_content = content_block_as_text(message.content)
            # Convert based on role
            match message.role:
                case "user":
                    parts.append(UserPromptPart(content=text_content))
                case "assistant":
                    # Convert assistant messages to user parts for context
                    parts.append(UserPromptPart(content=f"Assistant: {text_content}"))
                case _ as unreachable:
                    assert_never(unreachable)

        if not parts:
            msg = f"No supported message parts found in prompt {self.name!r}"
            raise ValueError(msg)

        return parts

    def create_mcp_command(self, staged_content: StagedContent) -> Command:
        """Convert MCP prompt to slashed Command.

        Args:
            staged_content: Staged content to add components to

        Returns:
            Slashed Command that executes the prompt
        """

        async def execute_prompt(
            ctx: CommandContext[Any],
            args: list[str],
            kwargs: dict[str, str],
        ) -> None:
            """Execute the MCP prompt with parsed arguments."""
            # Map parsed args to prompt parameters
            # Map positional args to prompt parameter names
            result = {
                self.arguments[i]["name"]: arg_value
                for i, arg_value in enumerate(args)
                if i < len(self.arguments)
            } | kwargs
            try:
                components = await self.get_components(result or None)
                staged_content.add(components)
                staged_count = len(staged_content)
                await ctx.print(f"✅ Prompt {self.name!r} staged ({staged_count} total parts)")
            except Exception as e:
                logger.exception("MCP prompt execution failed", prompt=self.name)
                await ctx.print(f"❌ Prompt error: {e}")

        usage = " ".join(f"<{i['name']}>" for i in args) if (args := self.arguments) else None
        return Command.from_raw(
            execute_prompt,
            name=self.name,
            description=self.description or f"MCP prompt: {self.name}",
            category="mcp",
            usage=usage,
        )


class FilePrompt(BasePrompt):
    """Prompt loaded from a file.

    This type of prompt loads its content from a file, allowing for longer or more
    complex prompts to be managed in separate files. The file content is loaded
    and parsed according to the specified format.
    """

    path: str | os.PathLike[str] | UPath
    """Path to the file containing the prompt content."""

    fmt: Literal["text", "markdown", "jinja2"] = Field("text", alias="format")
    """Format of the file content (text, markdown, or jinja2 template)."""

    type: Literal["file"] = Field("file", init=False)
    """Discriminator field identifying this as a file-based prompt."""

    watch: bool = False
    """Whether to watch the file for changes and reload automatically."""

    def to_mcp_prompt(self) -> MCPPrompt:
        """Convert to MCP Prompt."""
        from mcp.types import Prompt as MCPPrompt

        return MCPPrompt(
            name=self.name or to_upath(self.path).name,
            description=self.description,
        )

    @property
    def messages(self) -> list[PromptMessage]:
        """Get messages from file content."""
        content = to_upath(self.path).read_text("utf-8")
        match self.fmt:
            case "text":
                # Simple text format - whole file as user message
                msg = MessageContent(type="text", content=content)
            case "markdown":
                # TODO: Parse markdown sections into separate messages
                msg = MessageContent(type="text", content=content)
            case "jinja2":
                # Raw template - will be formatted during format()
                msg = MessageContent(type="text", content=content)
            case _ as unreachable:
                assert_never(unreachable)
        return [PromptMessage(role="user", content=msg)]

    async def format(self, arguments: dict[str, Any] | None = None) -> list[PromptMessage]:
        """Format the file content with arguments."""
        args = arguments or {}
        content = to_upath(self.path).read_text("utf-8")

        if self.fmt == "jinja2":
            # Use jinja2 for template formatting
            import jinja2

            env = jinja2.Environment(autoescape=True, enable_async=True)
            template = env.from_string(content)
            content = await template.render_async(**args)
        else:
            # Use simple string formatting
            try:
                content = content.format(**args)
            except KeyError as exc:
                raise ValueError(f"Missing argument in template: {exc}") from exc
        msg_content = MessageContent(type="text", content=content)
        return [PromptMessage(role="user", content=msg_content)]


# Type to use in configuration
PromptType = Annotated[StaticPrompt | DynamicPrompt | FilePrompt, Field(discriminator="type")]


if __name__ == "__main__":

    def prompt_fn() -> str:
        return "hello"

    async def main() -> None:
        prompt = DynamicPrompt(import_path=prompt_fn, name="test", description="test")
        result = await prompt.format()
        print(result)

    import anyio

    anyio.run(main)
