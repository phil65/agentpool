"""Simplified observability registry using Logfire with single backend."""

import os
from typing import TYPE_CHECKING

import logfire

from agentpool.log import get_logger
from agentpool_config.observability import CustomObservabilityConfig


if TYPE_CHECKING:
    from agentpool_config.observability import ObservabilityConfig, ObservabilityProviderConfig

logger = get_logger(__name__)


class ObservabilityRegistry:
    """Simplified registry that configures Logfire for single backend export."""

    def __init__(self) -> None:
        self._configured = False

    def configure_observability(self, observability_config: ObservabilityConfig) -> None:
        """Configure Logfire for single backend export.

        Args:
            observability_config: Configuration for observability
        """
        if not observability_config.enabled or not observability_config.provider:
            logger.debug("Observability disabled or no provider configured")
            return

        if self._configured:
            logger.warning("Observability already configured, skipping")
            return

        config = observability_config.provider
        if not config.enabled:
            logger.debug("Provider is disabled", provider=config.type)
            return

        _setup_otel_environment(config)  # Configure OTEL env variables based on provider
        logfire.configure(
            service_name=config.service_name,
            environment=config.environment,
            console=False,
            send_to_logfire=(config.type == "logfire"),
        )
        logfire.instrument_pydantic_ai()
        logfire.instrument_mcp()
        # Note: structlog logs are captured via StructlogProcessor in log.py

        self._configured = True
        logger.info("Configured observability", provider=config.type)


def _setup_otel_environment(config: ObservabilityProviderConfig) -> None:
    """Set up OTEL environment variables for the configured backend."""
    if isinstance(config, CustomObservabilityConfig):
        endpoint = config.endpoint
        headers = config.headers
    else:
        endpoint = config._endpoint
        headers = config._headers

    if not endpoint:
        logger.warning("No endpoint found", provider=config.type)
        return

    # Set standard OTEL environment variables
    os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = endpoint
    os.environ["OTEL_EXPORTER_OTLP_PROTOCOL"] = config.protocol

    # Set headers if available
    if headers:
        header_str = ",".join(f"{k}={v}" for k, v in headers.items())
        os.environ["OTEL_EXPORTER_OTLP_HEADERS"] = header_str

    # Set resource attributes
    resource_attrs = []
    if config.service_name:
        resource_attrs.append(f"service.name={config.service_name}")
    if config.environment:
        resource_attrs.append(f"deployment.environment.name={config.environment}")

    if resource_attrs:
        os.environ["OTEL_RESOURCE_ATTRIBUTES"] = ",".join(resource_attrs)

    logger.debug(
        "Set OTEL environment",
        endpoint=endpoint,
        protocol=config.protocol,
        headers=headers,
    )


# Global registry instance
registry = ObservabilityRegistry()
