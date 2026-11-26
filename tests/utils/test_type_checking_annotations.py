"""Introspection of callables whose annotations name TYPE_CHECKING-only imports."""

from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.models.test import TestModel
import pytest

from agentpool import Agent
from agentpool.agents.context import AgentContext
from agentpool.agents.events import ToolCallCompleteEvent
from agentpool.tools.base import FunctionTool
from agentpool.utils.inspection import get_argument_key
from agentpool.utils.signatures import (
    annotation_names,
    create_bound_callable,
    get_params_matching_predicate,
    is_context_type,
)
from tests.utils import tc_only_tools as tc


def test_annotations_are_unresolvable() -> None:
    with pytest.raises(NameError):
        _ = tc.agent_ctx_tool.__annotations__


@pytest.mark.parametrize(
    ("fn", "typ", "key"),
    [
        (tc.agent_ctx_tool, AgentContext, "ctx"),
        (tc.optional_agent_ctx_tool, AgentContext, "ctx"),
        (tc.run_ctx_tool, RunContext, "ctx"),
        (tc.agent_ctx_tool, RunContext, False),
        (tc.path_tool, AgentContext, False),
    ],
)
def test_get_argument_key(fn: Any, typ: type, key: str | bool) -> None:
    assert get_argument_key(fn, typ) == key


def test_context_params_found_by_name() -> None:
    fn = tc.optional_agent_ctx_tool
    assert get_params_matching_predicate(fn, lambda p: is_context_type(p.annotation)) == {"ctx"}
    assert annotation_names("RunContext[Deps] | mod.AgentContext | None") == {
        "RunContext",
        "AgentContext",
        "None",
    }


@pytest.mark.parametrize(
    ("fn", "properties"),
    [
        (tc.agent_ctx_tool, ["path", "count"]),
        (tc.optional_agent_ctx_tool, ["text"]),
        (tc.run_ctx_tool, ["text"]),
        (tc.path_tool, ["path"]),
    ],
)
def test_tool_schema_hides_context(fn: Any, properties: list[str]) -> None:
    params = FunctionTool.from_callable(fn).schema["function"]["parameters"]
    assert list(params["properties"]) == properties
    assert set(params.get("required", [])) <= set(properties)


@pytest.mark.parametrize(
    ("fn", "expected"),
    [
        (tc.agent_ctx_tool, "AgentContext:"),
        (tc.optional_agent_ctx_tool, "AgentContext:"),
        (tc.run_ctx_tool, "RunContext:"),
        (tc.path_tool, ""),
    ],
)
async def test_native_agent_runs_tool(fn: Any, expected: str) -> None:
    name = fn.__name__
    model = TestModel(call_tools=[name])
    async with Agent(name="a", model=model, tools=[fn]) as agent:
        events = [e async for e in agent.run_stream("hi")]
    done = [e for e in events if isinstance(e, ToolCallCompleteEvent)]
    assert [e.tool_name for e in done] == [name]
    assert done[0].error is None
    assert str(done[0].tool_result).startswith(expected)
    tool_def = next(t for t in model.last_model_request_parameters.function_tools)
    assert "ctx" not in tool_def.parameters_json_schema.get("properties", {})


async def test_bound_callable_binds_by_type_name() -> None:
    marker = object()
    bound = create_bound_callable(tc.run_ctx_tool, by_type={RunContext: marker})
    assert list(bound.__signature__.parameters) == ["text"]  # type: ignore[attr-defined]  # ty:ignore[unresolved-attribute]
    assert await bound(text="x") == "object:x"


@pytest.mark.parametrize(
    ("fn", "params"),
    [
        ("agentpool.resource_providers.plan_provider:PlanProvider.add_plan_entry", ["priority"]),
        (
            "agentpool.resource_providers.plan_provider:PlanProvider.update_plan_entry",
            ["status", "priority"],
        ),
        ("agentpool_toolsets.search_toolset:SearchTools.web_search", ["country", "language"]),
        ("agentpool_toolsets.search_toolset:SearchTools.news_search", ["country", "language"]),
    ],
)
def test_builtin_tool_parameter_types_resolve(fn: str, params: list[str]) -> None:
    """Tool parameter types must be importable at runtime: they make the tool's schema."""
    import annotationlib
    import importlib

    module, _, qualname = fn.partition(":")
    obj: Any = importlib.import_module(module)
    for part in qualname.split("."):
        obj = getattr(obj, part)
    hints = annotationlib.get_annotations(obj, format=annotationlib.Format.FORWARDREF)
    for param in params:
        assert "ForwardRef" not in repr(hints[param]), param
