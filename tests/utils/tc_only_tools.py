"""Tools whose annotations name things only imported under TYPE_CHECKING.

No `from __future__ import annotations` here: the annotations are evaluated lazily
(PEP 649) and raise `NameError` when read with the default VALUE format.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from pathlib import Path

    from pydantic_ai import RunContext
    from pydantic_ai.messages import ModelMessage

    from agentpool import Agent
    from agentpool.agents.context import AgentContext


async def agent_ctx_tool(ctx: AgentContext, path: Path, count: int = 1) -> str:
    """Tool taking an AgentContext."""
    return f"{type(ctx).__name__}:{count}"


async def optional_agent_ctx_tool(text: str, ctx: AgentContext | None = None) -> str:
    """Tool taking an optional AgentContext."""
    return f"{type(ctx).__name__}:{text}"


async def run_ctx_tool(ctx: RunContext, text: str) -> str:
    """Tool taking a RunContext."""
    return f"{type(ctx).__name__}:{text}"


def path_tool(path: Path) -> str:
    """Tool without context."""
    return str(path)


@dataclass
class BackendOptions:
    """Backend options typed with a TYPE_CHECKING-only name."""

    path: Path
    count: int = 1


class BackendClient:
    """Backend client typed with a TYPE_CHECKING-only name."""

    def start(self, path: Path, *, count: int = 1, **kwargs: Any) -> None:
        """Start."""


async def first_node(agent1: Agent[None], suffix: str = "!") -> Path:
    """Node function getting an agent injected."""
    return f"{agent1.name}{suffix}"  # type: ignore[return-value]  # ty:ignore[invalid-return-type]


async def second_node(first_node: Path, agent2: Agent[None] | None) -> str:
    """Node function depending on first_node."""
    assert agent2 is not None
    return f"{first_node}:{agent2.name}"


def history_processor(ctx: RunContext, messages: list[ModelMessage]) -> list[ModelMessage]:
    """History processor."""
    return messages
