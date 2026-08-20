"""Test ResourceAccess capability for integration tests.

Minimal ``AbstractCapability`` implementations for resource resolution tests:

- ``TestResourceAccessCap`` — implements ``ResourceAccess`` protocol only
- ``TestSkillResourceCap`` — implements ``SkillResource`` protocol only
- ``TestToolAndResourceCap`` — implements ``ResourceAccess`` AND provides a
  tool via ``get_toolset()``. Used to catch duplicate-instance tool conflicts
  that occur when config-defined capabilities are built both at pool init
  (for the ExtensionRegistry) and in ``NativeAgent.__init__()`` (for tool
  execution).

Registered in agent configs via ``GenericCapabilityConfig``:

```yaml
capabilities:
  - type: tests.fixtures.test_resource_cap.TestResourceAccessCap
    args:
      read_text: "hello world"
      read_uri: "test://doc.md"
```
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.toolsets import FunctionToolset

from wolfharness.capabilities.resource_protocols import (
    ResourceEntry,
    SkillEntry,
    TextResourceContent,
)


if TYPE_CHECKING:
    from collections.abc import Sequence


@dataclass
class TestResourceAccessCap(AbstractCapability[Any]):
    """Minimal capability implementing ``ResourceAccess`` for tests.

    Returns a fixed text for ``read_resource()`` when the URI matches
    ``read_uri``. Returns ``None`` otherwise.
    """

    read_text: str = "hello world"
    read_uri: str = "test://doc.md"
    client_name: str = "test"
    _owns_client: bool = field(default=False, repr=False)

    def __post_init__(self) -> None:
        pass

    async def list_resources(self) -> Sequence[ResourceEntry]:
        return [
            ResourceEntry(
                uri=self.read_uri,
                name="doc.md",
                description="Test resource",
                mime_type="text/markdown",
            )
        ]

    async def read_resource(self, uri: str) -> list[TextResourceContent] | None:
        if uri == self.read_uri:
            return [TextResourceContent(text=self.read_text, uri=uri)]
        return None

    async def resource_exists(self, uri: str) -> bool:
        return uri == self.read_uri


@dataclass
class TestSkillResourceCap(AbstractCapability[Any]):
    """Minimal capability implementing ``SkillResource`` for tests.

    Returns a fixed text for ``read_skill()`` when the name matches
    ``skill_name``. Returns ``None`` otherwise.
    """

    skill_text: str = "skill content"
    skill_name: str = "test-skill"
    _owns_client: bool = field(default=False, repr=False)

    def __post_init__(self) -> None:
        pass

    async def list_skills(self) -> Sequence[SkillEntry]:
        return [
            SkillEntry(
                name=self.skill_name,
                description="Test skill",
                uri=f"skill://{self.skill_name}",
            )
        ]

    async def read_skill(self, name: str) -> str | None:
        if name == self.skill_name:
            return self.skill_text
        return None

    async def skill_exists(self, name: str) -> bool:
        return name == self.skill_name


@dataclass
class TestToolAndResourceCap(AbstractCapability[Any]):
    """Capability that BOTH implements ``ResourceAccess`` AND provides a tool.

    Used to catch duplicate-instance tool conflicts: if the same capability
    is built at pool init (for the ExtensionRegistry) and again in
    ``NativeAgent.__init__()`` (for tool execution), the agent's toolset
    assembly will fail with a tool name conflict.
    """

    read_text: str = "tool+resource content"
    read_uri: str = "test://tool-doc.md"
    client_name: str = "test"
    _owns_client: bool = field(default=False, repr=False)

    def __post_init__(self) -> None:
        pass

    async def list_resources(self) -> Sequence[ResourceEntry]:
        return [
            ResourceEntry(
                uri=self.read_uri,
                name="tool-doc.md",
                description="Test tool+resource",
                mime_type="text/markdown",
            )
        ]

    async def read_resource(self, uri: str) -> list[TextResourceContent] | None:
        if uri == self.read_uri:
            return [TextResourceContent(text=self.read_text, uri=uri)]
        return None

    async def resource_exists(self, uri: str) -> bool:
        return uri == self.read_uri

    def get_toolset(self) -> FunctionToolset[Any] | None:
        """Provide a simple tool to trigger tool-conflict detection."""

        async def test_lookup(query: str) -> str:
            """Look up a test resource by query."""
            return f"lookup: {query}"

        return FunctionToolset([test_lookup], id=self._toolset_id)
