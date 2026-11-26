"""Tests for agentpool.utils.inspection."""

from collections.abc import Awaitable, Coroutine
import functools
from pathlib import Path
from typing import Annotated, Any, Union

import pytest

from agentpool.utils.inspection import has_return_type
from tests.utils import tc_only_tools as tc


type IntAlias = int
type UnionAlias = int | str


def _plain_int() -> int:
    return 1


def _generic_list() -> list[str]:
    return []


def _pipe_union() -> int | str:
    return 1


def _typing_union() -> Union[int, str]:  # noqa: UP007
    return 1


def _optional_int() -> int | None:
    return None


def _annotated_int() -> Annotated[int, "meta"]:
    return 1


def _alias_int() -> IntAlias:
    return 1


def _alias_union() -> UnionAlias:
    return 1


def _awaitable_int() -> Awaitable[int]:
    raise NotImplementedError


def _awaitable_union() -> Awaitable[int | None]:
    raise NotImplementedError


def _union_with_awaitable() -> int | Awaitable[str]:
    return 1


def _coroutine_bytes() -> Coroutine[int, str, bytes]:
    raise NotImplementedError


async def _async_int() -> int:
    return 1


async def _async_union() -> int | None:
    return 1


async def _async_generic_list() -> list[str]:
    return []


def _unannotated():
    return 1


async def _unannotated_async():
    return 1


def _any_return() -> Any:
    return 1


def _partial_target(x: int) -> str:
    return str(x)


class _CallableWithReturn:
    def __call__(self) -> int:
        return 1


@pytest.mark.parametrize(
    ("func", "expected_type", "expected"),
    [
        # Plain return type match / mismatch
        pytest.param(_plain_int, int, True, id="plain-match"),
        pytest.param(_plain_int, str, False, id="plain-mismatch"),
        pytest.param(_generic_list, list, True, id="generic-origin-match"),
        pytest.param(_generic_list, str, False, id="generic-arg-not-matched"),
        pytest.param(_annotated_int, int, True, id="annotated-match"),
        pytest.param(_annotated_int, str, False, id="annotated-mismatch"),
        pytest.param(_alias_int, int, True, id="type-alias-match"),
        pytest.param(_alias_int, str, False, id="type-alias-mismatch"),
        pytest.param(_alias_union, str, True, id="type-alias-union-match"),
        pytest.param(functools.partial(_partial_target, 1), str, True, id="partial-match"),
        pytest.param(_CallableWithReturn(), int, True, id="callable-instance-match"),
        # Union containing the type
        pytest.param(_pipe_union, int, True, id="pipe-union-first"),
        pytest.param(_pipe_union, str, True, id="pipe-union-second"),
        pytest.param(_pipe_union, float, False, id="pipe-union-mismatch"),
        pytest.param(_typing_union, str, True, id="typing-union-match"),
        pytest.param(_optional_int, int, True, id="optional-match"),
        pytest.param(_optional_int, type(None), True, id="optional-none-match"),
        pytest.param(_optional_int, str, False, id="optional-mismatch"),
        pytest.param(_union_with_awaitable, int, True, id="union-awaitable-sync-arm"),
        pytest.param(_union_with_awaitable, str, True, id="union-awaitable-async-arm"),
        pytest.param(_union_with_awaitable, float, False, id="union-awaitable-mismatch"),
        # Awaitable[T] / Coroutine / async def returning T
        pytest.param(_awaitable_int, int, True, id="awaitable-inner-match"),
        pytest.param(_awaitable_int, str, False, id="awaitable-inner-mismatch"),
        pytest.param(_awaitable_int, Awaitable, True, id="awaitable-origin-match"),
        pytest.param(_awaitable_union, int, True, id="awaitable-union-match"),
        pytest.param(_awaitable_union, str, False, id="awaitable-union-mismatch"),
        pytest.param(_coroutine_bytes, bytes, True, id="coroutine-return-arg-match"),
        pytest.param(_coroutine_bytes, int, False, id="coroutine-yield-arg-not-matched"),
        pytest.param(_coroutine_bytes, str, False, id="coroutine-send-arg-not-matched"),
        pytest.param(_coroutine_bytes, Coroutine, True, id="coroutine-origin-match"),
        pytest.param(_async_int, int, True, id="async-def-plain-match"),
        pytest.param(_async_int, str, False, id="async-def-plain-mismatch"),
        pytest.param(_async_union, int, True, id="async-def-union-match"),
        pytest.param(_async_union, str, False, id="async-def-union-mismatch"),
        pytest.param(_async_generic_list, list, True, id="async-def-generic-match"),
        pytest.param(_async_generic_list, str, False, id="async-def-generic-arg-not-matched"),
        # Lambda and unannotated callables
        pytest.param(lambda: 42, int, False, id="lambda-no-annotation"),
        pytest.param(lambda x: x, str, False, id="lambda-arg-no-annotation"),
        pytest.param(_unannotated, int, False, id="unannotated-def"),
        pytest.param(_unannotated_async, int, False, id="unannotated-async-def"),
        pytest.param(_any_return, int, False, id="any-return-mismatch"),
        # TYPE_CHECKING-only return annotations
        pytest.param(tc.first_node, Path, True, id="tc-only-async-match"),
        pytest.param(tc.first_node, str, False, id="tc-only-async-mismatch"),
        pytest.param(tc.tc_union_return, Path, True, id="tc-only-union-match"),
        pytest.param(tc.tc_union_return, str, False, id="tc-only-union-mismatch"),
        pytest.param(tc.tc_awaitable_return, Path, True, id="tc-only-awaitable-match"),
        pytest.param(tc.tc_awaitable_return, str, False, id="tc-only-awaitable-mismatch"),
        pytest.param(tc.tc_dotted_return, Path, True, id="tc-only-dotted-match"),
        pytest.param(tc.tc_dotted_return, str, False, id="tc-only-dotted-mismatch"),
        pytest.param(tc.history_processor, list, True, id="tc-only-generic-origin-match"),
        pytest.param(tc.history_processor, str, False, id="tc-only-generic-mismatch"),
    ],
)
def test_has_return_type(func: Any, expected_type: type[Any], expected: bool) -> None:
    assert has_return_type(func, expected_type) is expected
