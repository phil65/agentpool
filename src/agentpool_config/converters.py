"""Converter configuration."""

from docler_config import ConverterConfig
from pydantic import ConfigDict, Field
from schemez import Schema


class ConversionConfig(Schema):
    """Global conversion configuration."""

    providers: list[ConverterConfig] | None = Field(default=None, title="Converter providers")
    """List of configured converter providers."""

    default_provider: str | None = Field(
        default=None,
        examples=["markitdown", "youtube", "whisper_api"],
        title="Default provider",
    )
    """Name of default provider for conversions."""

    max_size: int | None = Field(
        default=None,
        examples=[1048576, 10485760, 52428800],
        title="Global size limit",
    )
    """Global size limit for all converters."""

    model_config = ConfigDict(frozen=True)
