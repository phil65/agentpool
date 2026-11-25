"""Project tasks."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Literal

from duty import duty  # pyright: ignore[reportMissingImports]  # ty:ignore[import-not-found]


# Read configuration from copier-answers.yml
_answers_file = Path(".copier-answers.yml")
if _answers_file.exists():
    content = _answers_file.read_text()
    match = re.search(r"^python_package_import_name:\s*(.+)$", content, re.MULTILINE)
    if match:
        PACKAGE_NAME = match.group(1).strip()
    else:
        msg = "python_package_import_name not found in copier-answers.yml"
        raise ValueError(msg)
else:
    msg = "copier-answers.yml not found in project root"
    raise FileNotFoundError(msg)


@duty(capture=False)
def build(ctx, *args: str):
    """Build documentation."""
    import subprocess

    args_str = " " + " ".join(args) if args else ""
    result = subprocess.run(
        f"uv run zensical build{args_str}",
        check=False,
        shell=True,
        capture_output=True,
        text=True,
    )
    # Print output
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr)
    # Check for errors in output (mkdocs may exit 0 even with template errors)
    error_patterns = ["Error:", "error:", "template not found"]
    combined_output = (result.stdout or "") + (result.stderr or "")
    for pattern in error_patterns:
        if pattern in combined_output:
            msg = f"Build failed: found '{pattern}' in output"
            raise RuntimeError(msg)
    if result.returncode != 0:
        msg = f"Build failed with exit code {result.returncode}"
        raise RuntimeError(msg)
    ctx.run("uv run python scripts/reorder_nav.py")


@duty(capture=False)
def serve(ctx, *args: str):
    """Serve documentation. Pass --reorder to build, reorder nav, and serve static files.

    With --reorder, also accepts --port=XXXX (default: 8000).
    """
    reorder = "--reorder" in args
    args = tuple(a for a in args if a != "--reorder")

    # Extract --port for reorder mode
    port = "8000"
    new_args = []
    for arg in args:
        if arg.startswith("--port="):
            port = arg.split("=", 1)[1]
        else:
            new_args.append(arg)
    args = tuple(new_args)

    if reorder:
        ctx.run("uv run zensical build")
        ctx.run("uv run python scripts/reorder_nav.py")
        # Serve the static site directly (zensical serve would rebuild and undo reorder)
        # Create a temp structure to match zensical's URL scheme: /agentpool/
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            (tmp_path / "agentpool").symlink_to(Path("site").resolve())
            print(f"Serving at http://localhost:{port}/agentpool/")
            ctx.run(f"uv run python -m http.server {port} -d {tmpdir} -b localhost")
    else:
        args_str = " " + " ".join(args) if args else ""
        ctx.run(f"uv run zensical serve{args_str}")


@duty(capture=False)
def test(ctx, *args: str):
    """Run tests."""
    args_str = " " + " ".join(args) if args else ""
    args_str = " -n auto" + args_str
    ctx.run(f"uv run pytest{args_str}")


@duty(capture=False)
def clean(ctx):
    """Clean all files from the Git directory except checked-in files."""
    ctx.run("git clean -dfX")


@duty(capture=False)
def update(ctx):
    """Update all environment packages using pip directly."""
    ctx.run("uv lock --upgrade")
    ctx.run("uv sync --all-extras")


def _get_lint_targets(filepath: str | None) -> tuple[str, str, str | None]:
    """Get lint targets based on optional filepath.

    Returns:
        Tuple of (ruff_target, ty_target, jsonschema_files_or_none)
    """
    if filepath is None:
        return (
            ".",
            "src/",
            "src/agentpool/config_resources/*.yml docs/examples/**/config.yml",
        )

    path = Path(filepath)

    # For ruff, use the file directly
    ruff_target = filepath

    # For ty, if it's under src/, use the file; otherwise skip
    ty_target = filepath if filepath.startswith("src/") else ""

    # For jsonschema, only run if the file matches the patterns
    jsonschema_files = None
    if path.suffix in {".yml", ".yaml"} and (
        "config_resources" in filepath
        or ("docs/examples" in filepath and path.name == "config.yml")
    ):
        jsonschema_files = filepath

    return ruff_target, ty_target, jsonschema_files


@duty(capture=False)
def lint(ctx, filepath: str | None = None):  # noqa: D417
    """Lint the code and fix issues if possible.

    Args:
        filepath: Optional path to a specific file to lint.
                  If not provided, lints the entire project.
    """
    ruff_target, ty_target, jsonschema_files = _get_lint_targets(filepath)

    ctx.run(f"uv run ruff check --fix --unsafe-fixes {ruff_target}")
    ctx.run(f"uv run ruff format {ruff_target}")

    if ty_target:
        ctx.run(f"uv run ty check {ty_target}")

    if jsonschema_files:
        ctx.run(
            f"uv run check-jsonschema --schemafile schema/config-schema.json {jsonschema_files}"
        )
    elif filepath is None:
        # Full project lint - run jsonschema on all config files
        ctx.run(
            "uv run check-jsonschema --schemafile schema/config-schema.json "
            "src/agentpool/config_resources/*.yml "
            "docs/examples/**/config.yml"
        )


@duty(capture=False)
def lint_check(ctx, filepath: str | None = None):  # noqa: D417
    """Lint the code (check only, no fixes).

    Args:
        filepath: Optional path to a specific file to lint.
                  If not provided, lints the entire project.
    """
    ruff_target, ty_target, jsonschema_files = _get_lint_targets(filepath)

    ctx.run(f"uv run ruff check {ruff_target}")
    ctx.run(f"uv run ruff format --check {ruff_target}")

    if ty_target:
        ctx.run(f"uv run ty check {ty_target}")

    if jsonschema_files:
        ctx.run(
            f"uv run check-jsonschema --schemafile schema/config-schema.json {jsonschema_files}"
        )
    elif filepath is None:
        # Full project lint - run jsonschema on all config files
        ctx.run(
            "uv run check-jsonschema --schemafile schema/config-schema.json "
            "src/agentpool/config_resources/*.yml "
            "docs/examples/**/config.yml"
        )


@duty(capture=False)
def version(
    ctx,
    *bump_type: Literal["major", "minor", "patch", "stable", "alpha", "beta", "rc"],
):
    """Release a new version with git operations. (major|minor|patch|stable|alpha|beta|rc)."""
    # Check for uncommitted changes
    result = ctx.run("git status --porcelain", capture=True)
    if result.strip():
        msg = "Cannot release with uncommitted changes. Please commit or stash first."
        raise RuntimeError(msg)

    # Read current version
    old_version = ctx.run("uv version --short", capture=True).strip()
    print(f"Current version: {old_version}")
    bump_str = " ".join(f"--bump {i}" for i in bump_type)
    ctx.run(f"uv version {bump_str}")
    new_version = ctx.run("uv version --short", capture=True).strip()
    print(f"New version: {new_version}")
    ctx.run("uv lock")

    # Update extension.toml
    ext_toml = Path("distribution/zed/extension.toml")
    if ext_toml.exists():
        content = ext_toml.read_text()
        content = content.replace(old_version, new_version)
        ext_toml.write_text(content)
        ctx.run("git add distribution/zed/extension.toml")

    ctx.run("git add pyproject.toml uv.lock")
    ctx.run(f'git commit -m "chore: bump version {old_version} -> {new_version}"')

    # Create and push tag
    tag = f"v{new_version}"
    ctx.run(f"git tag {tag}")
    print(f"Created tag: {tag}")


@duty(capture=False)
def smoke_test(ctx, timeout: int = 10):  # noqa: D417
    """Build wheel and verify serve-acp starts successfully via uvx.

    Builds the package, installs it in an isolated uvx environment,
    and checks that serve-acp stays alive for the given timeout.
    Exit code 124 from timeout means the process was still running (success).
    Any other exit code means it crashed (failure).

    Args:
        timeout: Seconds to wait before considering the server healthy.
    """
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        # Build wheel
        ctx.run(f"uv build --wheel --out-dir {tmpdir}")

        # Find the built wheel
        wheels = list(Path(tmpdir).glob("*.whl"))
        if not wheels:
            msg = "No wheel found after build"
            raise RuntimeError(msg)
        wheel = wheels[0]
        print(f"Built: {wheel.name}")

        # Run serve-acp from the wheel in an isolated environment
        result = subprocess.run(  # noqa: PLW1510
            ["timeout", str(timeout), "uvx", "--from", str(wheel), "agentpool", "serve-acp"],
            capture_output=True,
            text=True,
        )

        if result.returncode == 124:  # noqa: PLR2004
            print(f"serve-acp stayed alive for {timeout}s (healthy)")
        else:
            print(f"serve-acp exited with code {result.returncode}")
            if result.stdout:
                print(f"stdout: {result.stdout}")
            if result.stderr:
                print(f"stderr: {result.stderr}")
            msg = f"serve-acp crashed (exit code {result.returncode})"
            raise RuntimeError(msg)


@duty(capture=False)
def schema_html(ctx):
    """Create HTML documentation for JSON schema."""
    ctx.run(
        "generate-schema-doc "
        "--config template_name=js "
        "--config expand_buttons=true "
        "schema/config-schema.json "
        "docs/schema/index.html"
    )


@duty(capture=False)
def opencode_server(ctx, *args: str):
    """Start the OpenCode-compatible API server.

    Usage:
        duty opencode-server                    # Start on default port 4096
        duty opencode-server --port 8080        # Start on custom port
    """
    args_str = " " + " ".join(args) if args else ""
    ctx.run(f"uv run agentpool serve-opencode{args_str}")


@duty(capture=False)
def bump_opencode(ctx, version: str):
    """Pin another OpenCode V2 version: download its spec, regenerate the models, run the tests.

    Then review the spec diff and adapt server, agent and client (see AGENTS.md,
    "Updating the OpenCode version").

    Usage:
        duty bump-opencode 2.4.1
    """
    ctx.run(f"uv run python scripts/generate_opencode_v2_models.py --version {version}")
    ctx.run("git diff --stat -- schema/opencode-v2 src/opencode_v2_sdk")
    ctx.run("uv run pytest -q tests/servers/opencode_v2_server tests/agents/opencode_v2_agent")
