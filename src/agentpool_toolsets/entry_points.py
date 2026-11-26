"""Entry point based toolset implementation."""

from importlib.metadata import EntryPoint, entry_points
import re
from typing import TYPE_CHECKING

from agentpool.log import get_logger
from agentpool.resource_providers import ResourceProvider


if TYPE_CHECKING:
    from collections.abc import Sequence

    from agentpool.tools.base import Tool


logger = get_logger(__name__)

GROUP = "agentpool"
NAME = "tools"


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "_", name).lower()


def _matches(entry_point: EntryPoint, module: str) -> bool:
    """Whether the entry point belongs to ``module`` (its module path or distribution)."""
    if entry_point.module == module or entry_point.module.startswith(f"{module}."):
        return True
    dist = entry_point.dist
    return dist is not None and _normalize(dist.name) == _normalize(module)


class EntryPointTools(ResourceProvider):
    """Provider for the tools a package registers as entry point.

    The package registers ``tools = "pkg.module:get_tools"`` in the ``agentpool`` group;
    ``module`` selects the package (by module path or distribution name).
    """

    def __init__(self, module: str) -> None:
        super().__init__(name=module)
        self.module = module
        self._tools: list[Tool] | None = None

    def _find_entry_point(self) -> EntryPoint:
        for entry_point in entry_points(group=GROUP, name=NAME):
            if _matches(entry_point, self.module):
                return entry_point
        msg = f"No {NAME!r} entry point in group {GROUP!r} found for {self.module!r}"
        raise ValueError(msg)

    async def get_tools(self) -> Sequence[Tool]:
        """Get tools from the module's entry point."""
        if self._tools is not None:
            return self._tools
        get_tools = self._find_entry_point().load()
        meta = {"module": self.module}
        self._tools = [self.create_tool(item, metadata=meta) for item in get_tools()]
        return self._tools
