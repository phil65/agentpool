"""The jinjarope entry point registers agentpool's filters without importing agentpool."""

from pathlib import Path
import subprocess
import sys
import tomllib

import jinja2


ROOT = Path(__file__).parent.parent
EXPECTED_FILTERS = {
    "run_agent",
    "run_agent_sync",
    "pick_one",
    "get_structured",
    "get_structured_multiple",
    "pydantic_playground",
    "pydantic_playground_url",
    "pydantic_playground_iframe",
    "pydantic_playground_link",
    "config_fields_table",
    "config_union_table",
    "cli_command_table",
    "toolset_tools_table",
}


def test_entry_point_targets_the_light_module():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    entry = pyproject["project"]["entry-points"]["jinjarope.environment"]["agentpool"]
    assert entry == "agentpool_jinja:setup_env"


def test_setup_env_does_not_import_agentpool():
    code = (
        "import sys, jinja2, agentpool_jinja\n"
        "env = jinja2.Environment()\n"
        "agentpool_jinja.setup_env(env)\n"
        "loaded = sorted(m for m in sys.modules if m.split('.')[0] == 'agentpool')\n"
        "assert not loaded, loaded\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True, cwd=ROOT)


def test_filters_resolve_on_first_use():
    from pydantic import BaseModel, Field

    import agentpool_jinja

    class Example(BaseModel):
        name: str = Field(description="The name.")

    env = jinja2.Environment()
    agentpool_jinja.setup_env(env)
    assert set(env.filters) >= EXPECTED_FILTERS
    url_filter = env.filters["pydantic_playground_url"]
    assert getattr(url_filter, "jinja_pass_arg", None) is jinja2.utils._PassArg.environment
    table = env.from_string("{{ model | config_fields_table }}").render(model=Example)
    assert "The name." in table
    assert env.globals["agent"].from_callback  # The Agent class, behind the lazy global
