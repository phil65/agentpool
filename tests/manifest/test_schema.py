from typing import TYPE_CHECKING

import jsonschema
import pydantic
import pytest

from agentpool import AgentsManifest
from agentpool.models.agents import NativeAgentConfig


if TYPE_CHECKING:
    from pathlib import Path


ANCHORS_YAML = """
x-defaults: &defaults
  model: test
  retries: 3
agents:
  first:
    <<: *defaults
  second:
    <<: *defaults
    retries: 1
"""


def test_schema_generation():
    """Test that JSON schema can be generated from config models."""
    try:
        _schema = AgentsManifest.model_json_schema()
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"Failed to generate schema: {exc}")


def test_extension_keys_hold_yaml_anchors(tmp_path: Path):
    """Top-level ``x-`` keys are ignored, so they can hold YAML anchors (#12)."""
    path = tmp_path / "agents.yml"
    path.write_text(ANCHORS_YAML)
    manifest = AgentsManifest.from_file(path)
    assert set(manifest.agents) == {"first", "second"}
    first, second = manifest.agents["first"], manifest.agents["second"]
    assert isinstance(first, NativeAgentConfig)
    assert isinstance(second, NativeAgentConfig)
    assert (first.retries, second.retries) == (3, 1)


def test_other_unknown_top_level_keys_are_rejected():
    with pytest.raises(pydantic.ValidationError):
        AgentsManifest.model_validate({"agent": {"a": {"model": "test"}}})


def test_json_schema_allows_extension_keys():
    schema = AgentsManifest.model_json_schema()
    jsonschema.validate({"x-defaults": {"model": "test"}, "agents": {}}, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"agent": {}}, schema)


if __name__ == "__main__":
    pytest.main([__file__])
