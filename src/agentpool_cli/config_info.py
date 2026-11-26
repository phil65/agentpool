"""Command for displaying configuration resolution information."""

from typing import Annotated

import typer as t

from agentpool_cli import log


logger = log.get_logger(__name__)

config_cli = t.Typer(help="Configuration management commands", no_args_is_help=True)


@config_cli.command("show")
def show_config(
    explicit_path: Annotated[
        str | None,
        t.Argument(help="Optional explicit config path to include"),
    ] = None,
    format_output: Annotated[
        str,
        t.Option("--format", "-f", help="Output format: text, json, yaml"),
    ] = "text",
) -> None:
    """Show configuration resolution status."""
    from agentpool_config.resolution import get_config_search_paths, resolve_config

    search_paths = get_config_search_paths()

    if format_output == "text":
        t.echo("Configuration Search Paths:")
        t.echo("-" * 40)
        for source_name, path in search_paths:
            status = "found" if path else "not found"
            path_str = str(path) if path else "(none)"
            t.echo(f"  {source_name}: {path_str} [{status}]")

        t.echo()
        try:
            resolved = resolve_config(explicit_path=explicit_path)
        except ValueError as e:
            t.echo(f"Error resolving config: {e}", err=True)
            raise t.Exit(1) from e

        t.echo("Loaded Layers:")
        t.echo("-" * 40)
        if not resolved.layers:
            t.echo("  (no layers loaded)")
        else:
            for layer in resolved.layers:
                path_info = layer.path or "(inline)"
                t.echo(f"  [{layer.source}] {path_info}")

        t.echo()
        t.echo(f"Primary path: {resolved.primary_path or '(none)'}")
        if resolved.data:
            t.echo(f"Top-level keys: {', '.join(sorted(resolved.data.keys()))}")

    elif format_output in ("json", "yaml"):
        import anyenv
        import yamling

        result = {
            "search_paths": {name: str(path) if path else None for name, path in search_paths},
            "layers": [
                {"source": layer.source, "path": layer.path}
                for layer in resolve_config(explicit_path=explicit_path).layers
            ],
        }
        if format_output == "json":
            t.echo(anyenv.dump_json(result, indent=True))
        else:
            t.echo(yamling.dump_yaml(result, indent=2))


@config_cli.command("paths")
def show_paths() -> None:
    """Show standard configuration paths."""
    import os
    from pathlib import Path

    from agentpool_config.resolution import (
        CONFIG_FILE_NAMES,
        ENV_CONFIG_PATH,
        find_project_config,
        get_global_config_dir,
        get_global_config_path,
    )

    t.echo("Global Config Directory:")
    t.echo(f"  {get_global_config_dir()}")
    global_config = get_global_config_path()
    if global_config:
        t.echo(f"  Found: {global_config.name}")
    else:
        t.echo(f"  Not found. Create one of: {', '.join(CONFIG_FILE_NAMES)}")

    t.echo()
    t.echo("Project Config:")
    project_config = find_project_config()
    if project_config:
        t.echo(f"  Found: {project_config}")
    else:
        t.echo(f"  Not found (searched from {Path.cwd()})")

    t.echo()
    t.echo("Environment Variables:")
    custom = os.environ.get(ENV_CONFIG_PATH)
    t.echo(f"  AGENTPOOL_CONFIG: {custom or '(not set)'}")


@config_cli.command("init")
def init_config(
    location: Annotated[
        str,
        t.Argument(help="Where to create: 'global', 'project', or a path"),
    ] = "project",
    force: Annotated[bool, t.Option("--force", "-f")] = False,
) -> None:
    """Initialize a new configuration file."""
    from pathlib import Path

    from agentpool_config.resolution import ensure_global_config_dir

    if location == "global":
        target = ensure_global_config_dir() / "agentpool.yml"
    elif location == "project":
        target = Path.cwd() / "agentpool.yml"
    else:
        target = Path(location)

    if target.exists() and not force:
        t.echo(f"Config exists: {target}. Use --force to overwrite.", err=True)
        raise t.Exit(1)

    starter = """# AgentPool Configuration
# agents:
#   assistant:
#     type: native
#     model: anthropic:claude-sonnet-4-5
"""
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(starter)
    t.echo(f"Created: {target}")


if __name__ == "__main__":
    config_cli()
