"""Discovery, injection and type checks of the running module (issue #59.5)."""

from typing import TYPE_CHECKING, Any

import pytest

from agentpool.running.decorators import with_nodes
from agentpool.running.executor import (
    _sort_functions,
    _validate_value_type,
    discover_functions,
)


if TYPE_CHECKING:
    from pathlib import Path


MODULE = """
from agentpool.running import node_function


@node_function
async def first() -> str:
    return "a"


@node_function(depends_on="first")
async def second(first: str) -> str:
    return first + "b"
"""


def test_discovered_functions_can_be_sorted(tmp_path: Path) -> None:
    path = tmp_path / "flows.py"
    path.write_text(MODULE)
    functions = discover_functions(path)
    assert [f.name for f in _sort_functions(functions)] == ["first", "second"]


async def test_with_nodes_keeps_positional_arguments() -> None:
    class Pool:
        def __init__(self) -> None:
            self.nodes: dict[str, Any] = {}

    async def add(a: int, b: int = 2) -> int:
        return a + b

    assert await with_nodes(Pool(), add)(1) == 3  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("value", "expected", "ok"),
    [
        (["a"], list[str], True),
        ({"a": 1}, dict[str, int], True),
        ("a", list[str], False),
        (1, int | None, True),
        (None, int | None, True),
        ("a", int | None, False),
        (1, Any, True),
    ],
)
def test_value_types_with_generics_and_unions(value: Any, expected: Any, ok: bool) -> None:
    if ok:
        _validate_value_type(value, expected, "f", "p")
    else:
        with pytest.raises(TypeError):
            _validate_value_type(value, expected, "f", "p")
