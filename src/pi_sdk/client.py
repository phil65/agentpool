"""Async Python RPC client for pi's coding agent.

Spawns the agent in RPC mode and provides a typed async API for all operations.
Communication uses JSONL over stdin/stdout.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import contextlib
from dataclasses import dataclass, field
import json
import logging
from typing import TYPE_CHECKING, Any, Self

import anyenv

from pi_sdk.models import (
    AgentMessageAdapter,
    AgentSessionEvent,
    AgentSessionEventAdapter,
    BashResult,
    CompactionResult,
    CycleModelData,
    CycleThinkingLevelData,
    ExportHtmlData,
    ForkData,
    ForkMessageEntry,
    LastAssistantTextData,
    Model,
    RpcSessionState,
    RpcSlashCommand,
    SessionStats,
    SwitchSessionData,
)


if TYPE_CHECKING:
    from pi_sdk.models import AgentMessage, ImageContent, SteeringMode, ThinkingLevel


logger = logging.getLogger(__name__)


class RpcError(Exception):
    """Raised when the RPC agent returns an error response."""


@dataclass
class RpcClientOptions:
    """Configuration for the RPC client."""

    cli_path: str = "pi"
    cwd: str | None = None
    env: dict[str, str] | None = None
    provider: str | None = None
    model: str | None = None
    args: list[str] = field(default_factory=list)


EventListener = Callable[[AgentSessionEvent], None]


class RpcClient:
    """Async RPC client for pi's coding agent.

    Usage::

        async with RpcClient(RpcClientOptions(cwd="/my/project")) as client:
            client.on_event(lambda e: print(e.type))
            events = await client.prompt_and_wait("Hello!")
            for event in events:
                match event:
                    case AgentEndEvent(messages=msgs):
                        print(f"Got {len(msgs)} messages")
    """

    def __init__(self, options: RpcClientOptions | None = None) -> None:
        self._options = options or RpcClientOptions()
        self._process: asyncio.subprocess.Process | None = None
        self._event_listeners: list[EventListener] = []
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._request_id = 0
        self._stderr = ""
        self._reader_task: asyncio.Task[None] | None = None

    # =========================================================================
    # Lifecycle
    # =========================================================================

    async def start(self) -> None:
        """Start the RPC agent process."""
        if self._process is not None:
            raise RuntimeError("Client already started")

        cmd_args = [self._options.cli_path, "--mode", "rpc"]
        if self._options.provider:
            cmd_args.extend(["--provider", self._options.provider])
        if self._options.model:
            cmd_args.extend(["--model", self._options.model])
        cmd_args.extend(self._options.args)

        import os

        env = {**os.environ, **(self._options.env or {})}

        self._process = await asyncio.create_subprocess_exec(
            *cmd_args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self._options.cwd,
            env=env,
        )
        # Start background tasks for reading stdout and stderr
        self._reader_task = asyncio.create_task(self._read_stdout())
        self._stderr_task = asyncio.create_task(self._read_stderr())
        # Brief wait to detect immediate exit
        await asyncio.sleep(0.1)
        if self._process.returncode is not None:
            msg = (
                f"Agent process exited immediately with code {self._process.returncode}. "
                f"Stderr: {self._stderr}"
            )
            raise RuntimeError(msg)

    async def stop(self) -> None:
        """Stop the RPC agent process."""
        if self._process is None:
            return

        self._process.terminate()
        try:
            await asyncio.wait_for(self._process.wait(), timeout=1.0)
        except TimeoutError:
            self._process.kill()
            await self._process.wait()

        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader_task

        self._process = None
        self._reader_task = None

        # Reject all pending requests
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(RpcError("Client stopped"))
        self._pending.clear()

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.stop()

    # =========================================================================
    # Event subscription
    # =========================================================================

    def on_event(self, listener: EventListener) -> Callable[[], None]:
        """Subscribe to typed agent events. Returns an unsubscribe callable."""
        self._event_listeners.append(listener)

        def unsubscribe() -> None:
            with contextlib.suppress(ValueError):
                self._event_listeners.remove(listener)

        return unsubscribe

    @property
    def stderr(self) -> str:
        """Collected stderr output (useful for debugging)."""
        return self._stderr

    # =========================================================================
    # Command methods
    # =========================================================================

    async def prompt(self, message: str, images: list[ImageContent] | None = None) -> None:
        """Send a prompt. Use on_event() or wait_for_idle() for results."""
        payload: dict[str, Any] = {"type": "prompt", "message": message}
        if images:
            payload["images"] = [img.model_dump(by_alias=True) for img in images]
        await self._send(payload)

    async def steer(self, message: str, images: list[ImageContent] | None = None) -> None:
        """Queue a steering message to interrupt the agent mid-run."""
        payload: dict[str, Any] = {"type": "steer", "message": message}
        if images:
            payload["images"] = [img.model_dump(by_alias=True) for img in images]
        await self._send(payload)

    async def follow_up(self, message: str, images: list[ImageContent] | None = None) -> None:
        """Queue a follow-up message processed after the agent finishes."""
        payload: dict[str, Any] = {"type": "follow_up", "message": message}
        if images:
            payload["images"] = [img.model_dump(by_alias=True) for img in images]
        await self._send(payload)

    async def abort(self) -> None:
        """Abort current operation."""
        await self._send({"type": "abort"})

    async def new_session(self, parent_session: str | None = None) -> bool:
        """Start a new session. Returns True if cancelled by extension."""
        payload: dict[str, Any] = {"type": "new_session"}
        if parent_session:
            payload["parentSession"] = parent_session
        resp = await self._send(payload)
        return bool(self._get_data(resp).get("cancelled", False))

    async def get_state(self) -> RpcSessionState:
        """Get current session state."""
        resp = await self._send({"type": "get_state"})
        return RpcSessionState.model_validate(self._get_data(resp))

    async def set_model(self, provider: str, model_id: str) -> Model:
        """Set model by provider and ID."""
        resp = await self._send({"type": "set_model", "provider": provider, "modelId": model_id})
        return Model.model_validate(self._get_data(resp))

    async def cycle_model(self) -> CycleModelData | None:
        """Cycle to next model. Returns None if only one model available."""
        resp = await self._send({"type": "cycle_model"})
        data = self._get_data(resp)
        return CycleModelData.model_validate(data) if data else None

    async def get_available_models(self) -> list[Model]:
        """Get list of available models."""
        resp = await self._send({"type": "get_available_models"})
        data = self._get_data(resp)
        return [Model.model_validate(m) for m in data.get("models", [])]

    async def set_thinking_level(self, level: ThinkingLevel) -> None:
        """Set thinking level."""
        await self._send({"type": "set_thinking_level", "level": level})

    async def cycle_thinking_level(self) -> CycleThinkingLevelData | None:
        """Cycle thinking level. Returns None if model doesn't support thinking."""
        resp = await self._send({"type": "cycle_thinking_level"})
        data = self._get_data(resp)
        return CycleThinkingLevelData.model_validate(data) if data else None

    async def set_steering_mode(self, mode: SteeringMode) -> None:
        """Set steering message mode."""
        await self._send({"type": "set_steering_mode", "mode": mode})

    async def set_follow_up_mode(self, mode: SteeringMode) -> None:
        """Set follow-up message mode."""
        await self._send({"type": "set_follow_up_mode", "mode": mode})

    async def compact(self, custom_instructions: str | None = None) -> CompactionResult:
        """Compact session context."""
        payload: dict[str, Any] = {"type": "compact"}
        if custom_instructions:
            payload["customInstructions"] = custom_instructions
        resp = await self._send(payload)
        return CompactionResult.model_validate(self._get_data(resp))

    async def set_auto_compaction(self, enabled: bool) -> None:
        """Set auto-compaction enabled/disabled."""
        await self._send({"type": "set_auto_compaction", "enabled": enabled})

    async def set_auto_retry(self, enabled: bool) -> None:
        """Set auto-retry enabled/disabled."""
        await self._send({"type": "set_auto_retry", "enabled": enabled})

    async def abort_retry(self) -> None:
        """Abort in-progress retry."""
        await self._send({"type": "abort_retry"})

    async def bash(self, command: str) -> BashResult:
        """Execute a bash command."""
        resp = await self._send({"type": "bash", "command": command})
        return BashResult.model_validate(self._get_data(resp))

    async def abort_bash(self) -> None:
        """Abort running bash command."""
        await self._send({"type": "abort_bash"})

    async def get_session_stats(self) -> SessionStats:
        """Get session statistics."""
        resp = await self._send({"type": "get_session_stats"})
        return SessionStats.model_validate(self._get_data(resp))

    async def export_html(self, output_path: str | None = None) -> str:
        """Export session to HTML. Returns the output file path."""
        payload: dict[str, Any] = {"type": "export_html"}
        if output_path:
            payload["outputPath"] = output_path
        resp = await self._send(payload)
        return ExportHtmlData.model_validate(self._get_data(resp)).path

    async def switch_session(self, session_path: str) -> bool:
        """Switch to a different session file. Returns True if cancelled."""
        resp = await self._send({"type": "switch_session", "sessionPath": session_path})
        return SwitchSessionData.model_validate(self._get_data(resp)).cancelled

    async def fork(self, entry_id: str) -> ForkData:
        """Fork from a specific message."""
        resp = await self._send({"type": "fork", "entryId": entry_id})
        return ForkData.model_validate(self._get_data(resp))

    async def get_fork_messages(self) -> list[ForkMessageEntry]:
        """Get messages available for forking."""
        resp = await self._send({"type": "get_fork_messages"})
        data = self._get_data(resp)
        return [ForkMessageEntry.model_validate(m) for m in data.get("messages", [])]

    async def get_last_assistant_text(self) -> str | None:
        """Get text of last assistant message."""
        resp = await self._send({"type": "get_last_assistant_text"})
        return LastAssistantTextData.model_validate(self._get_data(resp)).text

    async def set_session_name(self, name: str) -> None:
        """Set the session display name."""
        await self._send({"type": "set_session_name", "name": name})

    async def get_messages(self) -> list[AgentMessage]:
        """Get all messages in the session, validated into typed models."""
        resp = await self._send({"type": "get_messages"})
        data = self._get_data(resp)
        return [AgentMessageAdapter.validate_python(m) for m in data.get("messages", [])]

    async def get_commands(self) -> list[RpcSlashCommand]:
        """Get available commands."""
        resp = await self._send({"type": "get_commands"})
        data = self._get_data(resp)
        return [RpcSlashCommand.model_validate(c) for c in data.get("commands", [])]

    # =========================================================================
    # Helpers
    # =========================================================================

    async def wait_for_idle(self, timeout: float = 60.0) -> None:
        """Wait for the agent to become idle (agent_end event)."""
        fut: asyncio.Future[None] = asyncio.get_event_loop().create_future()

        def _listener(event: AgentSessionEvent) -> None:
            if event.type == "agent_end" and not fut.done():
                fut.set_result(None)

        unsub = self.on_event(_listener)
        try:
            await asyncio.wait_for(fut, timeout=timeout)
        except TimeoutError:
            raise TimeoutError(
                f"Timeout waiting for agent to become idle. Stderr: {self._stderr}"
            ) from None
        finally:
            unsub()

    async def collect_events(self, timeout: float = 60.0) -> list[AgentSessionEvent]:
        """Collect validated events until agent becomes idle."""
        events: list[AgentSessionEvent] = []
        fut: asyncio.Future[None] = asyncio.get_event_loop().create_future()

        def _listener(event: AgentSessionEvent) -> None:
            events.append(event)
            if event.type == "agent_end" and not fut.done():
                fut.set_result(None)

        unsub = self.on_event(_listener)
        try:
            await asyncio.wait_for(fut, timeout=timeout)
        except TimeoutError:
            raise TimeoutError(f"Timeout collecting events. Stderr: {self._stderr}") from None
        finally:
            unsub()
        return events

    async def prompt_and_wait(
        self,
        message: str,
        images: list[ImageContent] | None = None,
        timeout: float = 60.0,
    ) -> list[AgentSessionEvent]:
        """Send a prompt and wait for completion, returning all validated events."""
        collect_task = asyncio.create_task(self.collect_events(timeout))
        await self.prompt(message, images)
        return await collect_task

    # =========================================================================
    # Internal
    # =========================================================================

    async def _read_stdout(self) -> None:
        """Background task: read JSONL lines from stdout and dispatch."""
        assert self._process
        assert self._process.stdout
        reader = self._process.stdout
        while True:
            line = await reader.readline()
            if not line:
                break
            self._handle_line(line.decode(errors="replace").strip())

    async def _read_stderr(self) -> None:
        """Background task: accumulate stderr for debugging."""
        assert self._process
        assert self._process.stderr
        reader = self._process.stderr
        while True:
            chunk = await reader.read(4096)
            if not chunk:
                break
            self._stderr += chunk.decode(errors="replace")

    def _handle_line(self, line: str) -> None:
        if not line:
            return
        try:
            data = anyenv.load_json(line, return_type=dict)
        except anyenv.JsonLoadError:
            logger.debug("Ignoring non-JSON line: %s", line[:200])
            return

        # Check if it's a response to a pending request
        if data.get("type") == "response" and data.get("id") in self._pending:
            fut = self._pending.pop(data["id"])
            if not fut.done():
                fut.set_result(data)
            return

        # Otherwise it's an event — validate into typed model and notify listeners
        try:
            event = AgentSessionEventAdapter.validate_python(data)
        except Exception:  # noqa: BLE001
            logger.debug("Could not validate event: %s", data.get("type"))
            return

        for listener in self._event_listeners:
            try:
                listener(event)
            except Exception:
                logger.exception("Event listener error")

    async def _send(self, command: dict[str, Any]) -> dict[str, Any]:
        """Send a command and wait for its response."""
        if not self._process or not self._process.stdin:
            msg = "Client not started"
            raise RuntimeError(msg)

        self._request_id += 1
        req_id = f"req_{self._request_id}"
        command["id"] = req_id

        loop = asyncio.get_event_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[req_id] = fut

        payload = json.dumps(command, separators=(",", ":")) + "\n"
        self._process.stdin.write(payload.encode("utf-8"))
        await self._process.stdin.drain()

        try:
            return await asyncio.wait_for(fut, timeout=30.0)
        except TimeoutError:
            self._pending.pop(req_id, None)
            raise TimeoutError(
                f"Timeout waiting for response to {command.get('type')}. Stderr: {self._stderr}"
            ) from None

    @staticmethod
    def _get_data(response: dict[str, Any]) -> Any:
        """Extract data from a successful response, or raise on error."""
        if not response.get("success"):
            raise RpcError(response.get("error", "Unknown RPC error"))
        return response.get("data") or {}


if __name__ == "__main__":

    async def main() -> None:
        opts = RpcClientOptions(cwd="/tmp")
        async with RpcClient(opts) as client:
            print("Client started successfully")

            # Get session state
            state = await client.get_state()
            print(f"Session state: {state}")

            # Send a simple prompt and collect events
            print("Sending prompt...")
            events = await client.prompt_and_wait("Say hello in one sentence.", timeout=30.0)
            for ev in events:
                print(f"  Event: {ev.type}")

            # Get last assistant text
            text = await client.get_last_assistant_text()
            print(f"Assistant said: {text}")

    asyncio.run(main())
