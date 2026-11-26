"""Configuration resolution with layered inheritance.

This module implements a configuration system inspired by OpenCode's approach,
supporting multiple config layers that are deep-merged together.

Precedence order (later overrides earlier):
1. Global config - ~/.config/agentpool/agentpool.yml - user preferences
2. Custom config - AGENTPOOL_CONFIG env var path
3. Project config - agentpool.yml in project/git root
4. Explicit config - CLI argument (highest precedence)
5. Fallback config - Only used if no agents defined in any other layer

Each layer is optional. Configs are deep-merged: conflicting keys override,
non-conflicting settings from all configs are preserved.
"""

from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import platformdirs
import yamling


if TYPE_CHECKING:
    from upathtools import JoinablePathLike


type ConfigSource = Literal["builtin", "global", "custom", "project", "explicit"]


@dataclass
class ConfigLayer:
    """A single configuration layer with its source and data."""

    source: ConfigSource
    path: str | None
    data: dict[str, Any] = field(repr=False)


@dataclass
class ResolvedConfig:
    """Result of config resolution with merged data and source tracking."""

    data: dict[str, Any]
    """The merged configuration data."""

    layers: list[ConfigLayer] = field(default_factory=list)
    """All layers that contributed, in precedence order (lowest to highest)."""

    primary_path: str | None = None
    """The primary config path (explicit or project, used for relative path resolution)."""

    @property
    def source_paths(self) -> list[str]:
        """Get list of all config file paths that contributed."""
        return [layer.path for layer in self.layers if layer.path is not None]

    def get_layer(self, source: ConfigSource) -> ConfigLayer | None:
        """Get a specific layer by source type."""
        return next((layer for layer in self.layers if layer.source == source), None)


# Standard config file names to search for
CONFIG_FILE_NAMES: tuple[str, ...] = (
    "agentpool.yml",
    "agentpool.yaml",
    "agentpool.json",
    "agentpool.jsonc",
)

# Environment variable names
ENV_CONFIG_PATH = "AGENTPOOL_CONFIG"
ENV_CONFIG_CONTENT = "AGENTPOOL_CONFIG_CONTENT"
ENV_DISABLE_GLOBAL = "AGENTPOOL_NO_GLOBAL_CONFIG"
ENV_DISABLE_PROJECT = "AGENTPOOL_NO_PROJECT_CONFIG"


def get_global_config_dir() -> Path:
    """Get the global configuration directory.

    Returns:
        Path to ~/.config/agentpool (or platform equivalent)
    """
    return Path(platformdirs.user_config_dir("agentpool", appauthor=False))


def get_global_config_path() -> Path | None:
    """Get path to global config file if it exists.

    Searches for config files in precedence order in the global config dir.

    Returns:
        Path to global config file, or None if not found
    """
    config_dir = get_global_config_dir()
    if not config_dir.exists():
        return None

    for name in CONFIG_FILE_NAMES:
        config_path = config_dir / name
        if config_path.is_file():
            return config_path

    return None


def find_project_config(start_dir: JoinablePathLike | None = None) -> Path | None:
    """Find project config by traversing up to git root or filesystem root.

    Searches for agentpool.yml/yaml/json in the current directory and parent
    directories up to the nearest git repository root or filesystem root.

    Args:
        start_dir: Directory to start search from (defaults to cwd)

    Returns:
        Path to project config file, or None if not found
    """
    current = Path.cwd() if start_dir is None else Path(str(start_dir)).resolve()

    # Track if we've passed a git root
    passed_git_root = False

    while True:
        # Check for config files in current directory
        for name in CONFIG_FILE_NAMES:
            config_path = current / name
            if config_path.is_file():
                return config_path

        # Check if this is a git root
        if (current / ".git").exists():
            passed_git_root = True

        # Move to parent
        parent = current.parent

        # Stop conditions
        if parent == current:  # Reached filesystem root
            break
        if passed_git_root:  # Don't traverse above git root
            break

        current = parent

    return None


def _load_yaml_data(path: JoinablePathLike) -> dict[str, Any]:
    """Load YAML/JSON data from a file path.

    Args:
        path: Path to config file

    Returns:
        Parsed configuration data (an empty file gives an empty mapping)

    Raises:
        ValueError: If file cannot be loaded or parsed, or is not a mapping
    """
    try:
        data = yamling.load_yaml_file(
            path,
            resolve_inherit=True,  # Support INHERIT field
            enable_env=True,  # Support !ENV tags
        )
    except Exception as e:
        raise ValueError(f"Failed to load config from {path}: {e}") from e
    if data is None:
        return {}
    if not isinstance(data, dict):
        msg = f"Config {path} must be a mapping, got {type(data).__name__}"
        raise ValueError(msg)  # noqa: TRY004
    return data


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep merge two dictionaries.

    Values from override take precedence. Nested dicts are merged recursively.
    Lists are replaced, not concatenated (matching OpenCode behavior).

    Args:
        base: Base dictionary
        override: Dictionary with overriding values

    Returns:
        New merged dictionary
    """
    # Deep merge where override values win for conflicts
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def resolve_config(  # noqa: PLR0915
    explicit_path: JoinablePathLike | None = None,
    *,
    fallback_config: JoinablePathLike | None = None,
    project_dir: JoinablePathLike | None = None,
    include_global: bool = True,
    include_project: bool = True,
) -> ResolvedConfig:
    """Resolve configuration from all applicable layers.

    Loads and merges configuration from multiple sources in precedence order.
    Later sources override earlier ones for conflicting keys.

    The fallback_config is only used if NO other config defines any agents.
    This allows built-in defaults to provide a working configuration when
    the user hasn't configured anything, without polluting user configs.

    Args:
        explicit_path: Explicit config path (highest precedence)
        fallback_config: Fallback config used ONLY if no agents defined elsewhere
        project_dir: Directory to search for project config (defaults to cwd)
        include_global: Whether to include global config
        include_project: Whether to include project config

    Returns:
        ResolvedConfig with merged data and layer information

    Raises:
        ValueError: If explicit_path is provided but cannot be loaded
    """
    layers: list[ConfigLayer] = []
    merged_data: dict[str, Any] = {}
    primary_path: str | None = None

    # Check environment variable overrides for include flags
    if os.environ.get(ENV_DISABLE_GLOBAL):
        include_global = False
    if os.environ.get(ENV_DISABLE_PROJECT):
        include_project = False

    # 1. Global config
    if include_global:
        global_path = get_global_config_path()
        if global_path is not None:
            try:
                data = _load_yaml_data(global_path)
                layer = ConfigLayer("global", str(global_path), data)
                layers.append(layer)
                merged_data = _deep_merge(merged_data, data)
            except ValueError:
                pass  # Global config errors are non-fatal

    # 2. Custom config from environment variable
    custom_path = os.environ.get(ENV_CONFIG_PATH)
    if custom_path:
        try:
            data = _load_yaml_data(custom_path)
            layer = ConfigLayer("custom", custom_path, data)
            layers.append(layer)
            merged_data = _deep_merge(merged_data, data)
            primary_path = custom_path  # Custom config can be primary
        except ValueError:
            pass  # Custom config errors are non-fatal

    # 2b. Inline config from environment variable (highest custom precedence)
    inline_content = os.environ.get(ENV_CONFIG_CONTENT)
    if inline_content:
        try:
            import yaml

            data = yaml.safe_load(inline_content)
            if isinstance(data, dict):
                layer = ConfigLayer("custom", None, data)
                layers.append(layer)
                merged_data = _deep_merge(merged_data, data)
        except yaml.YAMLError, TypeError, ValueError:
            pass  # Inline config errors are non-fatal

    # 3. Project config
    if include_project and explicit_path is None:
        project_path = find_project_config(project_dir)
        if project_path is not None:
            try:
                data = _load_yaml_data(project_path)
                layer = ConfigLayer("project", str(project_path), data)
                layers.append(layer)
                merged_data = _deep_merge(merged_data, data)
                primary_path = str(project_path)
            except ValueError:
                pass  # Project config errors are non-fatal

    # 4. Explicit config (highest precedence)
    if explicit_path is not None:
        # Explicit config MUST load successfully
        data = _load_yaml_data(explicit_path)
        layer = ConfigLayer("explicit", str(explicit_path), data)
        layers.append(layer)
        merged_data = _deep_merge(merged_data, data)
        primary_path = str(explicit_path)

    # 5. Fallback config - ONLY if no agents defined in any layer
    # This ensures built-in defaults don't pollute user configurations
    has_agents = bool(merged_data.get("agents")) or bool(merged_data.get("file_agents"))
    if not has_agents and fallback_config is not None:
        try:
            data = _load_yaml_data(fallback_config)
            layer = ConfigLayer("builtin", str(fallback_config), data)
            # Prepend to layers (lowest precedence conceptually)
            layers.insert(0, layer)
            # Merge fallback as base, then re-apply user data on top
            merged_data = _deep_merge(data, merged_data)
            if primary_path is None:
                primary_path = str(fallback_config)
        except ValueError:
            pass  # Fallback config errors are non-fatal

    return ResolvedConfig(
        data=merged_data,
        layers=layers,
        primary_path=primary_path,
    )


def resolve_config_for_server(
    explicit_path: JoinablePathLike | None = None,
    *,
    fallback_config: JoinablePathLike | None = None,
    project_dir: JoinablePathLike | None = None,
) -> ResolvedConfig:
    """Resolve configuration for server commands (ACP, OpenCode, etc.).

    This is a convenience wrapper that sets appropriate defaults for server usage:
    - Includes global config for user preferences
    - Includes project config for project-specific settings
    - Uses fallback config only if no agents defined elsewhere

    Args:
        explicit_path: Explicit config path from CLI
        fallback_config: Fallback config used only if no agents defined
        project_dir: Directory to search for project config

    Returns:
        ResolvedConfig with merged data
    """
    return resolve_config(
        explicit_path=explicit_path,
        fallback_config=fallback_config,
        project_dir=project_dir,
        include_global=True,
        include_project=True,
    )


def get_config_search_paths() -> list[tuple[str, Path | None]]:
    """Get all config search paths for diagnostic purposes.

    Returns:
        List of (source_name, path_or_none) tuples showing where configs are searched
    """
    paths: list[tuple[str, Path | None]] = []
    # Global
    global_path = get_global_config_path()
    paths.append(("global", global_path))
    # Custom from env
    custom_path = os.environ.get(ENV_CONFIG_PATH)
    paths.append(("custom (env)", Path(custom_path) if custom_path else None))
    # Project
    project_path = find_project_config()
    paths.append(("project", project_path))
    return paths


def ensure_global_config_dir() -> Path:
    """Ensure global config directory exists.

    Returns:
        Path to the global config directory
    """
    config_dir = get_global_config_dir()
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir
