from typing import TYPE_CHECKING

import pytest

from agentpool import AgentsManifest


if TYPE_CHECKING:
    from pathlib import Path


YAML_BASE = """
prompts:
  system_prompts:
    reviewer:
      content: "Base reviewer prompt"
      category: role
    validator:
      content: "Base validator"
      category: quality
"""

YAML_CHILD = """
INHERIT: {base_path}
prompts:
  system_prompts:
    reviewer:
      content: "Specialized reviewer"  # Override
    analyzer:  # Add new
      content: "New analyzer prompt"
      category: task
"""


def test_prompt_inheritance(tmp_path: Path):
    """Test YAML inheritance for prompts."""
    base_file = tmp_path / "base.yml"
    base_file.write_text(YAML_BASE)

    child_yaml = YAML_CHILD.format(base_path=base_file)
    manifest = AgentsManifest.from_yaml(child_yaml, inherit_path=base_file)
    assert len(manifest.prompts.system_prompts) == 3
    assert manifest.prompts.system_prompts["reviewer"].content == "Specialized reviewer"
    assert manifest.prompts.system_prompts["validator"].content == "Base validator"
    assert manifest.prompts.system_prompts["analyzer"].category == "task"


if __name__ == "__main__":
    pytest.main(["-v", __file__])
