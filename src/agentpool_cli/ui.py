"""UI commands for launching interactive interfaces."""

from __future__ import annotations

import json
import os
from pathlib import Path
import platform
import signal
import socket
import subprocess
import time
from typing import Annotated

import typer as t

from agentpool_cli import log


logger = log.get_logger(__name__)

# Create UI subcommand group
ui_app = t.Typer(help="Launch interactive user interfaces")


@ui_app.command("opencode")
def opencode_ui_command(
    config: Annotated[
        str | None,
        t.Argument(help="Path to agent configuration (optional, not used with --attach)"),
    ] = None,
    host: Annotated[
        str,
        t.Option("--host", "-h", help="Host to bind/connect to"),
    ] = "127.0.0.1",
    port: Annotated[
        int,
        t.Option("--port", "-p", help="Port for server to listen on / connect to"),
    ] = 4096,
    agent: Annotated[
        str | None,
        t.Option(
            "--agent",
            help="Name of specific agent to use (not used with --attach)",
        ),
    ] = None,
    attach: Annotated[
        bool,
        t.Option("--attach", help="Only attach TUI to existing server (don't start server)"),
    ] = False,
) -> None:
    """Launch OpenCode TUI with integrated server or attach to existing one.

    By default, starts an OpenCode-compatible server in the background and
    automatically attaches the OpenCode TUI to it. When you exit the TUI,
    the server is automatically shut down.

    With --attach, only launches the TUI and connects to an existing server
    (useful when running the server separately or connecting from multiple clients).

    Examples:
        # Start server + TUI
        agentpool ui opencode

        # Use specific config and agent
        agentpool ui opencode agents.yml --agent myagent

        # Custom port
        agentpool ui opencode --port 8080

        # Attach to existing server (no server startup)
        agentpool ui opencode --attach
        agentpool ui opencode --attach --port 8080
    """
    url = f"http://{host}:{port}"
    # Attach-only mode: just launch TUI
    if attach:
        logger.info("Attaching to existing OpenCode server", url=url)
        os.system("clear" if os.name != "nt" else "cls")
        result = subprocess.run(["opencode", "--server", url], check=False)
        if result.returncode not in {0, 130}:  # 130 = Ctrl+C
            logger.warning("OpenCode TUI exited with non-zero status", code=result.returncode)
        return
    # Build server command
    server_cmd = ["agentpool", "serve-opencode", "--host", host, "--port", str(port)]
    if config:
        server_cmd.append(config)
    if agent:
        server_cmd.extend(["--agent", agent])
    logger.info("Starting OpenCode server", url=url)
    # Start server in background with suppressed output
    server = subprocess.Popen(server_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        # Wait for server to be ready with retry
        max_retries = 30
        for i in range(max_retries):
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(0.5)
                sock.connect((host, port))
                sock.close()
                logger.info("Server is ready", url=url)
                break
            except (TimeoutError, ConnectionRefusedError, OSError):
                if i == max_retries - 1:
                    msg = f"Server failed to start after {max_retries} attempts"
                    raise RuntimeError(msg)  # noqa: B904
                time.sleep(0.5)

        # Give HTTP layer a moment to be fully ready
        time.sleep(0.5)
        os.system("clear" if os.name != "nt" else "cls")
        # Attach TUI
        result = subprocess.run(["opencode", "--server", url], check=False)
        if result.returncode != 0:
            logger.warning("OpenCode TUI exited with non-zero status", code=result.returncode)

    except KeyboardInterrupt:
        logger.info("UI interrupted by user")
    except Exception as e:
        logger.exception("Error running OpenCode UI")
        raise t.Exit(1) from e
    finally:
        # Clean up server
        logger.info("Shutting down server")
        server.send_signal(signal.SIGTERM)
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            logger.warning("Server did not shut down gracefully, killing")
            server.kill()


@ui_app.command("toad")
def toad_ui_command(
    config: Annotated[
        str | None,
        t.Argument(help="Path to agent configuration (optional)"),
    ] = None,
    websocket: Annotated[
        bool,
        t.Option("--websocket", "-w", help="Use WebSocket transport (otherwise stdio)"),
    ] = False,
    port: Annotated[
        int,
        t.Option("--port", "-p", help="Port for WebSocket server (only with --websocket)"),
    ] = 8765,
) -> None:
    """Launch Toad TUI for ACP agents.

    By default uses stdio transport where Toad spawns the agentpool server.
    With --websocket, starts a WebSocket ACP server in the background first.

    Examples:
        # Direct stdio (Toad spawns server)
        agentpool ui toad

        # Use specific config
        agentpool ui toad agents.yml

        # WebSocket transport
        agentpool ui toad --websocket

        # WebSocket with custom port
        agentpool ui toad --websocket --port 9000
    """
    if websocket:
        _run_toad_websocket(config, port)
    else:
        _run_toad_stdio(config)


def _run_toad_stdio(config: str | None) -> None:
    """Run Toad with stdio transport (Toad spawns server)."""
    # Build agentpool command that Toad will spawn
    agentpool_cmd = "agentpool serve-acp"
    if config:
        agentpool_cmd += f" {config}"
    os.system("clear" if os.name != "nt" else "cls")
    # Run toad with agentpool as subprocess
    result = subprocess.run(
        [
            "uvx",
            "--python",
            "3.14",
            "--from",
            "batrachian-toad@latest",
            "toad",
            "acp",
            agentpool_cmd,
        ],
        check=False,
    )
    if result.returncode not in {0, 130}:  # 130 = Ctrl+C
        logger.warning("Toad TUI exited with non-zero status", code=result.returncode)


def _run_toad_websocket(config: str | None, port: int) -> None:
    """Run Toad with WebSocket transport."""
    url = f"ws://localhost:{port}"

    # Build server command
    server_cmd = [
        "agentpool",
        "serve-acp",
        "--transport",
        "websocket",
        "--ws-port",
        str(port),
    ]
    if config:
        server_cmd.append(config)

    logger.info("Starting ACP WebSocket server", url=url)
    # Start server in background
    server = subprocess.Popen(server_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        # Wait for server startup
        time.sleep(1.5)
        os.system("clear" if os.name != "nt" else "cls")
        # Run toad with mcp-ws client
        result = subprocess.run(
            ["uvx", "--from", "batrachian-toad@latest", "toad", "acp", f"uvx mcp-ws {url}"],
            check=False,
        )

        if result.returncode not in {0, 130}:  # 130 = Ctrl+C
            logger.warning("Toad TUI exited with non-zero status", code=result.returncode)

    except KeyboardInterrupt:
        logger.info("UI interrupted by user")
    except Exception as e:
        logger.exception("Error running Toad UI")
        raise t.Exit(1) from e
    finally:
        # Clean up server
        logger.info("Shutting down server")
        server.send_signal(signal.SIGTERM)
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            logger.warning("Server did not shut down gracefully, killing")
            server.kill()


@ui_app.command("desktop")
def opencode_desktop_command(  # noqa: PLR0915
    config: Annotated[
        str | None,
        t.Argument(help="Path to agent configuration (optional, not used with --attach)"),
    ] = None,
    host: Annotated[
        str,
        t.Option("--host", "-h", help="Host to bind/connect to"),
    ] = "127.0.0.1",
    port: Annotated[
        int,
        t.Option("--port", "-p", help="Port for server to listen on / connect to"),
    ] = 4096,
    agent: Annotated[
        str | None,
        t.Option(
            "--agent",
            help="Name of specific agent to use (not used with --attach)",
        ),
    ] = None,
    attach: Annotated[
        bool,
        t.Option("--attach", help="Connect desktop app to existing server (don't start server)"),
    ] = False,
) -> None:
    """Launch OpenCode desktop app with integrated server or attach to existing one.

    By default, starts an OpenCode-compatible server in the background and
    configures the desktop app to connect to it. The desktop app will run
    independently and you can close the terminal.

    With --attach, configures the desktop app to connect to an existing server
    without starting a new one.

    Note: This command requires the OpenCode desktop app to be installed.
    The app will be configured to use the specified server URL via its config.

    Examples:
        # Start server, configure desktop app, and launch it
        agentpool ui desktop

        # Use specific config and agent
        agentpool ui desktop agents.yml --agent myagent

        # Custom port
        agentpool ui desktop --port 8080

        # Configure desktop to attach to existing server
        agentpool ui desktop --attach
        agentpool ui desktop --attach --port 8080

        # After using --attach, reset to default (spawn local server)
        agentpool ui desktop --attach --port 0  # port 0 clears the setting
    """
    url = f"http://{host}:{port}"
    # Determine config path based on platform
    config_dir = Path.home() / ".config" / "opencode"
    config_file = config_dir / "config.json"
    # Handle attach mode - configure desktop app to use external server
    if attach:
        if port == 0:
            # Special case: port 0 means clear the setting
            logger.info("Clearing desktop app server configuration")
            if config_file.exists():
                try:
                    with config_file.open() as f:
                        existing_config = json.load(f)
                    # Remove server config
                    if "server" in existing_config:
                        del existing_config["server"]
                    with config_file.open("w") as f:
                        json.dump(existing_config, f, indent=2)
                    logger.info("Cleared server configuration from config file")
                except Exception as e:  # noqa: BLE001
                    logger.warning("Failed to clear config", error=str(e))
        else:
            # Configure desktop app to use specified server
            logger.info("Configuring desktop app to attach to server", url=url)
            config_dir.mkdir(parents=True, exist_ok=True)
            # Read existing config or create new
            existing_config = {}
            if config_file.exists():
                try:
                    with config_file.open() as f:
                        existing_config = json.load(f)
                except Exception as e:  # noqa: BLE001
                    logger.warning("Failed to read existing config", error=str(e))

            # Update server configuration
            existing_config["server"] = {
                "hostname": host,
                "port": port,
            }

            try:
                with config_file.open("w") as f:
                    json.dump(existing_config, f, indent=2)
                logger.info("Updated desktop app configuration", config=str(config_file))
            except Exception as e:
                logger.exception("Failed to write config", error=str(e))
                raise t.Exit(1) from e

        # Launch desktop app
        logger.info("Launching OpenCode desktop app")
        try:
            # Try common desktop app launch commands
            # On macOS: open -a OpenCode
            # On Linux: OpenCode (capital O) is the desktop app
            # On Windows: start opencode

            system = platform.system()
            if system == "Darwin":
                subprocess.Popen(["open", "-a", "OpenCode"])
            elif system == "Windows":
                subprocess.Popen(["start", "opencode"], shell=True)
            else:  # Linux and others
                # Try different possible command names - OpenCode (capital O) is the desktop app
                for cmd in ["OpenCode", "opencode-desktop"]:
                    try:
                        subprocess.Popen([cmd])
                        break
                    except FileNotFoundError:
                        continue
                else:
                    msg = (
                        "Could not find OpenCode desktop app. Please install it or launch manually."
                    )
                    raise FileNotFoundError(msg)  # noqa: TRY301

            if port != 0:
                logger.info(
                    "Desktop app launched and configured to use server",
                    url=url,
                    note="The app will connect to the server. You can close this terminal.",
                )
            else:
                logger.info(
                    "Desktop app launched with default configuration",
                    note="The app will spawn its own local server. You can close this terminal.",
                )

        except Exception as e:
            logger.exception("Failed to launch desktop app", error=str(e))
            logger.info(
                "Configuration has been updated. Please launch the OpenCode desktop app manually.",
                config=str(config_file),
            )
            raise t.Exit(1) from e

        return

    # Default mode: Start server + launch desktop app
    # Build server command
    server_cmd = ["agentpool", "serve-opencode", "--host", host, "--port", str(port)]
    if config:
        server_cmd.append(config)
    if agent:
        server_cmd.extend(["--agent", agent])

    logger.info("Starting OpenCode server for desktop app", url=url)
    # Start server in background
    server = subprocess.Popen(server_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        # Wait for server to be ready
        max_retries = 30
        for i in range(max_retries):
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(0.5)
                sock.connect((host, port))
                sock.close()
                logger.info("Server is ready", url=url)
                break
            except (TimeoutError, ConnectionRefusedError, OSError):
                if i == max_retries - 1:
                    raise RuntimeError(f"Server failed to start after {max_retries} attempts")  # noqa: B904
                time.sleep(0.5)

        # Give HTTP layer a moment to be fully ready
        time.sleep(0.5)
        # Configure desktop app to use this server
        config_dir.mkdir(parents=True, exist_ok=True)
        existing_config = {}
        if config_file.exists():
            try:
                with config_file.open() as f:
                    existing_config = json.load(f)
            except Exception as e:  # noqa: BLE001
                logger.warning("Failed to read existing config", error=str(e))

        existing_config["server"] = {"hostname": host, "port": port}

        try:
            with config_file.open("w") as f:
                json.dump(existing_config, f, indent=2)
            logger.info("Configured desktop app", config=str(config_file))
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to write config", error=str(e))

        # Launch desktop app
        logger.info("Launching OpenCode desktop app")
        system = platform.system()
        try:
            if system == "Darwin":
                subprocess.Popen(["open", "-a", "OpenCode"])
            elif system == "Windows":
                subprocess.Popen(["start", "opencode"], shell=True)
            else:  # Linux
                # OpenCode (capital O) is the desktop app
                for cmd in ["OpenCode", "opencode-desktop"]:
                    try:
                        subprocess.Popen([cmd])
                        break
                    except FileNotFoundError:
                        continue
                else:
                    raise FileNotFoundError("Could not find OpenCode desktop app")  # noqa: TRY301

            logger.info(
                "Desktop app launched",
                note="Server is running in background. Press Ctrl+C to stop the server.",
            )

            # Keep server running until interrupted
            logger.info("Server running. Press Ctrl+C to stop.")
            server.wait()

        except FileNotFoundError as e:
            logger.exception(
                "Desktop app not found. Please install OpenCode desktop app or launch it manually.",
                config=str(config_file),
            )
            raise t.Exit(1) from e

    except KeyboardInterrupt:
        logger.info("Shutting down server")
    except Exception as e:
        logger.exception("Error running desktop app")
        raise t.Exit(1) from e
    finally:
        # Clean up server
        server.send_signal(signal.SIGTERM)
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            logger.warning("Server did not shut down gracefully, killing")
            server.kill()


if __name__ == "__main__":
    t.run(ui_app)
