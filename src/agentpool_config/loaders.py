"""Models for resource information."""

from collections.abc import Sequence as TypingSequence
from typing import Annotated, Literal
import warnings

from pydantic import ConfigDict, Field, SecretStr, model_validator
from schemez import Schema
from upathtools import to_upath

from agentpool.common_types import JsonObject
from agentpool.utils.importing import import_callable
from agentpool.utils.signatures import get_forwardref_signature


class BaseResourceLoaderConfig(Schema):
    """Base class for all resource types."""

    type: str = Field(init=False, title="Resource type")
    """Type identifier for this resource."""

    description: str = Field(
        default="",
        title="Resource description",
        examples=["Configuration file", "User data", "API response"],
    )
    """Human-readable description of the resource."""

    uri: str | None = Field(
        default=None,
        title="Resource URI",
        examples=["file:///path/to/file", "https://api.example.com/data"],
    )
    """Canonical URI for this resource, set during registration if unset."""

    # watch: WatchConfig | None = None
    # """Configuration for file system watching, if supported."""

    name: str | None = Field(
        default=None,
        exclude=True,
        title="Resource name",
        examples=["config", "user_data", "api_response"],
    )
    """Technical identifier (automatically set from config key during registration)."""

    model_config = ConfigDict(frozen=True)

    # @property
    # def supports_watching(self) -> bool:
    #     """Whether this resource instance supports watching."""
    #     return False

    # def is_watched(self) -> bool:
    #     """Tell if this resource should be watched."""
    #     return self.supports_watching and self.watch is not None and self.watch.enabled

    def is_templated(self) -> bool:
        """Whether this resource supports URI templates."""
        return False  # Default: resources are static

    @property
    def mime_type(self) -> str:
        """Get the MIME type for this resource.

        This should be overridden by subclasses that can determine
        their MIME type. Default is text/plain.
        """
        return "text/plain"


class PathResourceLoaderConfig(BaseResourceLoaderConfig):
    """Resource loaded from a file or URL."""

    model_config = ConfigDict(json_schema_extra={"x-doc-title": "Path Resource"})

    type: Literal["path"] = Field(default="path", init=False, title="Resource type")
    """Discriminator field identifying this as a path-based resource."""

    path: str = Field(
        title="File path or URL",
        examples=["/path/to/file.txt", "https://example.com/data.json", "config/settings.yml"],
    )
    """Path to the file or URL to load."""

    watch: WatchConfig | None = Field(default=None, title="Watch configuration")
    """Configuration for watching the file for changes."""

    def validate_resource(self) -> list[str]:
        """Check if path exists for local files."""
        warnings = []
        path = to_upath(self.path)
        prefixes = ("http://", "https://")
        if not path.exists() and not path.as_uri().startswith(prefixes):
            warnings.append(f"Resource path not found: {path}")
        return warnings

    @property
    def supports_watching(self) -> bool:
        """Whether this resource instance supports watching."""
        if not to_upath(self.path).exists():
            msg = f"Cannot watch non-existent path: {self.path}"
            warnings.warn(msg, UserWarning, stacklevel=2)
            return False
        return True

    @model_validator(mode="after")
    def validate_path(self) -> PathResourceLoaderConfig:
        """Validate that the path is not empty."""
        if not self.path:
            raise ValueError("Path cannot be empty")
        return self

    def is_templated(self) -> bool:
        """Path resources are templated if they contain placeholders."""
        return "{" in str(self.path)

    @property
    def mime_type(self) -> str:
        """Get MIME type based on file extension."""
        from agentpool.mime_utils import guess_type

        return guess_type(str(self.path)) or "application/octet-stream"


class TextResourceLoaderConfig(BaseResourceLoaderConfig):
    """Raw text resource."""

    model_config = ConfigDict(json_schema_extra={"x-doc-title": "Text Resource"})

    type: Literal["text"] = Field(default="text", init=False, title="Resource type")
    """Discriminator field identifying this as a text-based resource."""

    content: str = Field(
        title="Text content",
        examples=["Hello World", '{ "key": "value" }', "---\nkey: value"],
    )
    """The actual text content of the resource."""

    _mime_type: str | None = None  # Optional override

    @model_validator(mode="after")
    def validate_content(self) -> TextResourceLoaderConfig:
        """Validate that the content is not empty."""
        if not self.content:
            raise ValueError("Content cannot be empty")
        return self

    @property
    def mime_type(self) -> str:
        """Get MIME type, trying to detect JSON/YAML."""
        if self._mime_type:
            return self._mime_type
        # Could add content inspection here
        return "text/plain"


class CLIResourceLoaderConfig(BaseResourceLoaderConfig):
    """Resource from CLI command execution."""

    model_config = ConfigDict(json_schema_extra={"x-doc-title": "CLI Resource"})

    type: Literal["cli"] = Field(default="cli", init=False, title="Resource type")
    """Discriminator field identifying this as a CLI-based resource."""

    command: str | TypingSequence[str] = Field(
        title="Command to execute",
        examples=["ls -la", "git status", ["python", "script.py", "--arg"]],
    )
    """Command to execute (string or sequence of arguments)."""

    shell: bool = Field(default=False, title="Use shell")
    """Whether to run the command through a shell."""

    cwd: str | None = Field(
        default=None,
        title="Working directory",
        examples=["/path/to/project", "~/workspace"],
    )
    """Working directory for command execution."""

    timeout: float | None = Field(
        default=None,
        title="Timeout in seconds",
        examples=[30.0, 60.0, 120.0],
    )
    """Maximum time in seconds to wait for command completion."""

    @model_validator(mode="after")
    def validate_command(self) -> CLIResourceLoaderConfig:
        """Validate command configuration."""
        if not self.command:
            raise ValueError("Command cannot be empty")
        if (
            isinstance(self.command, list | tuple)
            and not self.shell
            and not all(isinstance(part, str) for part in self.command)
        ):
            raise ValueError("When shell=False, all command parts must be strings")
        return self


class RepositoryResource(BaseResourceLoaderConfig):
    """Git repository content."""

    model_config = ConfigDict(json_schema_extra={"x-doc-title": "Repository Resource"})

    type: Literal["repository"] = Field(default="repository", init=False, title="Resource type")
    """Repository resource configuration."""

    repo_url: str = Field(
        title="Repository URL",
        examples=["https://github.com/user/repo.git", "git@github.com:user/repo.git"],
    )
    """URL of the git repository."""

    ref: str = Field(
        default="main",
        title="Git reference",
        examples=["main", "develop", "v1.0.0", "abc123def"],
    )
    """Git reference (branch, tag, or commit)."""

    path: str = Field(
        default="",
        title="Repository path",
        examples=["", "src/", "docs/README.md"],
    )
    """Path within the repository."""

    sparse_checkout: list[str] | None = Field(
        default=None,
        title="Sparse checkout paths",
        examples=[["src/", "docs/"], ["*.py", "requirements.txt"]],
    )
    """Optional list of paths for sparse checkout."""

    user: str | None = Field(
        default=None,
        title="Username",
        examples=["github_user", "git_username"],
    )
    """Optional user name for authentication."""

    password: SecretStr | None = Field(default=None, title="Password")
    """Optional password for authentication."""

    def validate_resource(self) -> list[str]:
        warnings = []
        if self.user and not self.password:
            warnings.append(f"Repository {self.repo_url} has user but no password")
        return warnings


class SourceResourceLoaderConfig(BaseResourceLoaderConfig):
    """Resource from Python source code."""

    model_config = ConfigDict(json_schema_extra={"x-doc-title": "Source Resource"})

    type: Literal["source"] = Field(default="source", init=False, title="Resource type")
    """Source code resource configuration."""

    import_path: str = Field(
        title="Import path",
        examples=["mypackage.module", "utils.helpers", "app.models.User"],
    )
    """Dotted import path to the Python module or object."""

    recursive: bool = Field(default=False, title="Include recursively")
    """Whether to include submodules recursively."""

    include_tests: bool = Field(default=False, title="Include tests")
    """Whether to include test files and directories."""

    @model_validator(mode="after")
    def validate_import_path(self) -> SourceResourceLoaderConfig:
        """Validate that the import path is properly formatted."""
        if not all(part.isidentifier() for part in self.import_path.split(".")):
            raise ValueError(f"Invalid import path: {self.import_path}")
        return self


class CallableResourceLoaderConfig(BaseResourceLoaderConfig):
    """Resource from executing a Python callable."""

    model_config = ConfigDict(json_schema_extra={"x-doc-title": "Callable Resource"})

    type: Literal["callable"] = Field(default="callable", init=False, title="Resource type")
    """Callable-based resource configuration."""

    import_path: str = Field(
        title="Callable import path",
        examples=["mymodule.get_data", "utils.generators.create_content"],
    )
    """Dotted import path to the callable to execute."""

    keyword_args: JsonObject = Field(default_factory=dict, title="Keyword arguments")
    """Keyword arguments to pass to the callable."""

    @model_validator(mode="after")
    def validate_import_path(self) -> CallableResourceLoaderConfig:
        """Validate that the import path is properly formatted."""
        if not all(part.isidentifier() for part in self.import_path.split(".")):
            raise ValueError(f"Invalid import path: {self.import_path}")
        return self

    def is_templated(self) -> bool:
        """Callable resources are templated if they take parameters."""
        fn = import_callable(self.import_path)
        sig = get_forwardref_signature(fn)
        return bool(sig.parameters)


Resource = Annotated[
    PathResourceLoaderConfig
    | TextResourceLoaderConfig
    | CLIResourceLoaderConfig
    | SourceResourceLoaderConfig
    | CallableResourceLoaderConfig,
    Field(discriminator="type"),
]


class WatchConfig(Schema):
    """Watch configuration for resources."""

    enabled: bool = Field(default=False, title="Watch enabled")
    """Whether the watch is enabled"""

    patterns: list[str] | None = Field(
        default=None,
        title="Watch patterns",
        examples=[["*.py", "*.yml"], ["src/**", "!**/__pycache__"]],
    )
    """List of pathspec patterns (.gitignore style)"""

    ignore_file: str | None = Field(
        default=None,
        title="Ignore file path",
        examples=[".gitignore", ".watchignore"],
    )
    """Path to .gitignore-style file"""
