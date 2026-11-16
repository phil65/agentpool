"""Models for PydanticAI builtin tools configuration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import ConfigDict, Field
from pydantic_ai import (
    CodeExecutionTool,
    FileSearchTool,
    ImageGenerationTool,
    MCPServerTool,
    MemoryTool,
    WebFetchTool,
    WebSearchTool,
)
from pydantic_ai.native_tools import ImageAspectRatio, WebSearchUserLocation  # noqa: TC002

from agentpool_config.tools import BaseToolConfig


if TYPE_CHECKING:
    from pydantic_ai.native_tools import AbstractNativeTool


class BaseBuiltinToolConfig(BaseToolConfig):
    """Base configuration for PydanticAI builtin tools."""

    type: Literal["builtin"] = Field("builtin", init=False)
    """Top-level discriminator - always 'builtin' for builtin tools."""

    builtin_type: str = Field(init=False)
    """Sub-discriminator for specific builtin tool type."""

    def get_builtin_tool(self) -> AbstractNativeTool:
        """Convert config to PydanticAI builtin tool instance."""
        raise NotImplementedError


class WebSearchToolConfig(BaseBuiltinToolConfig):
    """Configuration for PydanticAI web search builtin tool.

    Example:
        ```yaml
        tools:
          - type: builtin
            builtin_type: web_search
            search_context_size: high
            blocked_domains: ["spam.com"]
        ```
    """

    model_config = ConfigDict(title="Web Search Tool")

    builtin_type: Literal["web_search"] = Field("web_search", init=False)
    """Web search builtin tool."""

    search_context_size: Literal["low", "medium", "high"] = Field(
        default="medium",
        examples=["low", "medium", "high"],
        title="Search context size",
    )
    """The search context size parameter controls how much context is retrieved."""

    user_location: WebSearchUserLocation | None = Field(default=None, title="User location")
    """User location for localizing search results (city, country, region, timezone)."""

    blocked_domains: list[str] | None = Field(
        default=None,
        examples=[["spam.com", "ads.example.com"], ["blocked.site"]],
        title="Blocked domains",
    )
    """Domains that will never appear in results."""

    allowed_domains: list[str] | None = Field(
        default=None,
        examples=[["wikipedia.org", "github.com"], ["trusted.site"]],
        title="Allowed domains",
    )
    """Only these domains will be included in results."""

    max_uses: int | None = Field(default=None, examples=[5, 10, 20], title="Maximum uses")
    """Maximum number of times the tool can be used."""

    def get_builtin_tool(self) -> WebSearchTool:
        """Convert config to WebSearchTool instance."""
        return WebSearchTool(
            search_context_size=self.search_context_size,
            user_location=self.user_location,
            blocked_domains=self.blocked_domains,
            allowed_domains=self.allowed_domains,
            max_uses=self.max_uses,
        )


class CodeExecutionToolConfig(BaseBuiltinToolConfig):
    """Configuration for PydanticAI code execution builtin tool.

    Example:
        ```yaml
        tools:
          - type: builtin
            builtin_type: code_execution
        ```
    """

    model_config = ConfigDict(title="Code Execution Tool")

    builtin_type: Literal["code_execution"] = Field("code_execution", init=False)
    """Code execution builtin tool."""

    def get_builtin_tool(self) -> CodeExecutionTool:
        """Convert config to CodeExecutionTool instance."""
        return CodeExecutionTool()


class WebFetchToolConfig(BaseBuiltinToolConfig):
    """Configuration for PydanticAI web fetch builtin tool.

    Example:
        ```yaml
        tools:
          - type: builtin
            builtin_type: web_fetch
        ```
    """

    model_config = ConfigDict(title="Web Fetch Tool")

    builtin_type: Literal["web_fetch"] = Field("web_fetch", init=False)
    """Web fetch builtin tool."""

    max_uses: int | None = Field(default=None, examples=[5, 10, 20], title="Maximum uses")
    """Maximum number of times the tool can be used."""

    allowed_domains: list[str] | None = Field(
        default=None,
        examples=[["wikipedia.org", "github.com"]],
        title="Allowed domains",
    )
    """If provided, only these domains will be fetched."""

    blocked_domains: list[str] | None = Field(
        default=None,
        examples=[["spam.com", "ads.example.com"]],
        title="Blocked domains",
    )
    """If provided, these domains will never be fetched."""

    enable_citations: bool = Field(default=False, title="Enable citations")
    """If True, enables citations for fetched content."""

    max_content_tokens: int | None = Field(
        default=None,
        examples=[1000, 5000],
        title="Max content tokens",
    )
    """Maximum content length in tokens for fetched content."""

    def get_builtin_tool(self) -> WebFetchTool:
        """Convert config to WebFetchTool instance."""
        return WebFetchTool(
            max_uses=self.max_uses,
            allowed_domains=self.allowed_domains,
            blocked_domains=self.blocked_domains,
            enable_citations=self.enable_citations,
            max_content_tokens=self.max_content_tokens,
        )


class ImageGenerationToolConfig(BaseBuiltinToolConfig):
    """Configuration for PydanticAI image generation builtin tool.

    Example:
        ```yaml
        tools:
          - type: builtin
            builtin_type: image_generation
            quality: high
            size: 1024x1024
        ```
    """

    model_config = ConfigDict(title="Image Generation Tool")

    builtin_type: Literal["image_generation"] = Field("image_generation", init=False)
    """Image generation builtin tool."""

    background: Literal["transparent", "opaque", "auto"] = Field(
        default="auto",
        examples=["transparent", "opaque", "auto"],
        title="Background type",
    )
    """Background type for the generated image."""

    input_fidelity: Literal["high", "low"] | None = Field(
        default=None,
        examples=["high", "low"],
        title="Input fidelity",
    )
    """Control how much effort the model will exert to match input image features."""

    moderation: Literal["auto", "low"] = Field(
        default="auto",
        examples=["auto", "low"],
        title="Moderation level",
    )
    """Moderation level for the generated image."""

    output_compression: int | None = Field(
        default=None,
        ge=0,
        le=100,
        examples=[75, 90, 100],
        title="Output compression",
    )
    """Compression level for the output image."""

    output_format: Literal["png", "webp", "jpeg"] | None = Field(
        default=None,
        examples=["png", "webp", "jpeg"],
        title="Output format",
    )
    """The output format of the generated image."""

    partial_images: int = Field(
        default=0,
        ge=0,
        examples=[0, 2, 4],
        title="Partial images count",
    )
    """Number of partial images to generate in streaming mode."""

    quality: Literal["low", "medium", "high", "auto"] = Field(
        default="auto",
        examples=["low", "medium", "high", "auto"],
        title="Image quality",
    )
    """The quality of the generated image."""

    size: Literal["auto", "1024x1024", "1024x1536", "1536x1024", "1K", "2K", "4K"] | None = Field(
        default=None,
        examples=["auto", "1024x1024", "1024x1536", "1536x1024", "1K", "2K", "4K"],
        title="Image size",
    )
    """The size of the generated image.

    OpenAI: 'auto', '1024x1024', '1024x1536', '1536x1024'.
    Google (Gemini): '1K', '2K', '4K'.
    """

    aspect_ratio: ImageAspectRatio | None = Field(
        default=None,
        examples=["1:1", "16:9", "3:2"],
        title="Aspect ratio",
    )
    """The aspect ratio for generated images. Supported by Google and OpenAI."""

    def get_builtin_tool(self) -> ImageGenerationTool:
        """Convert config to ImageGenerationTool instance."""
        return ImageGenerationTool(
            background=self.background,
            input_fidelity=self.input_fidelity,
            moderation=self.moderation,
            output_compression=self.output_compression,
            output_format=self.output_format,
            partial_images=self.partial_images,
            quality=self.quality,
            size=self.size,
            aspect_ratio=self.aspect_ratio,
        )


class MemoryToolConfig(BaseBuiltinToolConfig):
    """Configuration for PydanticAI memory builtin tool.

    Example:
        ```yaml
        tools:
          - type: builtin
            builtin_type: memory
        ```
    """

    model_config = ConfigDict(title="Memory Tool")

    builtin_type: Literal["memory"] = Field("memory", init=False)
    """Memory builtin tool."""

    def get_builtin_tool(self) -> MemoryTool:
        """Convert config to MemoryTool instance."""
        return MemoryTool()


class FileSearchToolConfig(BaseBuiltinToolConfig):
    """Configuration for PydanticAI file search builtin tool.

    Provides vector search over uploaded files (RAG). Supported by OpenAI and Google.

    Example:
        ```yaml
        tools:
          - type: builtin
            builtin_type: file_search
            file_store_ids: ["vs_abc123"]
        ```
    """

    model_config = ConfigDict(title="File Search Tool")

    builtin_type: Literal["file_search"] = Field("file_search", init=False)
    """File search builtin tool."""

    file_store_ids: list[str] = Field(
        examples=[["vs_abc123"], ["corpora/my-corpus"]],
        title="File store IDs",
    )
    """The file store IDs to search through.

    For OpenAI, these are vector store IDs created via the OpenAI API.
    For Google, these are file search store names uploaded via the Gemini Files API.
    """

    def get_builtin_tool(self) -> FileSearchTool:
        """Convert config to FileSearchTool instance."""
        return FileSearchTool(file_store_ids=self.file_store_ids)


class MCPServerToolConfig(BaseBuiltinToolConfig):
    """Configuration for PydanticAI MCP server builtin tool.

    Example:
        ```yaml
        tools:
          - type: builtin
            builtin_type: mcp_server
            id: my_server
            url: https://api.example.com/mcp
        ```
    """

    model_config = ConfigDict(title="MCP Server Tool")

    builtin_type: Literal["mcp_server"] = Field("mcp_server", init=False)
    """MCP server builtin tool."""

    server_id: str = Field(
        alias="id",
        examples=["my_mcp_server", "code_tools", "web_api"],
        title="Server ID",
    )
    """A unique identifier for the MCP server."""

    url: str = Field(
        examples=["https://api.example.com/mcp", "http://localhost:8080"],
        title="Server URL",
    )
    """The URL of the MCP server to use."""

    authorization_token: str | None = Field(
        default=None,
        examples=["Bearer token123", "api_key_abc"],
        title="Authorization token",
    )
    """Authorization header to use when making requests to the MCP server."""

    # description is inherited from BaseToolConfig

    allowed_tools: list[str] | None = Field(
        default=None,
        examples=[["search", "fetch"], ["execute", "compile"]],
        title="Allowed tools",
    )
    """A list of tools that the MCP server can use."""

    headers: dict[str, str] | None = Field(default=None, title="HTTP headers")
    """Optional HTTP headers to send to the MCP server."""

    def get_builtin_tool(self) -> MCPServerTool:
        """Convert config to MCPServerTool instance."""
        return MCPServerTool(
            id=self.server_id,
            url=self.url,
            authorization_token=self.authorization_token,
            description=self.description,
            allowed_tools=self.allowed_tools,
            headers=self.headers,
        )


# Union type for builtin tool configs (sub-discriminated by builtin_type)
BuiltinToolConfig = Annotated[
    WebSearchToolConfig
    | CodeExecutionToolConfig
    | WebFetchToolConfig
    | ImageGenerationToolConfig
    | MemoryToolConfig
    | FileSearchToolConfig
    | MCPServerToolConfig,
    Field(discriminator="builtin_type"),
]
