"""Tests for config_resources to ensure all configs stay in sync."""

import pathlib

import pytest

from agentpool import config_resources
from agentpool.models.manifest import AgentsManifest


def _get_all_yml_constants() -> list[tuple[str, str]]:
    """Get all constants from config_resources that point to .yml files."""
    results = []
    for name in dir(config_resources):
        if name.startswith("_"):
            continue
        value = getattr(config_resources, name)
        if isinstance(value, str) and value.endswith(".yml"):
            results.append((name, value))
    return results


@pytest.mark.parametrize(
    ("name", "config_path"),
    _get_all_yml_constants(),
    ids=[name for name, _ in _get_all_yml_constants()],
)
def test_pool_config_loads(name: str, config_path: str) -> None:
    """Verify all pool config files can be loaded and parsed."""
    path = pathlib.Path(config_path)
    assert path.exists(), f"Config file {name} does not exist: {config_path}"

    # Load and validate the config
    config = AgentsManifest.from_file(path)
    assert config is not None
    assert config.config_file_path == config_path


if __name__ == "__main__":
    pytest.main(["-vv", __file__])
