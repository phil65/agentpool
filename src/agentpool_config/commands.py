"""Command configuration for prompt shortcuts."""

from collections.abc import Callable
import inspect
from pathlib import Path
import re
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import ConfigDict, Field, ImportString
from schemez import Schema


if TYPE_CHECKING:
    from slashed import Command


class BaseCommandConfig(Schema):
    """Base configuration for commands."""

    model_config = ConfigDict(json_schema_extra={"title": "Base Command"})

    type: str = Field(title="Command type")
    """Type discriminator for command configurations."""

    name: str | None = Field(
        default=None,
        examples=["summarize", "code_review", "translate"],
        title="Command name",
    )
    """Command name (optional, can be inferred from key)."""

    description: str | None = Field(
        default=None,
        examples=["Summarize the given text", "Review code for issues"],
        title="Command description",
    )
    """Optional description of what this command does."""

    def get_callable(self) -> Callable[..., str]:
        """Generate a callable function from the command configuration.

        Returns:
            A function with proper signature that can be introspected by libraries
            like slashed that examine Python callables.
        """
        raise NotImplementedError

    def get_slashed_command(self, category: str = "manifest") -> Command:
        """Create a slashed Command from this configuration.

        Args:
            category: Category to assign to the command

        Returns:
            A slashed Command instance ready for registration
        """
        from slashed import Command

        func = self.get_callable()
        return Command(
            func,
            name=self.name,
            description=self.description,
            category=category,
        )


class StaticCommandConfig(BaseCommandConfig):
    """Static command with inline content."""

    model_config = ConfigDict(json_schema_extra={"title": "Static Command"})

    type: Literal["static"] = Field("static", init=False)
    """Static command configuration."""

    content: str = Field(
        examples=[
            "Summarize this text: {text}",
            "Translate {text} to {language}",
            "Review this code: {code}",
        ],
        title="Template content",
    )
    """The prompt template content. Supports ${env.VAR} substitution."""

    def get_callable(self) -> Callable[..., str]:
        """Generate a callable function from the static command content.

        Parses {param} placeholders in content to create function parameters.

        Returns:
            A function with signature matching the template parameters
        """
        # Extract parameter names from {param} placeholders
        param_names = list(set(re.findall(r"\{(\w+)\}", self.content)))
        param_names.sort()  # Consistent ordering

        # Create function that does template substitution
        def command_func(*args: Any, **kwargs: Any) -> str:
            """Generated command function."""
            # Build substitution dict from args and kwargs
            substitutions = {}
            for i, name in enumerate(param_names):
                if i < len(args):
                    substitutions[name] = args[i]
                elif name in kwargs:
                    substitutions[name] = kwargs[name]
                else:
                    substitutions[name] = ""  # Default empty string

            # Substitute into template
            return self.content.format(**substitutions)

        # Create proper signature
        parameters = [
            inspect.Parameter(
                name, inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=str, default=""
            )
            for name in param_names
        ]
        signature = inspect.Signature(parameters, return_annotation=str)
        # Set function metadata
        command_func.__name__ = self.name or "unnamed_command"
        command_func.__doc__ = self.description or self.content
        command_func.__signature__ = signature  # type: ignore

        return command_func


class CallableCommandConfig(BaseCommandConfig):
    """Callable command that references a Python function."""

    model_config = ConfigDict(json_schema_extra={"title": "Callable Command"})

    type: Literal["callable"] = Field("callable", init=False)
    """Callable command configuration."""

    function: ImportString[Callable[..., Any]] = Field(
        examples=[
            "mymodule.commands.summarize",
            "utils.prompts:code_review",
            "external.tools:translate_text",
        ],
        title="Function import path",
    )
    """Python function import path (e.g., 'my.package.module.function_name')."""

    def get_callable(self) -> Callable[..., str]:
        """Return the imported function directly.

        Returns:
            The imported Python function

        Raises:
            ImportError: If the function cannot be imported
            TypeError: If the imported object is not callable
        """
        func = self.function
        if not callable(func):
            raise TypeError(f"Imported object {self.function} is not callable")

        # Set name if provided and function has __name__
        if self.name and hasattr(func, "__name__"):
            func.__name__ = self.name  # ty: ignore

        # Set description as docstring if provided
        if self.description and not func.__doc__:
            func.__doc__ = self.description
        elif self.description:
            # Prepend description to existing docstring
            existing_doc = func.__doc__ or ""
            func.__doc__ = f"{self.description}\n\n{existing_doc}".strip()

        return func


class FileCommandConfig(BaseCommandConfig):
    """File-based command that loads content from external file."""

    model_config = ConfigDict(json_schema_extra={"title": "File-based Command"})

    type: Literal["file"] = Field("file", init=False)
    """File-based command configuration."""

    path: str = Field(
        examples=[
            "prompts/summarize.txt",
            "/templates/code_review.j2",
            "commands/translate.md",
        ],
        title="Template file path",
    )
    """Path to file containing the prompt template."""

    encoding: str = Field(
        default="utf-8",
        examples=["utf-8", "ascii", "latin1"],
        title="File encoding",
    )
    """File encoding to use when reading the file."""

    def get_callable(self) -> Callable[..., str]:
        """Generate a callable function from the file-based command.

        Loads content from file and creates function with parameters
        based on {param} placeholders in the file content.

        Returns:
            A function with signature matching the template parameters

        Raises:
            FileNotFoundError: If the specified file doesn't exist
            UnicodeDecodeError: If file cannot be decoded with specified encoding
        """
        file_path = Path(self.path)
        content = file_path.read_text(encoding=self.encoding)
        # Extract parameter names from {param} placeholders
        param_names = list(set(re.findall(r"\{(\w+)\}", content)))
        param_names.sort()  # Consistent ordering

        # Create function that does template substitution
        def command_func(*args: Any, **kwargs: Any) -> str:
            """Generated command function from file."""
            # Build substitution dict from args and kwargs
            substitutions = {}
            for i, name in enumerate(param_names):
                if i < len(args):
                    substitutions[name] = args[i]
                elif name in kwargs:
                    substitutions[name] = kwargs[name]
                else:
                    substitutions[name] = ""  # Default empty string

            # Substitute into template
            return content.format(**substitutions)

        # Create proper signature
        parameters = [
            inspect.Parameter(
                name, inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=str, default=""
            )
            for name in param_names
        ]
        signature = inspect.Signature(parameters, return_annotation=str)

        # Set function metadata
        command_func.__name__ = self.name or "unnamed_command"
        command_func.__doc__ = self.description or f"Command from {self.path}"
        command_func.__signature__ = signature  # type: ignore

        return command_func


CommandConfig = Annotated[
    StaticCommandConfig | FileCommandConfig | CallableCommandConfig,
    Field(discriminator="type"),
]
