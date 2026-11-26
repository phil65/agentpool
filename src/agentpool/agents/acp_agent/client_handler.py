"""ACP Agent - MessageNode wrapping an external ACP subprocess."""

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aclientp.client.protocol import Client
from aclientp.schema import (
    CreateTerminalResponse,
    KillTerminalResponse,
    ReadTextFileResponse,
    ReleaseTerminalResponse,
    RequestPermissionResponse,
    TerminalOutputResponse,
    WaitForTerminalExitResponse,
    WriteTextFileResponse,
)
import anyio
from pydantic_ai import PartDeltaEvent, TextPartDelta

from agentpool.agents.exceptions import OperationNotAllowedError, ResourceNotFoundError
from agentpool.log import get_logger
from agentpool.mcp_server.tool_bridge import DEFAULT_ROUTE
from agentpool.sessions.environment import use_session_environment
from agentpool.tools.decisions import Approve, Deny


if TYPE_CHECKING:
    from collections.abc import Sequence

    from aclientp.schema import (
        AvailableCommand,
        CreateTerminalRequest,
        KillTerminalRequest,
        PermissionOption,
        ReadTextFileRequest,
        ReleaseTerminalRequest,
        RequestPermissionRequest,
        SessionNotification,
        TerminalOutputRequest,
        WaitForTerminalExitRequest,
        WriteTextFileRequest,
    )
    from aclientp.schema.elicitation import (
        ElicitationCompleteNotification,
        ElicitationRequest,
        ElicitationResponse,
    )
    from exxec import ExecutionEnvironment
    from slashed import Command

    from agentpool.agents.acp_agent import ACPAgent

logger = get_logger(__name__)


class ACPClientHandler(Client):
    """Client handler that collects session updates and handles agent requests.

    This implements the full ACP Client protocol including:
    - Session update collection (text chunks, thoughts, tool calls)
    - Filesystem operations (read/write files) via ExecutionEnvironment
    - Terminal operations (create, output, kill, release) via ProcessManager
    - Permission request handling via InputProvider

    The handler routes each ACP session's updates and requests to its state
    (``ACPAgent._states``): the agent runs a session on the ACP server per pool session,
    all over this one connection.

    Uses ExecutionEnvironment for all file and process operations, enabling
    swappable backends (local, Docker, E2B, SSH, etc.).

    The handler holds a reference to the parent ACPAgent, delegating env access
    to ensure the env stays in sync when reassigned externally.
    """

    def __init__(self, agent: ACPAgent[Any]) -> None:
        self._agent = agent
        self._terminals: dict[str, ExecutionEnvironment] = {}
        """The environment each terminal runs in, by terminal ID."""

    def _env(self, session_id: str) -> ExecutionEnvironment:
        """Where requests of ACP session ``session_id`` run: the agent's client env.

        By default that's the environment of the pool session the ACP session belongs to
        (captured with its turn): requests come in outside the turn's context.
        """
        state = self._agent._states.get(session_id)
        with use_session_environment(state.environment if state is not None else None):
            return self._agent.client_env

    def _terminal_env(self, terminal_id: str) -> ExecutionEnvironment:
        if (env := self._terminals.get(terminal_id)) is None:
            raise ResourceNotFoundError("Terminal", terminal_id)
        return env

    @property
    def allow_file_operations(self) -> bool:
        if (caps := self._agent._init_request.client_capabilities) and caps.fs:
            return bool(caps.fs.read_text_file or caps.fs.write_text_file)
        return False

    @property
    def allow_terminal(self) -> bool:
        caps = self._agent._init_request.client_capabilities
        return bool(caps and caps.terminal)

    async def session_update(self, params: SessionNotification) -> None:
        """Handle session update notifications from the agent.

        Some updates are state changes (mode, model, config) that should update
        session state. Others are stream data (text chunks, tool calls) that
        should be queued for consumption.

        Raw updates are stored as the single source of truth. Conversion to
        native events happens lazily during streaming consumption.
        """
        from aclientp.schema import (
            AvailableCommandsUpdate,
            ConfigOptionUpdate,
            CurrentModeUpdate,
        )

        if (state := self._agent._states.get(params.session_id)) is None:
            logger.debug("Update of an ACP session not ours", session_id=params.session_id)
            return
        # Handle state updates - these modify session state, not stream data
        match params.update:
            case CurrentModeUpdate(current_mode_id=mode_id):
                if state.modes:
                    state.modes.current_mode_id = mode_id
                    if any(m.id == mode_id for m in state.modes.available_modes):
                        await self._agent.update_state(
                            "mode", mode_id, session_id=state.pool_session_id
                        )
                state.current_mode_id = mode_id
                logger.debug("Mode updated", mode_id=mode_id)
                state.update_event.set()
            case ConfigOptionUpdate(config_options=new_options):
                # Detect changes by comparing with current state
                old_by_id = {o.id: o.current_value for o in state.config_options}
                # Replace full config options list
                state.config_options = list(new_options)
                # Emit change signals for each option whose value changed
                for opt in new_options:
                    old_val = old_by_id.get(opt.id)
                    if old_val != opt.current_value:
                        await self._agent.update_state(
                            config_id=str(opt.id),
                            value_id=str(opt.current_value),
                            session_id=state.pool_session_id,
                        )
                state.update_event.set()
            case AvailableCommandsUpdate() as update:
                state.available_commands = update
                # Populate command store with remote commands
                self._populate_command_store(update.available_commands)
                # Emit to parent session - remote commands will be merged with local ones.
                # The "way back" works because session.split_commands() only extracts
                # LOCAL commands; remote commands pass through to the agent prompt.
                await self._agent.state_updated.emit(update)
                logger.debug("Available commands updated", count=len(update.available_commands))
                state.update_event.set()
            case _:
                # TODO: AgentPlanUpdate handling is complex and needs design work.
                # Options:
                # 1. Update pool.todos - requires merging with existing todos
                # 2. Pass through to UI - but then todos aren't centrally managed
                # 3. Switch to agent-owned todos instead of pool-owned
                # For now, AgentPlanUpdate falls through to stream data.

                state.add_update(params.update)
                state.update_event.set()

    async def request_permission(
        self, params: RequestPermissionRequest
    ) -> RequestPermissionResponse:
        """Handle permission requests via InputProvider."""
        tc = params.tool_call
        name = tc.title or "operation"
        logger.info("Permission requested", tool_name=name)
        # Check auto_approve FIRST, before any forwarding
        # This ensures "bypass permissions" mode works even for nested ACP agents
        if self._agent.auto_approve and (
            option_id := _option_of(params.options, "allow_once", "allow_always")
        ):
            logger.debug("Auto-granting permission (auto_approve=True)", tool_name=name)
            return RequestPermissionResponse.allowed(option_id)
        state = self._agent._states.get(params.session_id)
        pool_session_id = state.pool_session_id if state is not None else None
        # Try callback second (forwards to the parent session for nested ACP agents)
        if callback := self._agent.permission_callback(pool_session_id):
            try:
                logger.debug("Forwarding permission via callback", tool_name=name)
                response = await callback(params)
                logger.debug(
                    "Permission response received", tool_name=name, outcome=response.outcome.outcome
                )
            except Exception:
                logger.exception("Failed to forward permission via callback")
            else:  # Fall through to old logic
                return response

        # The turn of this session: its context and input provider
        route = pool_session_id if pool_session_id is not None else DEFAULT_ROUTE
        base = self._agent._tool_bridge.context(route) or self._agent.get_context()
        input_provider = (state.input_provider if state else None) or base.input_provider
        if input_provider:
            args = tc.raw_input if isinstance(tc.raw_input, dict) else {}
            ctx = replace(
                base,
                input_provider=input_provider,
                tool_call_id=tc.tool_call_id,
                tool_name=name,
                tool_input=args,
            )
            try:
                decision = await ctx.decide_tool(name)
            except Exception:
                logger.exception("Failed to get permission via input provider")
                return RequestPermissionResponse.denied()
            # Answered with the agent's own options, by kind ("cancelled" means the
            # prompt turn was cancelled, so it only answers a denial that stops)
            match decision:
                case Approve(scope="session"):
                    option_id = _option_of(params.options, "allow_always", "allow_once")
                case Approve():
                    option_id = _option_of(params.options, "allow_once", "allow_always")
                case Deny(stop=False, scope="session"):
                    option_id = _option_of(params.options, "reject_always", "reject_once")
                case Deny(stop=False):
                    option_id = _option_of(params.options, "reject_once", "reject_always")
                case Deny(stop=True):
                    ctx.stop_turn()
                    option_id = None
            if option_id is None:
                return RequestPermissionResponse.denied()
            return RequestPermissionResponse.allowed(option_id)

        logger.debug("Denying permission (no input provider)", tool_name=name)
        return RequestPermissionResponse.denied()

    async def read_text_file(self, params: ReadTextFileRequest) -> ReadTextFileResponse:
        """Read text from file via ExecutionEnvironment filesystem."""
        if not self.allow_file_operations:
            raise OperationNotAllowedError("File operations")
        fs = self._env(params.session_id).get_fs()
        try:
            content_bytes = await fs._cat_file(params.path)
            content = content_bytes.decode("utf-8")
            # Apply line filtering if requested
            if params.line is not None or params.limit is not None:
                lines = content.splitlines(keepends=True)
                start_line = (params.line - 1) if params.line else 0
                end_line = start_line + params.limit if params.limit else len(lines)
                content = "".join(lines[start_line:end_line])

            logger.debug("Read file", path=params.path, num_chars=len(content))
            return ReadTextFileResponse(content=content)

        except FileNotFoundError, KeyError:
            # Match Zed behavior: return empty string for non-existent files
            logger.debug("File not found, returning empty string", path=params.path)
            return ReadTextFileResponse(content="")
        except Exception:
            logger.exception("Failed to read file", path=params.path)
            raise

    async def write_text_file(self, params: WriteTextFileRequest) -> WriteTextFileResponse:
        """Write text to file via ExecutionEnvironment filesystem."""
        if not self.allow_file_operations:
            raise OperationNotAllowedError("File operations")
        fs = self._env(params.session_id).get_fs()
        content_bytes = params.content.encode("utf-8")
        parent = str(Path(params.path).parent)
        try:
            if parent and parent != ".":  # Ensure parent directory exists
                await fs._makedirs(parent, exist_ok=True)
            await fs._pipe_file(params.path, content_bytes)
            logger.debug("Wrote file", path=params.path, num_chars=len(params.content))
            return WriteTextFileResponse()
        except Exception:
            logger.exception("Failed to write file", path=params.path)
            raise

    async def create_terminal(self, params: CreateTerminalRequest) -> CreateTerminalResponse:
        """Create a new terminal session via the configured ExecutionEnvironment.

        The ProcessManager implementation determines where the terminal runs
        (local, Docker, E2B, SSH, or forwarded to parent ACP client like Zed).

        The terminal_id returned by process_manager.start_process() is used directly.
        """
        if not self.allow_terminal:
            raise OperationNotAllowedError("Terminal operations")

        env = self._env(params.session_id)
        try:
            terminal_id = await env.process_manager.start_process(
                command=params.command,
                args=list(params.args) if params.args else None,
                cwd=params.cwd,
                env={var.name: var.value for var in params.env or []},
            )
        except Exception:
            logger.exception("Failed to create terminal", command=params.command)
            raise
        else:
            # Use the ID from process_manager directly - for ACPProcessManager this
            # is already the parent's terminal ID (e.g., Zed's)
            self._terminals[terminal_id] = env
            logger.info("Created terminal", terminal_id=terminal_id, command=params.command)
            return CreateTerminalResponse(terminal_id=terminal_id)

    async def terminal_output(self, params: TerminalOutputRequest) -> TerminalOutputResponse:
        """Get output from terminal via ProcessManager."""
        if not self.allow_terminal:
            raise OperationNotAllowedError("Terminal operations")

        terminal_id = params.terminal_id
        env = self._terminal_env(terminal_id)
        proc_output = await env.process_manager.get_output(terminal_id)
        output = proc_output.combined or proc_output.stdout or ""
        return TerminalOutputResponse(output=output, truncated=proc_output.truncated)

    async def wait_for_terminal_exit(
        self, params: WaitForTerminalExitRequest
    ) -> WaitForTerminalExitResponse:
        """Wait for terminal process to exit via ProcessManager."""
        if not self.allow_terminal:
            raise OperationNotAllowedError("Terminal operations")

        terminal_id = params.terminal_id
        env = self._terminal_env(terminal_id)
        exit_code = await env.process_manager.wait_for_exit(terminal_id)
        logger.debug("Terminal exited", terminal_id=terminal_id, exit_code=exit_code)
        return WaitForTerminalExitResponse(exit_code=exit_code)

    async def kill_terminal(self, params: KillTerminalRequest) -> KillTerminalResponse:
        """Kill terminal process via ProcessManager."""
        if not self.allow_terminal:
            raise OperationNotAllowedError("Terminal operations")

        terminal_id = params.terminal_id
        env = self._terminal_env(terminal_id)
        await env.process_manager.kill_process(terminal_id)
        logger.info("Killed terminal", terminal_id=terminal_id)
        return KillTerminalResponse()

    async def release_terminal(self, params: ReleaseTerminalRequest) -> ReleaseTerminalResponse:
        """Release terminal resources via ProcessManager."""
        if not self.allow_terminal:
            raise OperationNotAllowedError("Terminal operations")

        terminal_id = params.terminal_id
        env = self._terminal_env(terminal_id)
        await env.process_manager.release_process(terminal_id)
        del self._terminals[terminal_id]
        logger.info("Released terminal", terminal_id=terminal_id)
        return ReleaseTerminalResponse()

    async def cleanup(self) -> None:
        """Clean up all resources."""
        for terminal_id, env in list(self._terminals.items()):
            try:
                await env.process_manager.release_process(terminal_id)
            except Exception:
                logger.exception("Error cleaning up terminal", terminal_id=terminal_id)
        self._terminals.clear()

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Handle extension methods."""
        logger.debug("Extension method called", method=method)
        return {"ok": True, "method": method}

    async def elicitation(self, params: ElicitationRequest) -> ElicitationResponse:
        """Decline elicitation by default."""
        from aclientp.schema.elicitation import ElicitationResponse

        return ElicitationResponse.decline()

    async def elicitation_complete(self, params: ElicitationCompleteNotification) -> None:
        """Ignore elicitation complete notifications."""

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        """Handle extension notifications."""
        logger.debug("Extension notification", method=method)

    def _populate_command_store(self, commands: Sequence[AvailableCommand]) -> None:
        """Populate the agent's command store with remote ACP commands.

        Args:
            commands: List of AvailableCommand objects from the remote agent
        """
        store = self._agent.command_store

        for cmd in commands:
            command = self._create_acp_command(cmd)
            store.register_command(command, replace=True)

        logger.debug("Populated command store", command_count=len(store.list_commands()))

    def _create_acp_command(self, cmd: AvailableCommand) -> Command:
        """Create a slashed Command from an ACP AvailableCommand.

        The command, when executed, sends a prompt with the slash command
        to the remote ACP agent.

        Args:
            cmd: AvailableCommand from remote agent

        Returns:
            A slashed Command that sends the command to the remote agent
        """
        from slashed import Command

        async def run_cmd(ctx: Any, args: list[str], kwargs: dict[str, str]) -> None:
            """Execute the remote ACP slash command."""
            # Build command string
            args_str = " ".join(args) if args else ""
            if kwargs:
                kwargs_str = " ".join(f"{k}={v}" for k, v in kwargs.items())
                args_str = f"{args_str} {kwargs_str}".strip()
            # Execute via agent run_stream - the slash command goes as a prompt
            prompt = f"/{cmd.name} {args_str}".strip()
            # In the session the command was run for (see BaseAgent.use_session)
            async for event in self._agent.run_stream(
                prompt, session=self._agent._explicit_session
            ):
                match event:
                    case PartDeltaEvent(delta=TextPartDelta(content_delta=delta)):
                        await ctx.print(delta)

        return Command.from_raw(
            run_cmd,
            name=cmd.name,
            description=cmd.description,
            category="remote",
            usage=cmd.input.root.hint if cmd.input else None,
        )


if __name__ == "__main__":
    from agentpool.agents.acp_agent import ACPAgent

    async def main() -> None:
        """Demo: Basic call to an ACP agent."""
        args = ["run", "agentpool", "serve-acp"]
        cwd = str(Path.cwd())
        async with ACPAgent(command="uv", args=args, cwd=cwd, event_handlers=["detailed"]) as agent:
            print("Response (streaming): ", end="", flush=True)
            await agent.run("Say hello briefly.")

    anyio.run(main)


def _option_of(options: Sequence[PermissionOption], *kinds: str) -> str | None:
    """The ID of the first offered option of the first of ``kinds`` there is one of."""
    for kind in kinds:
        for option in options:
            if option.kind == kind:
                return option.option_id
    return None
