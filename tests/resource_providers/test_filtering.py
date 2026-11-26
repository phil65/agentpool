"""FilteringResourceProvider filters tools and delegates everything else (#53.1)."""

from typing import TYPE_CHECKING, Any, Self

from agentpool.resource_providers import FilteringResourceProvider, ResourceProvider
from agentpool.tools.base import FunctionTool


if TYPE_CHECKING:
    from collections.abc import Sequence

    from agentpool.tools.base import Tool


def keep() -> str:
    """Kept tool."""
    return "keep"


def drop() -> str:
    """Filtered tool."""
    return "drop"


class Inner(ResourceProvider):
    """Provider with everything a wrapper has to delegate."""

    kind = "custom"

    def __init__(self) -> None:
        super().__init__(name="inner", owner="me")
        self.entered = self.exited = False

    async def __aenter__(self) -> Self:
        self.entered = True
        return self

    async def __aexit__(self, *args: object) -> None:
        self.exited = True

    async def get_tools(self) -> Sequence[Tool]:
        return [FunctionTool.from_callable(keep), FunctionTool.from_callable(drop)]

    async def get_prompts(self) -> Any:
        return ["PROMPT"]

    async def get_skills(self) -> Any:
        return ["SKILL"]

    async def get_skill_instructions(self, skill_name: str) -> str:
        return f"do {skill_name}"

    async def get_request_parts(self, name: str, arguments: Any = None) -> Any:
        return [f"PART {name}"]


async def test_filters_tools_and_delegates_the_rest() -> None:
    inner = Inner()
    wrapped = FilteringResourceProvider(inner, {"drop": False})
    assert (wrapped.name, wrapped.owner, wrapped.kind) == ("inner", "me", "custom")
    async with wrapped:
        assert inner.entered
        assert [t.name for t in await wrapped.get_tools()] == ["keep"]
        assert await wrapped.get_prompts() == ["PROMPT"]
        assert await wrapped.get_skills() == ["SKILL"]
        assert await wrapped.get_skill_instructions("x") == "do x"
        assert await wrapped.get_request_parts("p") == ["PART p"]
    assert inner.exited


async def test_forwards_change_signals() -> None:
    inner = Inner()
    wrapped = FilteringResourceProvider(inner, {})
    got: list[str] = []

    async def on_change(event: Any) -> None:
        got.append(event.resource_type)

    wrapped.tools_changed.connect(on_change)
    wrapped.skills_changed.connect(on_change)
    await inner.tools_changed.emit(inner.create_change_event("tools"))
    await inner.skills_changed.emit(inner.create_change_event("skills"))
    assert got == ["tools", "skills"]
