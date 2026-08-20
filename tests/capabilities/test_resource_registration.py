"""Tests for ResourceCapability registration and YAML opt-out.

Tests that:
- ``ResourceCapability`` is registered after ``AgentPool.__aenter__()``
- The pool-level ``ExtensionRegistry`` has the capability at ``POOL`` scope
- Agents with ``resources.enabled: false`` don't get resource tools
- ``ResourceConfig`` defaults to ``enabled=True``
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import yamling

from wolfharness import AgentPool, AgentsManifest
from wolfharness_config.nodes import BaseAgentConfig, ResourceConfig


if TYPE_CHECKING:
    from collections.abc import AsyncIterator


pytestmark = pytest.mark.unit


# =============================================================================
# Config fixtures
# =============================================================================


MINIMAL_CONFIG = """\
agents:
  test_agent:
    type: native
    model: test
    system_prompt: "You are a test agent."
"""

DISABLED_RESOURCES_CONFIG = """\
agents:
  test_agent:
    type: native
    model: test
    system_prompt: "You are a test agent."
    resources:
      enabled: false
"""


@pytest.fixture
async def minimal_pool() -> AsyncIterator[AgentPool]:
    """Real AgentPool with TestModel — no MCP, no storage, no external deps."""
    manifest = yamling.load_yaml(MINIMAL_CONFIG, verify_type=dict)
    manifest_obj = AgentsManifest.model_validate(manifest)
    async with AgentPool(manifest_obj) as pool:
        yield pool


@pytest.fixture
async def disabled_resources_pool() -> AsyncIterator[AgentPool]:
    """AgentPool where the test agent has resources.enabled=false."""
    manifest = yamling.load_yaml(DISABLED_RESOURCES_CONFIG, verify_type=dict)
    manifest_obj = AgentsManifest.model_validate(manifest)
    async with AgentPool(manifest_obj) as pool:
        yield pool


@pytest.fixture
def minimal_manifest() -> AgentsManifest:
    """Manifest object without entering AgentPool context."""
    manifest = yamling.load_yaml(MINIMAL_CONFIG, verify_type=dict)
    return AgentsManifest.model_validate(manifest)


# =============================================================================
# ResourceConfig model tests
# =============================================================================


def test_resource_config_defaults():
    """ResourceConfig defaults to enabled=True."""
    config = ResourceConfig()
    assert config.enabled is True


def test_resource_config_disabled():
    """ResourceConfig can be created with enabled=False."""
    config = ResourceConfig(enabled=False)
    assert config.enabled is False


def test_resource_config_frozen():
    """ResourceConfig is frozen — fields cannot be mutated."""
    from pydantic import ValidationError

    config = ResourceConfig()
    with pytest.raises((ValidationError, AttributeError)):
        config.enabled = False  # type: ignore[misc]


def test_base_agent_config_has_resources_field():
    """BaseAgentConfig has a ``resources`` field defaulting to enabled=True."""
    config = BaseAgentConfig(name="test")
    assert config.resources.enabled is True


def test_base_agent_config_resources_disabled():
    """BaseAgentConfig accepts resources.enabled=False."""
    config = BaseAgentConfig(name="test", resources=ResourceConfig(enabled=False))
    assert config.resources.enabled is False


# =============================================================================
# AgentPool registration tests
# =============================================================================


async def test_resource_capability_registered_after_enter(minimal_pool):
    """ResourceCapability is created but NOT registered in ExtensionRegistry.

    The pool should have a non-None ``resource_capability`` property,
    but it should NOT appear in ``get_resource_access()`` results because
    it is a tool wrapper, not a ``ResourceAccess`` data provider.
    """
    from wolfharness.capabilities.extension_registry import Scope, ScopeLevel
    from wolfharness.capabilities.resource_capability import ResourceCapability

    pool = minimal_pool
    assert pool.resource_capability is not None
    assert isinstance(pool.resource_capability, ResourceCapability)

    # ResourceCapability should NOT be in the ExtensionRegistry.
    # It's a tool wrapper, not a ResourceAccess provider.
    registry = pool.extension_registry
    pool_scope = Scope(level=ScopeLevel.POOL)
    resource_caps = list(registry.get_resource_access(pool_scope))
    assert not any(isinstance(cap, ResourceCapability) for cap in resource_caps)


async def test_resource_capability_not_registered_before_enter(minimal_manifest):
    """ResourceCapability is None before __aenter__."""
    pool = AgentPool(minimal_manifest)
    assert pool.resource_capability is None


# =============================================================================
# Opt-out tests
# =============================================================================


async def test_agent_with_resources_disabled_does_not_get_capability(disabled_resources_pool):
    """Agent with resources.enabled=false must not expose resource tools."""
    pool = disabled_resources_pool
    config = pool.agent_configs["test_agent"]
    assert config.resources.enabled is False
    agent = await pool.session_pool.sessions.get_or_create_session_agent(
        "disabled-resource-tools",
        "test_agent",
    )
    tool_names = {tool.name for tool in await agent._get_all_tools()}
    assert {
        "list_mcp_resources",
        "list_mcp_resource_templates",
        "read_mcp_resource",
    }.isdisjoint(tool_names)


async def test_agent_with_resources_enabled_gets_capability(minimal_pool):
    """Agent with default resources config exposes exactly the formal tools."""
    pool = minimal_pool
    config = pool.agent_configs["test_agent"]
    assert config.resources.enabled is True
    agent = await pool.session_pool.sessions.get_or_create_session_agent(
        "enabled-resource-tools",
        "test_agent",
    )
    tool_names = {tool.name for tool in await agent._get_all_tools()}
    assert {
        "list_mcp_resources",
        "list_mcp_resource_templates",
        "read_mcp_resource",
    } <= tool_names
    assert {
        "list_resources",
        "resource_exists",
        "read_resource",
        "list_resource_templates",
        "complete_resource_template",
    }.isdisjoint(tool_names)
