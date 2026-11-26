"""Tests for agent configuration loading."""

import logging
from typing import TYPE_CHECKING

import pytest

from agentpool import AgentsManifest


if TYPE_CHECKING:
    from pathlib import Path


def test_load_valid_config(valid_config: str):
    """Test loading valid configuration."""
    config = AgentsManifest.model_validate(valid_config)
    assert config.agents["support"].display_name == "Support Agent"
    assert "SupportResult" in config.responses


def test_load_invalid_file(caplog):
    """Test loading non-existent file."""
    caplog.set_level(logging.CRITICAL)
    with pytest.raises(ValueError):  # noqa: PT011
        AgentsManifest.from_file("nonexistent.yml")


def test_load_invalid_yaml(tmp_path: Path, caplog):
    """Test loading invalid YAML content."""
    caplog.set_level(logging.CRITICAL)
    invalid_file = tmp_path / "invalid.yml"
    invalid_file.write_text("invalid: yaml: content:")

    with pytest.raises(ValueError):  # noqa: PT011
        AgentsManifest.from_file(invalid_file)


if __name__ == "__main__":
    pytest.main([__file__])
