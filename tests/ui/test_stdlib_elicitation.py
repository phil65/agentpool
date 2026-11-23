from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mcp import types
import promptantic

from agentpool.ui.stdlib_provider import StdlibInputProvider


if TYPE_CHECKING:
    import pytest


SCHEMA = {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}


def params() -> types.ElicitRequestFormParams:
    return types.ElicitRequestFormParams(message="Who?", requestedSchema=SCHEMA)


async def test_form_elicitation_uses_a_form(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_fill(target: Any, **_: Any) -> dict[str, Any]:
        assert target == SCHEMA
        return {"name": "Ada"}

    monkeypatch.setattr(promptantic, "fill", fake_fill)
    result = await StdlibInputProvider().get_elicitation(params())
    assert result == types.ElicitResult(action="accept", content={"name": "Ada"})


async def test_cancelled_form_cancels_the_elicitation(monkeypatch: pytest.MonkeyPatch) -> None:
    async def cancelled(target: Any, **_: Any) -> dict[str, Any]:
        raise promptantic.FormCancelledError

    monkeypatch.setattr(promptantic, "fill", cancelled)
    result = await StdlibInputProvider().get_elicitation(params())
    assert result == types.ElicitResult(action="cancel")
