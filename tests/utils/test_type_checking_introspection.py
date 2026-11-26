"""Runtime introspection (backends, node functions, loaders) of TYPE_CHECKING-only names."""

from __future__ import annotations

import pathlib
from typing import TYPE_CHECKING, Any

import pytest

from agentpool.agents.backend_options import dataclass_field_types, parameter_types
from agentpool.agents.codex_agent.codex_converters import options_taken_by
from agentpool.running import with_nodes
from agentpool.running.discovery import NodeFunction
from agentpool.running.executor import execute_functions
from tests.utils import tc_only_tools as tc


if TYPE_CHECKING:
    from agentpool import AgentPool


@pytest.mark.parametrize(
    ("globalns", "path_type"), [({"Path": pathlib.Path}, pathlib.Path), ({}, Any)]
)
def test_backend_option_types(globalns: dict[str, Any], path_type: Any) -> None:
    expected = {"path": path_type, "count": int}
    assert dataclass_field_types(tc.BackendOptions, globalns) == expected
    assert parameter_types(tc.BackendClient.start, globalns) == expected
    assert options_taken_by(tc.BackendClient.start, {"path": 1, "other": 2}) == {"path": 1}


async def test_node_functions(pool: AgentPool) -> None:
    assert await with_nodes(pool)(tc.first_node)() == "agent1!"
    funcs = [NodeFunction(tc.second_node, depends_on=["first_node"]), NodeFunction(tc.first_node)]
    results = await execute_functions(funcs, pool)
    assert results == {"first_node": "agent1!", "second_node": "agent1!:agent2"}


def test_callable_resource_is_templated() -> None:
    from agentpool_config.loaders import CallableResourceLoaderConfig

    path = f"{tc.__name__}.path_tool"
    assert CallableResourceLoaderConfig.model_construct(import_path=path).is_templated()


def test_history_processor_signature() -> None:
    from agentpool.models.agents import _validate_processor_signature

    _validate_processor_signature(tc.history_processor)


def test_prompt_arguments() -> None:
    from agentpool.prompts.prompts import DynamicPrompt

    prompt = DynamicPrompt.from_callable(tc.path_tool)
    assert [arg.name for arg in prompt.to_mcp_prompt().arguments or []] == ["path"]
