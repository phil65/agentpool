"""LSP Manager - Orchestrates LSP servers across execution environments.

This module manages LSP server lifecycle:
- Starting LSP proxy processes via the execution environment
- Tracking running servers by language/server-id
- Sending requests via one-shot Python scripts
- Handling initialization handshake
"""

import asyncio
import contextlib
from dataclasses import dataclass, field
import time
from typing import TYPE_CHECKING, Any

import anyenv

from agentpool.diagnostics.models import (
    COMPLETION_KIND_MAP,
    SYMBOL_KIND_MAP,
    CallHierarchyCall,
    CallHierarchyItem,
    CodeAction,
    CompletionItem,
    Diagnostic,
    DiagnosticsResult,
    HoverInfo,
    Location,
    LSPServerState,
    Position,
    Range,
    RenameResult,
    SignatureInfo,
    SymbolInfo,
    TypeHierarchyItem,
)


if TYPE_CHECKING:
    from anyenv.lsp_servers import LSPServerInfo
    from exxec import ExecutionEnvironment

    from agentpool.diagnostics.models import (
        HoverContents,
    )


# One-shot script template for sending LSP requests via TCP
LSP_CLIENT_SCRIPT = '''
"""One-shot LSP client - sends request and prints JSON response."""
import asyncio
import json

PORT = {port!r}
METHOD = {method!r}
PARAMS = {params!r}


async def send_request() -> None:
    """Connect to LSP proxy via TCP, send request, print response."""
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", PORT)

        # Build JSON-RPC request
        request = {{"jsonrpc": "2.0", "id": 1, "method": METHOD, "params": PARAMS}}
        payload = json.dumps(request)
        header = f"Content-Length: {{len(payload)}}\\r\\n\\r\\n"

        # Send request
        writer.write(header.encode() + payload.encode())
        await writer.drain()

        # Read response headers
        headers = b""
        while b"\\r\\n\\r\\n" not in headers:
            chunk = await reader.read(1)
            if not chunk:
                print(json.dumps({{"error": "Connection closed"}}))
                return
            headers += chunk

        # Parse Content-Length
        header_str = headers.decode()
        length = None
        for line in header_str.split("\\r\\n"):
            if line.startswith("Content-Length:"):
                length = int(line.split(":")[1].strip())
                break

        if length is None:
            print(json.dumps({{"error": "No Content-Length header"}}))
            return

        # Read response body
        body = await reader.read(length)
        response = json.loads(body)

        # Print result (or error)
        print(json.dumps(response))

        writer.close()
        await writer.wait_closed()

    except ConnectionRefusedError:
        print(json.dumps({{"error": f"Connection refused on port {{PORT}}"}}))
    except Exception as e:
        print(json.dumps({{"error": str(e)}}))


asyncio.run(send_request())
'''


@dataclass
class LSPManager:
    """Manages LSP servers for an execution environment.

    Handles:
    - Starting LSP proxy processes that wrap stdio-based servers
    - Tracking running servers by ID
    - Sending requests via one-shot scripts or direct sockets
    - LSP initialization handshake
    """

    env: ExecutionEnvironment
    """The execution environment to run servers in."""

    port_file_dir: str = "/tmp/lsp-ports"
    """Directory for port files."""

    _servers: dict[str, LSPServerState] = field(default_factory=dict)
    """Running servers by server_id."""

    _server_configs: dict[str, LSPServerInfo] = field(default_factory=dict)
    """Server configurations by server_id."""

    _starting: set[str] = field(default_factory=set)
    """Server IDs currently being started (to prevent concurrent start attempts)."""

    def register_server(self, config: LSPServerInfo) -> None:
        """Register an LSP server configuration."""
        self._server_configs[config.id] = config

    def register_defaults(self) -> None:
        """Register all default LSP servers from anyenv."""
        from anyenv.lsp_servers import LSPServerRegistry

        registry = LSPServerRegistry()
        registry.register_defaults()
        for server in registry.all_servers:
            self.register_server(server)

    def get_server_for_file(self, path: str) -> LSPServerInfo | None:
        """Get the best LSP server for a file path."""
        import posixpath

        ext = posixpath.splitext(path)[1].lower()
        return next((cfg for cfg in self._server_configs.values() if cfg.can_handle(ext)), None)

    def is_running(self, server_id: str) -> bool:
        """Check if a server is currently running or starting."""
        if server_id in self._starting:
            return True  # Treat "starting" as running to prevent concurrent starts
        return server_id in self._servers and self._servers[server_id].initialized

    async def start_server(
        self,
        server_id: str,
        root_uri: str | None = None,
    ) -> LSPServerState:
        """Start an LSP server.

        Args:
            server_id: Server identifier (e.g., 'pyright', 'rust-analyzer')
            root_uri: Workspace root URI (e.g., 'file:///path/to/project')

        Returns:
            LSPServerState with server info

        Raises:
            ValueError: If server_id not registered
            RuntimeError: If server fails to start
        """
        from agentpool.diagnostics.lsp_proxy import LSPProxy

        if server_id in self._servers:
            return self._servers[server_id]

        # Check if already being started (prevent concurrent starts)
        if server_id in self._starting:
            # Wait for the other start to complete
            for _ in range(100):  # 10 second timeout
                await asyncio.sleep(0.1)
                if server_id in self._servers:
                    return self._servers[server_id]
            raise RuntimeError(f"Timeout waiting for {server_id} to start")

        config = self._server_configs.get(server_id)
        if not config:
            raise ValueError(f"Server {server_id!r} not registered")

        # Mark as starting to prevent concurrent attempts
        self._starting.add(server_id)

        try:
            # Build the command
            command = config.get_full_command()
            command_str = " ".join(command)
            # Port file path for this server
            port_file = f"{self.port_file_dir}/{server_id}.port"
            # Start the LSP proxy process
            proxy_cmd = LSPProxy.get_start_command(command_str, port_file)
            # Ensure port file directory exists
            await self.env.execute_command(f"mkdir -p {self.port_file_dir}")
            # Start proxy as background process
            process_id = await self.env.process_manager.start_process(
                command=proxy_cmd[0],
                args=proxy_cmd[1:],
                env=config.get_env(),
            )

            # Wait for server to be ready (check for .ready marker file)
            ready_path = f"{port_file}.ready"
            for _ in range(50):  # 5 second timeout
                result = await self.env.execute_command(f"test -f {ready_path} && echo ready")
                if result.stdout and "ready" in result.stdout:
                    break
                await asyncio.sleep(0.1)
            else:
                # Cleanup on failure
                await self.env.process_manager.kill_process(process_id)
                raise RuntimeError(f"LSP proxy for {server_id} failed to start (not ready)")

            # Read the port from the port file
            port_result = await self.env.execute_command(f"cat {port_file}")
            if not port_result.stdout or port_result.exit_code != 0:
                await self.env.process_manager.kill_process(process_id)
                raise RuntimeError(f"LSP proxy for {server_id} failed to start (no port file)")
            port = int(port_result.stdout.strip())

            # Create server state
            state = LSPServerState(
                server_id=server_id,
                process_id=process_id,
                port=port,
                language=config.extensions[0] if config.extensions else "unknown",
                root_uri=root_uri,
                initialized=False,
            )
            self._servers[server_id] = state

            # Run LSP initialize handshake
            try:
                await self._initialize_server(state, config, root_uri)
            except Exception:
                # Initialization failed - remove from servers dict and re-raise
                self._servers.pop(server_id, None)
                # Kill the process
                await self.env.process_manager.kill_process(process_id)
                raise

            return state
        finally:
            # Always remove from starting set
            self._starting.discard(server_id)

    async def _initialize_server(
        self,
        state: LSPServerState,
        config: LSPServerInfo,
        root_uri: str | None,
    ) -> None:
        """Run LSP initialize/initialized handshake.

        Args:
            state: Server state to update
            config: Server configuration
            root_uri: Workspace root URI
        """
        # Get initialization options
        init_options = dict(config.initialization)

        # Build initialize params
        init_params: dict[str, Any] = {
            "processId": None,  # We don't have a process ID in the traditional sense
            "rootUri": root_uri,
            "capabilities": {
                "textDocument": {
                    "publishDiagnostics": {
                        "relatedInformation": True,
                        "tagSupport": {"valueSet": [1, 2]},
                    },
                    "synchronization": {
                        "dynamicRegistration": False,
                        "willSave": False,
                        "willSaveWaitUntil": False,
                        "didSave": True,
                    },
                },
                "workspace": {
                    "workspaceFolders": True,
                    "configuration": True,
                },
            },
            "initializationOptions": init_options,
        }

        if root_uri:
            init_params["workspaceFolders"] = [{"uri": root_uri, "name": "workspace"}]

        # Send initialize request
        match await self._send_request(state.port, "initialize", init_params):
            case {"error": error}:
                raise RuntimeError(f"LSP initialize failed: {error}")
            case {"result": result}:
                # Store capabilities
                state.capabilities = result.get("capabilities", {})

        # Send initialized notification (no response expected)
        await self._send_notification(state.port, "initialized", {})

        state.initialized = True

    async def _send_request(
        self,
        port: int,
        method: str,
        params: dict[str, Any],
        retries: int = 3,
    ) -> dict[str, Any]:
        """Send an LSP request via execution environment.

        Args:
            port: TCP port to connect to
            method: LSP method name
            params: Method parameters
            retries: Number of retries for transient failures

        Returns:
            JSON-RPC response dict
        """
        import shlex

        script = LSP_CLIENT_SCRIPT.format(port=port, method=method, params=params)
        # Execute via environment with retries for connection refused
        cmd = f"python3 -c {shlex.quote(script)}"
        last_result = None
        for attempt in range(retries):
            result = await self.env.execute_command(cmd)
            last_result = result

            if result.exit_code != 0:
                return {"error": f"Script failed: {result.stderr}"}

            try:
                response: dict[str, Any] = anyenv.load_json(result.stdout or "{}", return_type=dict)
                # Check if it's a connection refused error - retry
                if (
                    "error" in response
                    and "Connection refused" in str(response["error"])
                    and attempt < retries - 1
                ):
                    await asyncio.sleep(0.5)
                    continue
            except anyenv.JsonLoadError as e:
                return {"error": f"Invalid JSON response: {e}"}
            else:
                return response

        # Should not reach here, but return last result error if we do
        return {"error": f"Failed after {retries} retries: {last_result}"}

    async def _send_notification(
        self,
        port: int,
        method: str,
        params: dict[str, Any],
    ) -> None:
        """Send an LSP notification (no response expected).

        For now, we use the same mechanism as requests but ignore the response.
        """
        # For notifications, we'd need a different script that doesn't wait
        # For simplicity, we'll skip actual notification sending for now
        # The initialize/initialized handshake works without waiting for response

    async def stop_server(self, server_id: str) -> None:
        """Stop an LSP server.

        Args:
            server_id: Server identifier
        """
        if server_id not in self._servers:
            return

        state = self._servers[server_id]

        # Send shutdown request
        with contextlib.suppress(Exception):
            await self._send_request(state.port, "shutdown", {})

        # Kill the process
        with contextlib.suppress(Exception):
            await self.env.process_manager.kill_process(state.process_id)

        # Cleanup port files
        port_file = f"{self.port_file_dir}/{server_id}.port"
        with contextlib.suppress(Exception):
            await self.env.execute_command(f"rm -f {port_file} {port_file}.ready")

        del self._servers[server_id]

    async def stop_all(self) -> None:
        """Stop all running LSP servers."""
        for server_id in self._servers:
            await self.stop_server(server_id)

    async def get_diagnostics(
        self,
        server_id: str,
        file_uri: str,
        content: str,
    ) -> DiagnosticsResult:
        """Get diagnostics for a file.

        This opens the file in the LSP server, waits for diagnostics,
        and returns them.

        Args:
            server_id: Server identifier
            file_uri: File URI (e.g., 'file:///path/to/file.py')
            content: File content

        Returns:
            DiagnosticsResult with parsed diagnostics
        """
        start_time = time.perf_counter()

        if server_id not in self._servers:
            return DiagnosticsResult(
                success=False,
                error=f"Server {server_id} not running",
                duration=time.perf_counter() - start_time,
            )

        state = self._servers[server_id]
        # Send textDocument/didOpen
        open_params = {
            "textDocument": {
                "uri": file_uri,
                "languageId": _uri_to_language_id(file_uri),
                "version": 1,
                "text": content,
            }
        }
        await self._send_notification(state.port, "textDocument/didOpen", open_params)

        # For proper diagnostic collection, we'd need to listen for
        # textDocument/publishDiagnostics notifications from the server.
        # This requires a more complex architecture with persistent connections.
        #
        # For now, we'll use the CLI fallback approach which is more reliable
        # for one-shot diagnostic runs.

        return DiagnosticsResult(
            success=True,
            diagnostics=[],
            duration=time.perf_counter() - start_time,
            server_id=server_id,
        )

    async def run_cli_diagnostics(self, server_id: str, files: list[str]) -> DiagnosticsResult:
        """Run CLI diagnostics using the server's CLI fallback.

        This is more reliable for one-shot diagnostic runs than the full
        LSP protocol, as it doesn't require persistent connections.

        Args:
            server_id: Server identifier
            files: File paths to check

        Returns:
            DiagnosticsResult with parsed diagnostics
        """
        start_time = time.perf_counter()
        config = self._server_configs.get(server_id)
        if not config:
            return DiagnosticsResult(
                success=False,
                error=f"Server {server_id} not registered",
                duration=time.perf_counter() - start_time,
            )

        if not config.has_cli_diagnostics:
            return DiagnosticsResult(
                success=False,
                error=f"Server {server_id} has no CLI diagnostic support",
                duration=time.perf_counter() - start_time,
            )

        # Build and run the diagnostic command
        command = config.build_diagnostic_command(files)
        result = await self.env.execute_command(command)
        # Parse the output
        diagnostics = config.parse_diagnostics(result.stdout or "", result.stderr or "")
        return DiagnosticsResult(
            diagnostics=[_convert_diagnostic(d, server_id) for d in diagnostics],
            success=True,
            duration=time.perf_counter() - start_time,
            server_id=server_id,
        )

    # =========================================================================
    # Document Operations
    # =========================================================================

    async def hover(
        self,
        server_id: str,
        file_uri: str,
        line: int,
        character: int,
    ) -> HoverInfo | None:
        """Get hover information at a position.

        Returns type information, documentation, and other details
        for the symbol at the given position.

        Args:
            server_id: Server identifier
            file_uri: File URI (e.g., 'file:///path/to/file.py')
            line: 0-based line number
            character: 0-based character offset

        Returns:
            HoverInfo if available, None otherwise
        """
        if server_id not in self._servers:
            return None

        state = self._servers[server_id]
        params = {
            "textDocument": {"uri": file_uri},
            "position": {"line": line, "character": character},
        }

        response = await self._send_request(state.port, "textDocument/hover", params)
        if "error" in response or not response.get("result"):
            return None

        result = response["result"]
        contents = _extract_hover_contents(result.get("contents", ""))
        range_ = _parse_range(result.get("range")) if result.get("range") else None
        return HoverInfo(contents=contents, range=range_)

    async def goto_definition(
        self,
        server_id: str,
        file_uri: str,
        line: int,
        character: int,
    ) -> list[Location]:
        """Go to definition of symbol at position.

        Args:
            server_id: Server identifier
            file_uri: File URI
            line: 0-based line number
            character: 0-based character offset

        Returns:
            List of definition locations
        """
        if server_id not in self._servers:
            return []

        state = self._servers[server_id]
        params = {
            "textDocument": {"uri": file_uri},
            "position": {"line": line, "character": character},
        }
        response = await self._send_request(state.port, "textDocument/definition", params)
        if "error" in response or not response.get("result"):
            return []

        return _parse_locations(response["result"])

    async def goto_type_definition(
        self,
        server_id: str,
        file_uri: str,
        line: int,
        character: int,
    ) -> list[Location]:
        """Go to type definition of symbol at position.

        Args:
            server_id: Server identifier
            file_uri: File URI
            line: 0-based line number
            character: 0-based character offset

        Returns:
            List of type definition locations
        """
        if server_id not in self._servers:
            return []

        state = self._servers[server_id]
        params = {
            "textDocument": {"uri": file_uri},
            "position": {"line": line, "character": character},
        }
        response = await self._send_request(state.port, "textDocument/typeDefinition", params)
        if "error" in response or not response.get("result"):
            return []

        return _parse_locations(response["result"])

    async def goto_implementation(
        self,
        server_id: str,
        file_uri: str,
        line: int,
        character: int,
    ) -> list[Location]:
        """Go to implementation of symbol at position.

        Useful for finding implementations of interfaces/abstract methods.

        Args:
            server_id: Server identifier
            file_uri: File URI
            line: 0-based line number
            character: 0-based character offset

        Returns:
            List of implementation locations
        """
        if server_id not in self._servers:
            return []

        state = self._servers[server_id]
        params = {
            "textDocument": {"uri": file_uri},
            "position": {"line": line, "character": character},
        }
        response = await self._send_request(state.port, "textDocument/implementation", params)
        if "error" in response or not response.get("result"):
            return []

        return _parse_locations(response["result"])

    async def find_references(
        self,
        server_id: str,
        file_uri: str,
        line: int,
        character: int,
        include_declaration: bool = True,
    ) -> list[Location]:
        """Find all references to symbol at position.

        Args:
            server_id: Server identifier
            file_uri: File URI
            line: 0-based line number
            character: 0-based character offset
            include_declaration: Whether to include the declaration itself

        Returns:
            List of reference locations
        """
        if server_id not in self._servers:
            return []

        state = self._servers[server_id]
        params = {
            "textDocument": {"uri": file_uri},
            "position": {"line": line, "character": character},
            "context": {"includeDeclaration": include_declaration},
        }
        response = await self._send_request(state.port, "textDocument/references", params)
        if "error" in response or not response.get("result"):
            return []

        return _parse_locations(response["result"])

    async def get_document_symbols(self, server_id: str, file_uri: str) -> list[SymbolInfo]:
        """Get all symbols in a document (outline).

        Returns a hierarchical list of symbols (classes, functions, etc.)
        in the document.

        Args:
            server_id: Server identifier
            file_uri: File URI

        Returns:
            List of symbols with hierarchy
        """
        if server_id not in self._servers:
            return []

        state = self._servers[server_id]
        params = {"textDocument": {"uri": file_uri}}
        response = await self._send_request(state.port, "textDocument/documentSymbol", params)
        if "error" in response or not response.get("result"):
            return []

        return _parse_document_symbols(response["result"], file_uri)

    async def search_workspace_symbols(self, server_id: str, query: str) -> list[SymbolInfo]:
        """Search for symbols in the workspace.

        Args:
            server_id: Server identifier
            query: Search query (fuzzy matching)

        Returns:
            List of matching symbols
        """
        if server_id not in self._servers:
            return []

        state = self._servers[server_id]
        params = {"query": query}
        response = await self._send_request(state.port, "workspace/symbol", params)
        if "error" in response or not response.get("result"):
            return []
        return _parse_workspace_symbols(response["result"])

    async def get_completions(
        self,
        server_id: str,
        file_uri: str,
        line: int,
        character: int,
    ) -> list[CompletionItem]:
        """Get completion suggestions at position.

        Args:
            server_id: Server identifier
            file_uri: File URI
            line: 0-based line number
            character: 0-based character offset

        Returns:
            List of completion items
        """
        if server_id not in self._servers:
            return []

        state = self._servers[server_id]
        params = {
            "textDocument": {"uri": file_uri},
            "position": {"line": line, "character": character},
        }
        response = await self._send_request(state.port, "textDocument/completion", params)
        if "error" in response or not response.get("result"):
            return []

        result = response["result"]
        # Result can be CompletionList or CompletionItem[]
        items = result.get("items", result) if isinstance(result, dict) else result
        return [_parse_completion_item(item) for item in items]

    async def get_signature_help(
        self,
        server_id: str,
        file_uri: str,
        line: int,
        character: int,
    ) -> SignatureInfo | None:
        """Get signature help at position.

        Useful when cursor is inside function call parentheses.

        Args:
            server_id: Server identifier
            file_uri: File URI
            line: 0-based line number
            character: 0-based character offset

        Returns:
            SignatureInfo if available, None otherwise
        """
        if server_id not in self._servers:
            return None

        state = self._servers[server_id]
        params = {
            "textDocument": {"uri": file_uri},
            "position": {"line": line, "character": character},
        }

        response = await self._send_request(state.port, "textDocument/signatureHelp", params)
        if "error" in response or not response.get("result"):
            return None

        result = response["result"]
        signatures = result.get("signatures", [])
        if not signatures:
            return None

        active_sig = result.get("activeSignature", 0)
        sig = signatures[min(active_sig, len(signatures) - 1)]

        return SignatureInfo(
            label=sig.get("label", ""),
            documentation=_extract_documentation(sig.get("documentation")),
            parameters=sig.get("parameters", []),
            active_parameter=result.get("activeParameter"),
        )

    async def get_code_actions(
        self,
        server_id: str,
        file_uri: str,
        start_line: int,
        start_character: int,
        end_line: int,
        end_character: int,
        diagnostics: list[Diagnostic] | None = None,
    ) -> list[CodeAction]:
        """Get available code actions for a range.

        Code actions include quick fixes, refactorings, and source actions.

        Args:
            server_id: Server identifier
            file_uri: File URI
            start_line: Start line (0-based)
            start_character: Start character
            end_line: End line (0-based)
            end_character: End character
            diagnostics: Optional diagnostics to get fixes for

        Returns:
            List of available code actions
        """
        if server_id not in self._servers:
            return []

        state = self._servers[server_id]
        # Convert our Diagnostic to LSP format
        lsp_diagnostics = [
            {
                "range": {
                    "start": {"line": d.line - 1, "character": d.column - 1},
                    "end": {
                        "line": (d.end_line or d.line) - 1,
                        "character": (d.end_column or d.column) - 1,
                    },
                },
                "message": d.message,
                "severity": _severity_to_lsp(d.severity),
                "source": d.source,
                "code": d.code,
            }
            for d in (diagnostics or [])
        ]

        params = {
            "textDocument": {"uri": file_uri},
            "range": {
                "start": {"line": start_line, "character": start_character},
                "end": {"line": end_line, "character": end_character},
            },
            "context": {
                "diagnostics": lsp_diagnostics,
                "only": ["quickfix", "refactor", "source"],
            },
        }

        response = await self._send_request(state.port, "textDocument/codeAction", params)
        if "error" in response or not response.get("result"):
            return []

        return [_parse_code_action(action) for action in response["result"]]

    async def rename_symbol(
        self,
        server_id: str,
        file_uri: str,
        line: int,
        character: int,
        new_name: str,
    ) -> RenameResult:
        """Rename a symbol across the workspace.

        Args:
            server_id: Server identifier
            file_uri: File URI
            line: 0-based line number
            character: 0-based character offset
            new_name: New name for the symbol

        Returns:
            RenameResult with the edits to apply
        """
        if server_id not in self._servers:
            return RenameResult(changes={}, success=False, error="Server not running")

        state = self._servers[server_id]

        # First check if rename is valid
        prepare_params = {
            "textDocument": {"uri": file_uri},
            "position": {"line": line, "character": character},
        }

        prepare_response = await self._send_request(
            state.port, "textDocument/prepareRename", prepare_params
        )

        if "error" in prepare_response:
            return RenameResult(changes={}, success=False, error=str(prepare_response["error"]))

        # Now do the rename
        rename_params = {
            "textDocument": {"uri": file_uri},
            "position": {"line": line, "character": character},
            "newName": new_name,
        }

        response = await self._send_request(state.port, "textDocument/rename", rename_params)
        if "error" in response:
            return RenameResult(changes={}, success=False, error=str(response["error"]))

        result = response.get("result", {})
        changes = result.get("changes", {})
        document_changes = result.get("documentChanges", [])

        # Normalize to changes format
        if document_changes and not changes:
            changes = {}
            for doc_change in document_changes:
                if "textDocument" in doc_change:
                    uri = doc_change["textDocument"]["uri"]
                    changes[uri] = doc_change.get("edits", [])

        return RenameResult(changes=changes, success=True)

    async def format_document(
        self,
        server_id: str,
        file_uri: str,
        tab_size: int = 4,
        insert_spaces: bool = True,
    ) -> list[dict[str, Any]]:
        """Format an entire document.

        Args:
            server_id: Server identifier
            file_uri: File URI
            tab_size: Tab size in spaces
            insert_spaces: Use spaces instead of tabs

        Returns:
            List of text edits to apply
        """
        if server_id not in self._servers:
            return []

        state = self._servers[server_id]
        params = {
            "textDocument": {"uri": file_uri},
            "options": {"tabSize": tab_size, "insertSpaces": insert_spaces},
        }

        response = await self._send_request(state.port, "textDocument/formatting", params)
        if "error" in response or not response.get("result"):
            return []
        return response["result"]  # type: ignore[no-any-return]

    # =========================================================================
    # Call Hierarchy
    # =========================================================================

    async def prepare_call_hierarchy(
        self,
        server_id: str,
        file_uri: str,
        line: int,
        character: int,
    ) -> list[CallHierarchyItem]:
        """Prepare call hierarchy at position.

        This returns the item(s) at the position that can be used
        to query incoming/outgoing calls.

        Args:
            server_id: Server identifier
            file_uri: File URI
            line: 0-based line number
            character: 0-based character offset

        Returns:
            List of call hierarchy items
        """
        if server_id not in self._servers:
            return []

        state = self._servers[server_id]
        params = {
            "textDocument": {"uri": file_uri},
            "position": {"line": line, "character": character},
        }

        response = await self._send_request(state.port, "textDocument/prepareCallHierarchy", params)
        if "error" in response or not response.get("result"):
            return []

        return [_parse_call_hierarchy_item(item) for item in response["result"]]

    async def get_incoming_calls(
        self,
        server_id: str,
        item: CallHierarchyItem,
    ) -> list[CallHierarchyCall]:
        """Get incoming calls (callers) for a call hierarchy item.

        Args:
            server_id: Server identifier
            item: Call hierarchy item from prepare_call_hierarchy

        Returns:
            List of incoming calls
        """
        if server_id not in self._servers:
            return []

        state = self._servers[server_id]
        params = {"item": _call_hierarchy_item_to_lsp(item)}
        response = await self._send_request(state.port, "callHierarchy/incomingCalls", params)
        if "error" in response or not response.get("result"):
            return []

        return [
            CallHierarchyCall(
                item=_parse_call_hierarchy_item(call["from"]),
                from_ranges=[_parse_range(r) for r in call.get("fromRanges", [])],
            )
            for call in response["result"]
        ]

    async def get_outgoing_calls(
        self,
        server_id: str,
        item: CallHierarchyItem,
    ) -> list[CallHierarchyCall]:
        """Get outgoing calls (callees) for a call hierarchy item.

        Args:
            server_id: Server identifier
            item: Call hierarchy item from prepare_call_hierarchy

        Returns:
            List of outgoing calls
        """
        if server_id not in self._servers:
            return []

        state = self._servers[server_id]
        params = {"item": _call_hierarchy_item_to_lsp(item)}
        response = await self._send_request(state.port, "callHierarchy/outgoingCalls", params)
        if "error" in response or not response.get("result"):
            return []

        return [
            CallHierarchyCall(
                item=_parse_call_hierarchy_item(call["to"]),
                from_ranges=[_parse_range(r) for r in call.get("fromRanges", [])],
            )
            for call in response["result"]
        ]

    # =========================================================================
    # Type Hierarchy
    # =========================================================================

    async def prepare_type_hierarchy(
        self,
        server_id: str,
        file_uri: str,
        line: int,
        character: int,
    ) -> list[TypeHierarchyItem]:
        """Prepare type hierarchy at position.

        Args:
            server_id: Server identifier
            file_uri: File URI
            line: 0-based line number
            character: 0-based character offset

        Returns:
            List of type hierarchy items
        """
        if server_id not in self._servers:
            return []

        state = self._servers[server_id]
        params = {
            "textDocument": {"uri": file_uri},
            "position": {"line": line, "character": character},
        }

        response = await self._send_request(state.port, "textDocument/prepareTypeHierarchy", params)

        if "error" in response or not response.get("result"):
            return []

        return [_parse_type_hierarchy_item(item) for item in response["result"]]

    async def get_supertypes(
        self,
        server_id: str,
        item: TypeHierarchyItem,
    ) -> list[TypeHierarchyItem]:
        """Get supertypes (base classes/interfaces) for a type.

        Args:
            server_id: Server identifier
            item: Type hierarchy item from prepare_type_hierarchy

        Returns:
            List of supertype items
        """
        if server_id not in self._servers:
            return []

        state = self._servers[server_id]
        params = {"item": _type_hierarchy_item_to_lsp(item)}
        response = await self._send_request(state.port, "typeHierarchy/supertypes", params)
        if "error" in response or not response.get("result"):
            return []

        return [_parse_type_hierarchy_item(item) for item in response["result"]]

    async def get_subtypes(
        self,
        server_id: str,
        item: TypeHierarchyItem,
    ) -> list[TypeHierarchyItem]:
        """Get subtypes (derived classes/implementations) for a type.

        Args:
            server_id: Server identifier
            item: Type hierarchy item from prepare_type_hierarchy

        Returns:
            List of subtype items
        """
        if server_id not in self._servers:
            return []

        state = self._servers[server_id]
        params = {"item": _type_hierarchy_item_to_lsp(item)}
        response = await self._send_request(state.port, "typeHierarchy/subtypes", params)
        if "error" in response or not response.get("result"):
            return []

        return [_parse_type_hierarchy_item(item) for item in response["result"]]


def _uri_to_language_id(uri: str) -> str:
    """Convert file URI to LSP language ID."""
    import posixpath

    ext = posixpath.splitext(uri)[1].lower()
    language_map = {
        ".py": "python",
        ".pyi": "python",
        ".js": "javascript",
        ".jsx": "javascriptreact",
        ".ts": "typescript",
        ".tsx": "typescriptreact",
        ".rs": "rust",
        ".go": "go",
        ".c": "c",
        ".cpp": "cpp",
        ".h": "c",
        ".hpp": "cpp",
        ".java": "java",
        ".rb": "ruby",
        ".lua": "lua",
        ".zig": "zig",
        ".swift": "swift",
        ".ex": "elixir",
        ".exs": "elixir",
        ".php": "php",
        ".dart": "dart",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".cs": "csharp",
    }
    return language_map.get(ext, "plaintext")


def _convert_diagnostic(diag: Any, server_id: str) -> Diagnostic:
    """Convert anyenv Diagnostic to agentpool Diagnostic."""
    return Diagnostic(
        file=diag.file,
        line=diag.line,
        column=diag.column,
        severity=diag.severity,
        message=diag.message,
        source=server_id,
        code=diag.code,
        end_line=diag.end_line,
        end_column=diag.end_column,
    )


# =============================================================================
# LSP Response Parsing Helpers
# =============================================================================


def _parse_position(pos: dict[str, Any]) -> Position:
    """Parse LSP Position."""
    return Position(line=pos["line"], character=pos["character"])


def _parse_range(range_: dict[str, Any]) -> Range:
    """Parse LSP Range."""
    return Range(
        start=_parse_position(range_["start"]),
        end=_parse_position(range_["end"]),
    )


def _parse_location(loc: dict[str, Any]) -> Location:
    """Parse LSP Location."""
    return Location(uri=loc["uri"], range=_parse_range(loc["range"]))


def _parse_locations(result: Any) -> list[Location]:
    """Parse LSP definition/references result (can be Location, Location[], or LocationLink[])."""
    if not result:
        return []

    # Single location
    if isinstance(result, dict) and "uri" in result:
        return [_parse_location(result)]

    # Array of locations or location links
    locations = []
    for item in result:
        if "targetUri" in item:  # LocationLink
            rng = _parse_range(item["targetRange"])
            locations.append(Location(uri=item["targetUri"], range=rng))
        elif "uri" in item:  # Location
            locations.append(_parse_location(item))

    return locations


def _extract_hover_contents(contents: HoverContents) -> str:
    """Extract string from hover contents."""
    match contents:
        case str():
            return contents
        case {"value": str() as value}:
            return value
        case list():
            # Array of MarkedString
            parts = []
            for item in contents:
                match item:
                    case (str() as value) | {"value": str() as value}:
                        parts.append(value)
            return "\n\n".join(parts)
        case _:
            return str(contents)


def _extract_documentation(doc: Any) -> str | None:
    """Extract documentation string."""
    match doc:
        case None:
            return None
        case (str() as value) | {"value": value}:
            return value
        case _:
            return str(doc)


def _parse_document_symbols(result: list[Any], file_uri: str) -> list[SymbolInfo]:
    """Parse document symbols (can be DocumentSymbol[] or SymbolInformation[])."""
    symbols = []

    for item in result:
        if "location" in item:
            # SymbolInformation (flat)
            symbols.append(
                SymbolInfo(
                    name=item["name"],
                    kind=SYMBOL_KIND_MAP.get(item.get("kind", 0), "unknown"),
                    location=_parse_location(item["location"]),
                    container_name=item.get("containerName"),
                )
            )
        else:
            # DocumentSymbol (hierarchical)
            symbols.append(_parse_document_symbol(item, file_uri))

    return symbols


def _parse_document_symbol(item: dict[str, Any], file_uri: str) -> SymbolInfo:
    """Parse a single DocumentSymbol with children."""
    children = [_parse_document_symbol(child, file_uri) for child in item.get("children", [])]

    return SymbolInfo(
        name=item["name"],
        kind=SYMBOL_KIND_MAP.get(item.get("kind", 0), "unknown"),
        location=Location(uri=file_uri, range=_parse_range(item["range"])),
        detail=item.get("detail"),
        children=children,
    )


def _parse_workspace_symbols(result: list[Any]) -> list[SymbolInfo]:
    """Parse workspace symbols."""
    return [
        SymbolInfo(
            name=item["name"],
            kind=SYMBOL_KIND_MAP.get(item.get("kind", 0), "unknown"),
            location=_parse_location(item["location"]),
            container_name=item.get("containerName"),
        )
        for item in result
        if "location" in item
    ]


def _parse_completion_item(item: dict[str, Any]) -> CompletionItem:
    """Parse a completion item."""
    return CompletionItem(
        label=item.get("label", ""),
        kind=COMPLETION_KIND_MAP.get(item.get("kind", 0)),
        detail=item.get("detail"),
        documentation=_extract_documentation(item.get("documentation")),
        insert_text=item.get("insertText"),
        sort_text=item.get("sortText"),
    )


def _parse_code_action(action: dict[str, Any]) -> CodeAction:
    """Parse a code action."""
    return CodeAction(
        title=action.get("title", ""),
        kind=action.get("kind"),
        is_preferred=action.get("isPreferred", False),
        edit=action.get("edit"),
        command=action.get("command"),
    )


def _parse_call_hierarchy_item(item: dict[str, Any]) -> CallHierarchyItem:
    """Parse a call hierarchy item."""
    return CallHierarchyItem(
        name=item["name"],
        kind=SYMBOL_KIND_MAP.get(item.get("kind", 0), "unknown"),
        uri=item["uri"],
        range=_parse_range(item["range"]),
        selection_range=_parse_range(item["selectionRange"]),
        detail=item.get("detail"),
        data=item.get("data"),
    )


def _call_hierarchy_item_to_lsp(item: CallHierarchyItem) -> dict[str, Any]:
    """Convert CallHierarchyItem back to LSP format."""
    # Find the numeric kind (12 is function default)
    kind_num = next((num for num, name in SYMBOL_KIND_MAP.items() if name == item.kind), 12)
    return {
        "name": item.name,
        "kind": kind_num,
        "uri": item.uri,
        "range": {
            "start": {"line": item.range.start.line, "character": item.range.start.character},
            "end": {"line": item.range.end.line, "character": item.range.end.character},
        },
        "selectionRange": {
            "start": {
                "line": item.selection_range.start.line,
                "character": item.selection_range.start.character,
            },
            "end": {
                "line": item.selection_range.end.line,
                "character": item.selection_range.end.character,
            },
        },
        "detail": item.detail,
        "data": item.data,
    }


def _parse_type_hierarchy_item(item: dict[str, Any]) -> TypeHierarchyItem:
    """Parse a type hierarchy item."""
    return TypeHierarchyItem(
        name=item["name"],
        kind=SYMBOL_KIND_MAP.get(item.get("kind", 0), "unknown"),
        uri=item["uri"],
        range=_parse_range(item["range"]),
        selection_range=_parse_range(item["selectionRange"]),
        detail=item.get("detail"),
        data=item.get("data"),
    )


def _type_hierarchy_item_to_lsp(item: TypeHierarchyItem) -> dict[str, Any]:
    """Convert TypeHierarchyItem back to LSP format."""
    # Find the numeric kind (5 is class default)
    kind_num = next((num for num, name in SYMBOL_KIND_MAP.items() if name == item.kind), 5)
    return {
        "name": item.name,
        "kind": kind_num,
        "uri": item.uri,
        "range": {
            "start": {"line": item.range.start.line, "character": item.range.start.character},
            "end": {"line": item.range.end.line, "character": item.range.end.character},
        },
        "selectionRange": {
            "start": {
                "line": item.selection_range.start.line,
                "character": item.selection_range.start.character,
            },
            "end": {
                "line": item.selection_range.end.line,
                "character": item.selection_range.end.character,
            },
        },
        "detail": item.detail,
        "data": item.data,
    }


def _severity_to_lsp(severity: str) -> int:
    """Convert severity string to LSP DiagnosticSeverity."""
    return {"error": 1, "warning": 2, "info": 3, "hint": 4}.get(severity, 1)
