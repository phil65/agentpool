"""T3 agent config, model selection and question conversion."""

from __future__ import annotations

from mcp import types
from pydantic import ValidationError
import pytest

from agentpool.agents.t3_agent import T3Agent
from agentpool.agents.t3_agent.questions import Question, to_answer, to_elicitation
from agentpool.agents.t3_agent.threads import server_path
from agentpool.models.manifest import AgentsManifest
from agentpool.models.t3_agents import T3AgentConfig


def test_manifest_loads_a_t3_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("T3_TOKEN", "secret")
    manifest = AgentsManifest.model_validate({
        "agents": {
            "remote": {
                "type": "t3",
                "url": "https://t3.test",
                "token": "${T3_TOKEN}",
                "project": "/work/proj",
                "provider": "codex",
                "model": "gpt-6.2-sol",
                "options": {"effort": "high"},
            }
        }
    })
    config = manifest.agents["remote"]
    assert isinstance(config, T3AgentConfig)
    assert config.get_token() == "secret"
    agent = config.get_agent()
    assert isinstance(agent, T3Agent)
    assert agent.model_name == "codex/gpt-6.2-sol"
    selection = agent._model_selection()
    assert selection is not None
    assert selection.model_dump(mode="json", by_alias=True, exclude_unset=True) == {
        "instanceId": "codex",
        "model": "gpt-6.2-sol",
        "options": [{"id": "effort", "value": "high"}],
    }


@pytest.mark.parametrize(
    ("fields", "error"),
    [
        ({"url": "https://t3.test"}, "needs `login`"),
        ({"login": "home", "model": "x"}, "`model` needs `provider`"),
    ],
)
def test_invalid_configs(fields: dict[str, str], error: str) -> None:
    with pytest.raises(ValidationError, match=error):
        T3AgentConfig.model_validate({"project": "p", **fields})


def test_project_default_model() -> None:
    agent = T3AgentConfig(login="home", project="proj_1").get_agent()
    assert agent.model_name is None
    assert agent._model_selection() is None


_OPTIONS = [{"label": "Red", "description": ""}, {"label": "Blue", "value": "blue"}]


def test_choice_question() -> None:
    question = Question.model_validate({
        "id": "c",
        "header": "Color",
        "question": "Which?",
        "options": _OPTIONS,
    })
    form = to_elicitation(question)
    assert form.message == "Color: Which?"
    assert form.requested_schema["properties"]["c"]["enum"] == ["Red", "Blue"]
    assert form.requested_schema["required"] == ["c"]
    accept = types.ElicitResult(action="accept", content={"c": "Blue"})
    assert to_answer(question, accept) == "blue"
    assert to_answer(question, types.ElicitResult(action="decline")) is None


def test_multi_select_and_free_text_questions() -> None:
    multi = Question.model_validate({
        "id": "m",
        "question": "Which?",
        "options": _OPTIONS,
        "multiSelect": True,
    })
    prop = to_elicitation(multi).requested_schema["properties"]["m"]
    assert prop["type"] == "array"
    accept = types.ElicitResult(action="accept", content={"m": ["Red", "Blue"]})
    assert to_answer(multi, accept) == ["Red", "blue"]
    free = Question.model_validate({
        "id": "f",
        "question": "Name?",
        "initialAnswer": "x",
        "required": False,
    })
    form = to_elicitation(free)
    assert form.requested_schema["properties"]["f"] == {
        "title": "f",
        "description": "Name?",
        "type": "string",
        "default": "x",
    }
    assert "required" not in form.requested_schema
    assert to_answer(free, types.ElicitResult(action="accept", content={"f": "Ada"})) == "Ada"


@pytest.mark.parametrize(
    ("first", "second", "same"),
    [
        ("/srv/app/", "/srv/app", True),
        ("C:\\src\\App", "c:\\src\\app", True),
        ("/src/app", "C:\\src\\app", False),
    ],
)
def test_server_paths(first: str, second: str, *, same: bool) -> None:
    assert (server_path(first) == server_path(second)) is same


@pytest.mark.parametrize("text", ["proj_123", "~/src/app", "src/app"])
def test_project_ids_and_relative_paths_are_not_server_paths(text: str) -> None:
    assert server_path(text) is None
