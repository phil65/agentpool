"""LSP Proxy - Helper for deploying and managing LSP proxy instances.

The actual proxy script is in lsp_proxy_script.py and is executed as a subprocess.
"""

from pathlib import Path


# Path to the proxy script
_PROXY_SCRIPT_PATH = Path(__file__).parent / "lsp_proxy_script.py"


class LSPProxy:
    """Helper class for deploying and managing LSP proxy instances."""

    @staticmethod
    def get_script_path() -> Path:
        """Get the path to the proxy script."""
        return _PROXY_SCRIPT_PATH

    @staticmethod
    def get_start_command(lsp_command: str, port_file: str) -> list[str]:
        """Get command to start proxy as a background process.

        Args:
            lsp_command: The LSP server command (e.g., "pyright-langserver --stdio")
            port_file: Path for the port file (proxy writes its port here)

        Returns:
            Command and args for process_manager.start_process()
        """
        return [
            "python",
            str(_PROXY_SCRIPT_PATH),
            "--command",
            lsp_command,
            "--port-file",
            port_file,
        ]
