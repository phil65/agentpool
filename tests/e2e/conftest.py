"""L4 subprocess E2E test fixtures for AgentPool protocol servers.

This conftest provides the ``subprocess_server`` fixture that spawns a real
``wolfharness serve-*`` process, waits for it to become healthy, and tears it
down gracefully on test exit.

Design decisions (see ``openspec/changes/layered-testing-infrastructure/design.md``):
- D14: Process spawn via ``asyncio.create_subprocess_exec`` with PIPE'd stdio.
- D14: Ephemeral ports via ``socket.bind(("", 0))`` for HTTP servers.
- D14: Health check polling with 5s timeout.
- D14: Graceful shutdown via SIGTERM → wait(5s) → SIGKILL fallback.
- D17: L4a smoke tests use ``model: test`` (pydantic-ai TestModel) so NO API
  key is needed. L4a tests use TestModel (not real_model), so real_model
  auto-skip does NOT apply. L4a should always run when
  ``-m "e2e and not slow"`` is selected.

L4a smoke tests: pytest -m "e2e and not slow" (~30s)
L4b full tests: pytest -m e2e
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
from typing import TYPE_CHECKING, Any

import pytest
import yaml


if TYPE_CHECKING:
    from collections.abc import AsyncIterator


# ---------------------------------------------------------------------------
# Skip conditions
# ---------------------------------------------------------------------------


def _wolfharness_binary_available() -> bool:
    """Check whether the ``wolfharness`` CLI binary is on PATH."""
    return shutil.which("wolfharness") is not None


SKIP_NO_BINARY = not _wolfharness_binary_available()
SKIP_WINDOWS = sys.platform == "win32"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class SubprocessServer:
    """Handle to a spawned ``wolfharness serve-*`` subprocess.

    Attributes:
        process: The asyncio subprocess.
        host: Bind host (for HTTP servers; empty for stdio).
        port: Bind port (0 for stdio servers).
        stderr_text: Captured stderr output (populated on teardown).
        is_stdio: Whether this is a stdio-based server (ACP default transport).
    """

    process: asyncio.subprocess.Process | subprocess.Popen
    host: str
    port: int
    stderr_text: str = ""
    is_stdio: bool = False

    @property
    def base_url(self) -> str:
        """Base URL for HTTP servers (empty for stdio)."""
        if self.is_stdio:
            return ""
        return f"http://{self.host}:{self.port}"

    @property
    def returncode(self) -> int | None:
        """Process return code (None if still running)."""
        return self.process.returncode


@dataclass
class ProcessRegistry:
    """Session-scoped registry of spawned subprocesses for cleanup verification."""

    processes: list[asyncio.subprocess.Process | subprocess.Popen] = field(default_factory=list)

    def register(self, process: asyncio.subprocess.Process | subprocess.Popen) -> None:
        self.processes.append(process)

    def all_terminated(self) -> bool:
        return all(p.returncode is not None for p in self.processes)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def allocate_ephemeral_port() -> int:
    """Allocate an ephemeral port via socket bind then immediately release it.

    Returns:
        A free port number suitable for passing to ``--port``.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("", 0))
        return sock.getsockname()[1]


def _build_command(
    serve_command: str,
    config_path: str,
    *,
    host: str,
    port: int,
    extra_args: list[str] | None = None,
) -> list[str]:
    """Build the full CLI command list.

    Args:
        serve_command: The serve subcommand (e.g. ``serve-acp``).
        config_path: Path to the YAML config file.
        host: Bind host for HTTP servers.
        port: Bind port for HTTP servers.
        extra_args: Additional CLI arguments.
    """
    cmd: list[str] = ["wolfharness", serve_command, str(config_path)]
    if serve_command == "serve-acp":
        # ACP defaults to stdio; use streamable-http for HTTP-based e2e.
        # Caller must pass --transport via extra_args if HTTP is desired.
        pass
    else:
        # HTTP servers accept --host and --port.
        cmd.extend(["--host", host, "--port", str(port)])
    if extra_args:
        cmd.extend(extra_args)
    return cmd


async def _health_check_http(
    host: str,
    port: int,
    *,
    path: str = "/",
    timeout: float = 5.0,
    interval: float = 0.5,
) -> bool:
    """Poll an HTTP server until it responds or timeout.

    Args:
        host: Server host.
        port: Server port.
        path: Health check endpoint path (default ``/``).
        timeout: Maximum seconds to wait.
        interval: Polling interval in seconds.
    """
    import httpx

    url = f"http://{host}:{port}{path}"
    deadline = asyncio.get_event_loop().time() + timeout
    last_error: Exception | None = None
    while asyncio.get_event_loop().time() < deadline:
        try:
            async with httpx.AsyncClient(timeout=interval) as client:
                resp = await client.get(url)
                # Any HTTP response (even 404) means the server is up.
                if resp.status_code < 500:
                    return True
        except (httpx.ConnectError, httpx.ConnectTimeout, OSError) as exc:
            last_error = exc
            await asyncio.sleep(interval)
    if last_error:
        pass  # pragma: no cover - debug only
    return False


async def _health_check_stdio(process: asyncio.subprocess.Process, timeout: float = 5.0) -> bool:
    """For stdio servers, verify the process is alive and has not exited early.

    A stdio ACP server doesn't have a health endpoint. We consider it healthy
    if it's still running after a brief stabilization period and hasn't written
    an error to stderr.

    Args:
        process: The spawned subprocess.
        timeout: Stabilization period in seconds.
    """
    # Give the process a moment to start up.
    await asyncio.sleep(0.5)
    if process.returncode is not None:
        return False
    # Wait a bit more to catch early crashes.
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if process.returncode is not None:
            return False
        await asyncio.sleep(0.25)
    return process.returncode is None


async def _terminate_process(process: asyncio.subprocess.Process | subprocess.Popen) -> str:
    """Terminate a subprocess gracefully: SIGTERM → wait(5s) → SIGKILL.

    Returns:
        Captured stderr text.
    """
    if isinstance(process, subprocess.Popen):
        return _terminate_popen(process)

    stderr_text = ""
    if process.returncode is not None:
        # Already exited; drain stderr.
        try:
            _stdout_bytes, stderr_bytes = await asyncio.wait_for(process.communicate(), timeout=2.0)
            stderr_text = stderr_bytes.decode("utf-8", errors="replace")
        except (TimeoutError, ProcessLookupError, OSError):
            pass
        return stderr_text

    # Send SIGTERM (or terminate() on Windows).
    try:
        if sys.platform == "win32":
            process.terminate()
        else:
            process.send_signal(signal.SIGTERM)
    except ProcessLookupError:
        # Already gone.
        pass

    try:
        await asyncio.wait_for(process.wait(), timeout=5.0)
    except TimeoutError:
        # SIGKILL fallback.
        try:
            process.kill()
            await asyncio.wait_for(process.wait(), timeout=2.0)
        except (TimeoutError, ProcessLookupError):
            pass

    # Drain stderr.
    try:
        if process.stderr:
            stderr_data = await process.stderr.read()
            stderr_text = stderr_data.decode("utf-8", errors="replace")
    except (OSError, RuntimeError):
        pass

    return stderr_text


def _terminate_popen(process: subprocess.Popen, timeout: float = 5.0) -> str:
    """Terminate a Popen subprocess gracefully: SIGTERM → wait(timeout) → SIGKILL.

    Returns:
        Empty string (stderr is DEVNULL for cached servers).
    """
    if process.returncode is not None:
        return ""
    try:
        if sys.platform == "win32":
            process.terminate()
        else:
            process.send_signal(signal.SIGTERM)
    except ProcessLookupError:
        return ""
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
            process.wait(timeout=2.0)
        except (subprocess.TimeoutExpired, ProcessLookupError, OSError):
            pass
    return ""


# ---------------------------------------------------------------------------
# Config fixture (10.7)
# ---------------------------------------------------------------------------


DEFAULT_CONFIG_YAML = """
agents:
  test_agent:
    type: native
    model: test
    system_prompt: "You are a test assistant."
"""


@pytest.fixture
def e2e_config(tmp_path: Path) -> Path:
    """Create a temporary YAML config with ``model: test`` (TestModel).

    The config defines a single native agent named ``test_agent`` that uses
    pydantic-ai's TestModel, so NO API key is needed. TestModel returns a
    deterministic response that exercises the full event pipeline.

    Returns:
        Path to the temporary YAML config file.
    """
    config_path = tmp_path / "e2e_config.yml"
    config_path.write_text(DEFAULT_CONFIG_YAML.strip() + "\n")
    return config_path


@pytest.fixture
def e2e_multi_agent_config(tmp_path: Path) -> Path:
    """YAML config with 2 agents (coordinator + worker) using TestModel.

    Returns:
        Path to the temporary YAML config file.
    """
    config = tmp_path / "multi_agent_config.yml"
    config.write_text("""\
agents:
  coordinator:
    type: native
    model: test
    system_prompt: "You are a coordinator agent."
  worker:
    type: native
    model: test
    system_prompt: "You are a worker agent."
""")
    return config


@pytest.fixture
def e2e_config_with_tool(tmp_path: Path) -> Path:
    """Create a temporary YAML config with a simple tool enabled.

    Returns:
        Path to the temporary YAML config file.
    """
    config = {
        "agents": {
            "test_agent": {
                "type": "native",
                "model": {
                    "type": "test",
                    "call_tools": ["bash"],
                    "tool_args": {"bash": {"command": "echo hello"}},
                },
                "system_prompt": "You are a test assistant with tools.",
                "tools": [{"type": "bash"}],
            }
        }
    }
    config_path = tmp_path / "e2e_config_tool.yml"
    config_path.write_text(yaml.dump(config, default_flow_style=False))
    return config_path


@pytest.fixture(scope="session")
def session_e2e_config(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Session-scoped e2e config with in-memory storage.

    Written once to a stable temp path via tmp_path_factory.
    Includes storage: {providers: [{type: memory}]} to eliminate cross-run SQLite leakage.
    """
    config_dir = tmp_path_factory.mktemp("e2e")
    config_path = config_dir / "e2e_config.yml"
    config_content = DEFAULT_CONFIG_YAML.strip() + "\n"
    config_content += "storage:\n  providers:\n    - type: memory\n"
    config_path.write_text(config_content)
    return config_path


@pytest.fixture(scope="session")
def session_e2e_config_with_tool(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Session-scoped e2e config with bash tool and in-memory storage."""
    config_dir = tmp_path_factory.mktemp("e2e")
    config_path = config_dir / "e2e_config_tool.yml"
    config = {
        "agents": {
            "test_agent": {
                "type": "native",
                "model": {
                    "type": "test",
                    "call_tools": ["bash"],
                    "tool_args": {"bash": {"command": "echo hello"}},
                },
                "system_prompt": "You are a test assistant with tools.",
                "tools": [{"type": "bash"}],
            }
        },
        "storage": {"providers": [{"type": "memory"}]},
    }
    config_path.write_text(yaml.dump(config, default_flow_style=False))
    return config_path


@pytest.fixture(scope="session")
def session_e2e_config_with_mcp(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Session-scoped e2e config with a fake stdio MCP server.

    Defines a single native agent (``test_agent`` using TestModel) and one
    pool-level stdio MCP server (``fake_kb``) backed by
    ``tests/fixtures/fake_mcp_server.py``. The fake server is built with
    ``fastmcp`` and exposes a single ``search_kb`` tool; it reports
    ``serverInfo.name = "fake-kb-server"`` and ``version = "0.1.0"`` via
    the MCP initialize handshake.

    Uses ``sys.executable`` as the command so the subprocess inherits the
    active venv (where ``fastmcp`` is installed). The fixture path is
    resolved relative to this conftest file to work regardless of the
    pytest rootdir.

    Includes ``storage: {providers: [{type: memory}]}`` to eliminate
    cross-run SQLite leakage.
    """
    config_dir = tmp_path_factory.mktemp("e2e_mcp")
    config_path = config_dir / "e2e_config_mcp.yml"
    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures"
    fake_server_path = fixtures_dir / "fake_mcp_server.py"
    config = {
        "agents": {
            "test_agent": {
                "type": "native",
                "model": "test",
                "system_prompt": "You are a test assistant.",
                "resources": {"enabled": True},
            }
        },
        "mcp_servers": [
            {
                "name": "fake_kb",
                "type": "stdio",
                "command": sys.executable,
                "args": [str(fake_server_path)],
                "enabled": True,
            }
        ],
        "storage": {"providers": [{"type": "memory"}]},
    }
    config_path.write_text(yaml.dump(config, default_flow_style=False))
    return config_path


# ---------------------------------------------------------------------------
# Subprocess server cache infrastructure
# ---------------------------------------------------------------------------


# Session-scoped cache of subprocess servers, keyed by
# (serve_command, is_stdio, health_path, str(extra_args), config_type).
# config_type is "default" or "with_tool" to distinguish config variants.
_server_cache: dict[tuple[str, bool, str, str, str], subprocess.Popen] = {}

_xdist_cache_disabled: dict[str, bool] = {"disabled": False}


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add --no-server-cache command-line option."""
    parser.addoption(
        "--no-server-cache",
        action="store_true",
        default=False,
        help="Disable subprocess server cache (each test spawns its own server).",
    )


def pytest_configure(config: pytest.Config) -> None:
    """Auto-disable cache if xdist is detected."""
    numprocesses = config.getoption("numprocesses", default=0)
    if numprocesses and numprocesses != 0:
        _xdist_cache_disabled["disabled"] = True
        import warnings

        warnings.warn(
            "pytest-xdist detected; subprocess server cache auto-disabled.",
            stacklevel=2,
        )


async def _clear_sessions(base_url: str) -> None:
    """Clear all sessions on an OpenCode server via HTTP.

    GET /session then DELETE each session. Catches 404/405 gracefully
    (treat as 'no sessions to clear'). Uses httpx.AsyncClient.
    """
    import httpx

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{base_url}/session")
            if resp.status_code in (404, 405):
                return  # No session endpoint (stateless server)
            if resp.status_code >= 500:
                return  # Server error, skip cleanup
            sessions = resp.json()
            for session in sessions:
                session_id = session.get("id") or session.get("session_id")
                if session_id:
                    with contextlib.suppress(httpx.HTTPStatusError, httpx.RequestError):
                        await client.delete(f"{base_url}/session/{session_id}")
    except (httpx.RequestError, OSError):
        pass  # Server may not have /session endpoint


# ---------------------------------------------------------------------------
# subprocess_server fixture (10.1)
# ---------------------------------------------------------------------------


@pytest.fixture
async def subprocess_server(  # noqa: PLR0915
    request: pytest.FixtureRequest,
    process_registry: ProcessRegistry,
    session_e2e_config: Path,
    allow_model_requests: Any,
) -> AsyncIterator[SubprocessServer]:
    """Spawn an ``wolfharness serve-*`` subprocess, reusing cached servers when possible.

    Cache bypass conditions:
    - ``--no-server-cache`` flag is set
    - ``is_stdio=True`` (ACP stdio connections are stateful)
    - ``@pytest.mark.isolated`` is set on the test
    - pytest-xdist is enabled
    - A custom ``config_path`` is explicitly provided in params

    For cached servers: uses ``subprocess.Popen`` (event-loop-agnostic),
    ``_health_check_socket`` for health, and ``_clear_sessions`` for state reset.

    Parametrize via ``indirect=True`` with a dict of parameters:

    .. code-block:: python

        @pytest.mark.parametrize(
            "subprocess_server",
            [{"serve_command": "serve-api", "extra_args": []}],
            indirect=True,
        )
        async def test_x(subprocess_server: SubprocessServer) -> None:
            ...

    Required param keys:
        - ``serve_command``: The CLI subcommand (e.g. ``serve-acp``,
          ``serve-opencode``, ``serve-agui``, ``serve-api``).

    Optional param keys:
        - ``config_path``: Path to the YAML config file (bypasses cache if set).
        - ``host``: Bind host (default ``127.0.0.1``, ignored for stdio ACP).
        - ``port``: Bind port (default: ephemeral, ignored for stdio ACP).
        - ``extra_args``: Additional CLI arguments.
        - ``is_stdio``: If True, treat as stdio server (no HTTP health check).
        - ``health_timeout``: Health check timeout in seconds (default 10.0).

    Yields:
        SubprocessServer handle.
    """
    params = getattr(request, "param", None)
    if params is None:
        msg = "subprocess_server fixture requires parametrize(indirect=True) with a dict"
        raise TypeError(msg)

    serve_command: str = params["serve_command"]
    host: str = params.get("host", "127.0.0.1")
    is_stdio: bool = params.get("is_stdio", False)
    health_timeout: float = params.get("health_timeout", 10.0)
    health_path: str = params.get("health_path", "/")
    extra_args: list[str] | None = params.get("extra_args")

    # Determine if cache should be bypassed
    no_cache = (
        bool(request.config.getoption("--no-server-cache", default=False))
        or is_stdio
        or _xdist_cache_disabled["disabled"]
        or request.node.get_closest_marker("isolated") is not None
        or "config_path" in params
    )

    config_path: str = str(params.get("config_path", session_e2e_config))

    if no_cache:
        # --- Non-cached path: existing asyncio flow (unchanged) ---
        if is_stdio:
            port = 0
            cmd = _build_command(
                serve_command, config_path, host=host, port=port, extra_args=extra_args
            )
        else:
            port = params.get("port") or allocate_ephemeral_port()
            cmd = _build_command(
                serve_command, config_path, host=host, port=port, extra_args=extra_args
            )

        env = os.environ.copy()
        env["OBSERVABILITY_ENABLED"] = "false"
        env["LOGFIRE_DISABLE"] = "true"

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        process_registry.register(process)

        server = SubprocessServer(
            process=process,
            host=host,
            port=port,
            is_stdio=is_stdio,
        )

        if is_stdio:
            healthy = await _health_check_stdio(process, timeout=health_timeout)
        else:
            healthy = await _health_check_http(
                host, port, path=health_path, timeout=health_timeout, interval=0.5
            )

        if not healthy:
            stderr_text = await _terminate_process(process)
            server.stderr_text = stderr_text
            msg = (
                f"Server {serve_command} failed to become healthy within "
                f"{health_timeout}s.\nCommand: {' '.join(cmd)}\n"
                f"STDERR:\n{stderr_text}"
            )
            raise RuntimeError(msg)

        yield server

        stderr_text = await _terminate_process(process)
        server.stderr_text = stderr_text
        return

    # --- Cached path ---
    cache_key = (serve_command, is_stdio, health_path, str(extra_args), "default")

    # Check cache for existing server
    cached_popen = _server_cache.get(cache_key)

    if cached_popen is not None:
        # Cache hit: check if process is still alive (Task 3.3)
        if cached_popen.poll() is not None:
            # Crashed — remove from cache and fall through to spawn
            _server_cache.pop(cache_key, None)
            cached_popen = None
        else:
            # Process alive — quick socket health check (Task 3.4)
            port = cached_popen._e2e_port  # type: ignore[attr-defined]
            healthy = await _health_check_socket(host, port, timeout=2.0)
            if not healthy:
                # Stale — terminate and re-spawn
                _terminate_popen(cached_popen)
                _server_cache.pop(cache_key, None)
                cached_popen = None

    if cached_popen is not None:
        # Cache hit: clear sessions for OpenCode servers (Task 3.5)
        cached_port = cached_popen._e2e_port  # type: ignore[attr-defined]
        base_url = f"http://{host}:{cached_port}"
        if serve_command == "serve-opencode":
            await _clear_sessions(base_url)

        server = SubprocessServer(
            process=cached_popen,
            host=host,
            port=cached_port,
            is_stdio=False,
        )
        yield server
        # Do NOT terminate cached servers (Task 3.6)
        return

    # Cache miss: spawn new server via Popen (Task 3.2)
    port = allocate_ephemeral_port()
    cmd = _build_command(
        serve_command,
        str(session_e2e_config),
        host=host,
        port=port,
        extra_args=extra_args,
    )

    env = os.environ.copy()
    env["OBSERVABILITY_ENABLED"] = "false"
    env["LOGFIRE_DISABLE"] = "true"

    popen = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    # Store port on the Popen object for later retrieval
    popen._e2e_port = port  # type: ignore[attr-defined]
    popen._e2e_host = host  # type: ignore[attr-defined]

    process_registry.register(popen)

    # Health check via raw socket (Task 3.2)
    healthy = await _health_check_socket(host, port, timeout=health_timeout)
    if not healthy:
        _terminate_popen(popen)
        msg = (
            f"Server {serve_command} failed to become healthy within "
            f"{health_timeout}s.\nCommand: {' '.join(cmd)}"
        )
        raise RuntimeError(msg)

    # Store in cache
    _server_cache[cache_key] = popen

    # Clear sessions for OpenCode (first use of cached server)
    if serve_command == "serve-opencode":
        await _clear_sessions(f"http://{host}:{port}")

    server = SubprocessServer(
        process=popen,
        host=host,
        port=port,
        is_stdio=False,
    )
    yield server
    # Do NOT terminate cached servers (Task 3.6)


async def _spawn_server(
    serve_command: str,
    config_path: Path | str,
    *,
    process_registry: ProcessRegistry,
    host: str = "127.0.0.1",
    is_stdio: bool = False,
    health_timeout: float = 10.0,
    health_path: str = "/",
    extra_args: list[str] | None = None,
) -> AsyncIterator[SubprocessServer]:
    """Spawn an wolfharness server subprocess (internal helper for custom fixtures).

    Args:
        serve_command: The CLI subcommand (e.g. serve-api).
        config_path: Path to the YAML config file.
        process_registry: Session-scoped process registry.
        host: Bind host.
        is_stdio: Whether this is a stdio server.
        health_timeout: Health check timeout.
        health_path: Health check endpoint path (default ``/``).
        extra_args: Additional CLI arguments.

    Yields:
        SubprocessServer handle.
    """
    if is_stdio:
        port = 0
        cmd = _build_command(
            serve_command, str(config_path), host=host, port=port, extra_args=extra_args
        )
    else:
        port = allocate_ephemeral_port()
        cmd = _build_command(
            serve_command, str(config_path), host=host, port=port, extra_args=extra_args
        )

    env = os.environ.copy()
    env["OBSERVABILITY_ENABLED"] = "false"
    env["LOGFIRE_DISABLE"] = "true"

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    process_registry.register(process)

    server = SubprocessServer(process=process, host=host, port=port, is_stdio=is_stdio)

    if is_stdio:
        healthy = await _health_check_stdio(process, timeout=health_timeout)
    else:
        healthy = await _health_check_http(
            host, port, path=health_path, timeout=health_timeout, interval=0.5
        )

    if not healthy:
        stderr_text = await _terminate_process(process)
        server.stderr_text = stderr_text
        msg = (
            f"Server {serve_command} failed to become healthy within "
            f"{health_timeout}s.\nCommand: {' '.join(cmd)}\n"
            f"STDERR:\n{stderr_text}"
        )
        raise RuntimeError(msg)

    yield server

    stderr_text = await _terminate_process(process)
    server.stderr_text = stderr_text


@pytest.fixture
async def subprocess_server_with_tool(
    request: pytest.FixtureRequest,
    process_registry: ProcessRegistry,
    session_e2e_config_with_tool: Path,
    allow_model_requests: Any,
) -> AsyncIterator[SubprocessServer]:
    """Spawn a server with the tool-enabled config (model: test with call_tools).

    Uses the same cache logic as ``subprocess_server`` but with the
    ``session_e2e_config_with_tool`` fixture providing the YAML config.
    The cache key uses ``"with_tool"`` as the config type to avoid collisions
    with the default config cache entries.
    """
    params = getattr(request, "param", {"serve_command": "serve-opencode"})
    serve_command: str = params.get("serve_command", "serve-opencode")
    host: str = params.get("host", "127.0.0.1")
    is_stdio: bool = params.get("is_stdio", False)
    health_timeout: float = params.get("health_timeout", 10.0)
    health_path: str = params.get("health_path", "/")
    extra_args: list[str] | None = params.get("extra_args")

    # Determine if cache should be bypassed
    no_cache = (
        bool(request.config.getoption("--no-server-cache", default=False))
        or is_stdio
        or _xdist_cache_disabled["disabled"]
        or request.node.get_closest_marker("isolated") is not None
    )

    if no_cache:
        async for server in _spawn_server(
            serve_command,
            session_e2e_config_with_tool,
            process_registry=process_registry,
            host=host,
            is_stdio=is_stdio,
            health_timeout=health_timeout,
            health_path=health_path,
            extra_args=extra_args,
        ):
            yield server
        return

    # --- Cached path ---
    cache_key = (serve_command, is_stdio, health_path, str(extra_args), "with_tool")

    # Check cache for existing server
    cached_popen = _server_cache.get(cache_key)

    if cached_popen is not None:
        # Cache hit: check if process is still alive
        if cached_popen.poll() is not None:
            # Crashed — remove from cache and fall through to spawn
            _server_cache.pop(cache_key, None)
            cached_popen = None
        else:
            # Process alive — quick socket health check
            port = cached_popen._e2e_port  # type: ignore[attr-defined]
            healthy = await _health_check_socket(host, port, timeout=2.0)
            if not healthy:
                # Stale — terminate and re-spawn
                _terminate_popen(cached_popen)
                _server_cache.pop(cache_key, None)
                cached_popen = None

    if cached_popen is not None:
        # Cache hit: clear sessions for OpenCode servers
        cached_port = cached_popen._e2e_port  # type: ignore[attr-defined]
        base_url = f"http://{host}:{cached_port}"
        if serve_command == "serve-opencode":
            await _clear_sessions(base_url)

        server = SubprocessServer(
            process=cached_popen,
            host=host,
            port=cached_port,
            is_stdio=False,
        )
        yield server
        # Do NOT terminate cached servers
        return

    # Cache miss: spawn new server via Popen
    port = allocate_ephemeral_port()
    cmd = _build_command(
        serve_command,
        str(session_e2e_config_with_tool),
        host=host,
        port=port,
        extra_args=extra_args,
    )

    env = os.environ.copy()
    env["OBSERVABILITY_ENABLED"] = "false"
    env["LOGFIRE_DISABLE"] = "true"

    popen = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    # Store port on the Popen object for later retrieval
    popen._e2e_port = port  # type: ignore[attr-defined]
    popen._e2e_host = host  # type: ignore[attr-defined]

    process_registry.register(popen)

    # Health check via raw socket
    healthy = await _health_check_socket(host, port, timeout=health_timeout)
    if not healthy:
        _terminate_popen(popen)
        msg = (
            f"Server {serve_command} failed to become healthy within "
            f"{health_timeout}s.\nCommand: {' '.join(cmd)}"
        )
        raise RuntimeError(msg)

    # Store in cache
    _server_cache[cache_key] = popen

    # Clear sessions for OpenCode (first use of cached server)
    if serve_command == "serve-opencode":
        await _clear_sessions(f"http://{host}:{port}")

    server = SubprocessServer(
        process=popen,
        host=host,
        port=port,
        is_stdio=False,
    )
    yield server
    # Do NOT terminate cached servers


@pytest.fixture
async def subprocess_server_with_mcp(
    request: pytest.FixtureRequest,
    process_registry: ProcessRegistry,
    session_e2e_config_with_mcp: Path,
    allow_model_requests: Any,
) -> AsyncIterator[SubprocessServer]:
    """Spawn an ``wolfharness serve-*`` subprocess with the fake MCP server config.

    Uses ``_spawn_server`` (non-cached) because the MCP-config variant is
    unique to MCP status tests and not worth caching. The fake MCP server
    adds startup time (subprocess spawn + MCP handshake), so the default
    health timeout is 20s.

    Parametrize via ``indirect=True`` with the same dict shape as
    ``subprocess_server`` (minus ``config_path``, which is fixed to
    ``session_e2e_config_with_mcp``).
    """
    params = getattr(request, "param", {"serve_command": "serve-opencode"})
    serve_command: str = params.get("serve_command", "serve-opencode")
    host: str = params.get("host", "127.0.0.1")
    is_stdio: bool = params.get("is_stdio", False)
    health_timeout: float = params.get("health_timeout", 20.0)
    health_path: str = params.get("health_path", "/session")
    extra_args: list[str] | None = params.get("extra_args")

    async for server in _spawn_server(
        serve_command,
        session_e2e_config_with_mcp,
        process_registry=process_registry,
        host=host,
        is_stdio=is_stdio,
        health_timeout=health_timeout,
        health_path=health_path,
        extra_args=extra_args,
    ):
        yield server


@pytest.fixture(scope="session")
def session_e2e_config_with_mcp_resources(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Session-scoped e2e config with a fake MCP server that exposes resources.

    Defines a single native agent (``test_agent`` using TestModel) and one
    pool-level stdio MCP server (``fake_resources``) backed by
    ``tests/fixtures/fake_mcp_server_resources.py``. The fake server exposes:

    - Text resource: ``config://app/settings``
    - Blob resource: ``image://logo`` (1x1 PNG)
    - Resource template: ``file:///{path}``
    - Tool: ``ping``

    Includes ``storage: {providers: [{type: memory}]}`` to eliminate
    cross-run SQLite leakage.
    """
    config_dir = tmp_path_factory.mktemp("e2e_mcp_res")
    config_path = config_dir / "e2e_config_mcp_resources.yml"
    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures"
    fake_server_path = fixtures_dir / "fake_mcp_server_resources.py"
    config = {
        "default_agent": "test_agent",
        "agents": {
            "test_agent": {
                "type": "native",
                "model": "test",
                "system_prompt": "You are a test assistant.",
            },
            "resource_tools_disabled_agent": {
                "type": "native",
                "model": "test",
                "system_prompt": "You are a test assistant.",
                "resources": {"enabled": False},
            },
        },
        "mcp_servers": [
            {
                "name": "fake_resources",
                "type": "stdio",
                "command": sys.executable,
                "args": [str(fake_server_path)],
                "enabled": True,
            },
            {
                "name": "fake_resources_secondary",
                "type": "stdio",
                "command": sys.executable,
                "args": [str(fake_server_path), "--variant", "secondary"],
                "enabled": True,
            },
        ],
        "storage": {"providers": [{"type": "memory"}]},
    }
    config_path.write_text(yaml.dump(config, default_flow_style=False))
    return config_path


@pytest.fixture
async def subprocess_server_with_mcp_resources(
    request: pytest.FixtureRequest,
    process_registry: ProcessRegistry,
    session_e2e_config_with_mcp_resources: Path,
    allow_model_requests: Any,
) -> AsyncIterator[SubprocessServer]:
    """Spawn an ``wolfharness serve-*`` subprocess with the resource-exposing MCP server.

    Uses ``_spawn_server`` (non-cached) because the MCP-resources-config variant is
    unique to resource integration tests. The fake MCP server adds startup time
    (subprocess spawn + MCP handshake), so the default health timeout is 20s.

    Parametrize via ``indirect=True`` with the same dict shape as
    ``subprocess_server`` (minus ``config_path``, which is fixed to
    ``session_e2e_config_with_mcp_resources``).
    """
    params = getattr(request, "param", {"serve_command": "serve-opencode"})
    serve_command: str = params.get("serve_command", "serve-opencode")
    host: str = params.get("host", "127.0.0.1")
    is_stdio: bool = params.get("is_stdio", False)
    health_timeout: float = params.get("health_timeout", 20.0)
    health_path: str = params.get("health_path", "/session")
    extra_args: list[str] | None = params.get("extra_args")

    async for server in _spawn_server(
        serve_command,
        session_e2e_config_with_mcp_resources,
        process_registry=process_registry,
        host=host,
        is_stdio=is_stdio,
        health_timeout=health_timeout,
        health_path=health_path,
        extra_args=extra_args,
    ):
        yield server


# ---------------------------------------------------------------------------
# ACP WebSocket (streamable-http) server fixture (B1.1)
# ---------------------------------------------------------------------------


@dataclass
class ACPWSServerHandle:
    """Handle to a spawned ``wolfharness serve-acp --transport streamable-http`` server.

    Attributes:
        process: The asyncio subprocess.
        host: Bind host.
        port: Bind port.
        stderr_text: Captured stderr output (populated on teardown).
    """

    process: asyncio.subprocess.Process
    host: str
    port: int
    stderr_text: str = ""

    @property
    def ws_url(self) -> str:
        """WebSocket URL for the ACP endpoint (``ws://host:port/acp``)."""
        return f"ws://{self.host}:{self.port}/acp"

    @property
    def returncode(self) -> int | None:
        """Process return code (None if still running)."""
        return self.process.returncode


@contextlib.asynccontextmanager
async def _spawn_acp_ws_server(
    config_path: Path | str,
    process_registry: ProcessRegistry,
    *,
    host: str = "127.0.0.1",
    agent: str = "test_agent",
    health_timeout: float = 15.0,
) -> AsyncIterator[ACPWSServerHandle]:
    """Spawn ``wolfharness serve-acp --transport streamable-http`` and wait for health.

    Args:
        config_path: Path to the YAML config file.
        process_registry: Session-scoped process registry for cleanup verification.
        host: Bind host.
        agent: Agent name to use (``--agent``).
        health_timeout: Health check timeout in seconds.

    Yields:
        ACPWSServerHandle with the running server.
    """
    port = allocate_ephemeral_port()
    cmd: list[str] = [
        "wolfharness",
        "serve-acp",
        str(config_path),
        "--agent",
        agent,
        "--transport",
        "streamable-http",
        "--host",
        host,
        "--port",
        str(port),
    ]

    env = os.environ.copy()
    env["OBSERVABILITY_ENABLED"] = "false"
    env["LOGFIRE_DISABLE"] = "true"

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    process_registry.register(process)

    handle = ACPWSServerHandle(process=process, host=host, port=port)

    # Health check: poll the HTTP server until it responds.
    # Use a raw socket connect instead of httpx to avoid the ALLOW_MODEL_REQUESTS
    # httpx MockTransport block (the gate intercepts all httpx clients).
    healthy = await _health_check_socket(host, port, timeout=health_timeout)
    if not healthy:
        stderr_text = await _terminate_process(process)
        handle.stderr_text = stderr_text
        msg = (
            f"ACP WS server failed to become healthy within {health_timeout}s.\n"
            f"Command: {' '.join(cmd)}\nSTDERR:\n{stderr_text}"
        )
        raise RuntimeError(msg)

    try:
        yield handle
    finally:
        stderr_text = await _terminate_process(process)
        handle.stderr_text = stderr_text


async def _health_check_socket(
    host: str,
    port: int,
    *,
    timeout: float = 15.0,
    interval: float = 0.5,
) -> bool:
    """Poll a TCP server until it accepts connections or timeout.

    Uses raw sockets (not httpx) to avoid the ALLOW_MODEL_REQUESTS MockTransport
    gate that intercepts all httpx clients.

    Args:
        host: Server host.
        port: Server port.
        timeout: Maximum seconds to wait.
        interval: Polling interval in seconds.
    """
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(interval)
                sock.connect((host, port))
                return True
        except (OSError, ConnectionRefusedError):
            await asyncio.sleep(interval)
    return False


# ---------------------------------------------------------------------------
# Process cleanup verification (10.6)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def process_registry() -> ProcessRegistry:
    """Session-scoped registry of all spawned subprocesses."""
    return ProcessRegistry()


@pytest.fixture(autouse=True)
def _register_process_cleanup(process_registry: ProcessRegistry) -> Any:
    """Autouse fixture that ensures process registry is available."""
    # The actual cleanup verification is in the session-scoped finalizer below.
    return


@pytest.fixture(scope="session", autouse=True)
def verify_no_orphaned_processes(
    process_registry: ProcessRegistry,
) -> Any:
    """Session-scoped finalizer that verifies all spawned processes are cleaned up.

    After all e2e tests complete, this checks that every process registered
    with the ``process_registry`` has terminated. If any are still running,
    it forcibly kills them and emits a warning.

    For ``subprocess.Popen`` handles (cached servers), uses graceful
    SIGTERM → wait(5s) → SIGKILL via ``_terminate_popen``.
    For ``asyncio.subprocess.Process`` handles, uses bare ``proc.kill()``.
    """
    yield

    orphans: list[asyncio.subprocess.Process | subprocess.Popen] = [
        p for p in process_registry.processes if p.returncode is None
    ]
    for proc in orphans:
        if isinstance(proc, subprocess.Popen):
            _terminate_popen(proc)
        else:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
    if orphans:
        import warnings

        warnings.warn(
            f"{len(orphans)} orphaned wolfharness subprocess(es) found and killed at session end.",
            stacklevel=2,
        )
