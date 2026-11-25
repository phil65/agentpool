"""Background shell commands (``/api/shell`` and ``session.shell``)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import os
import shutil
from typing import TYPE_CHECKING, Any, Literal

from agentpool.log import get_logger
from agentpool.utils import identifiers
from agentpool.utils.time_utils import now_ms
from agentpool_server.opencode_v2_server.events import build_event
from opencode_v2_sdk.models import (
    ExperimentalJobUpdated,
    ExperimentalJobUpdatedData,
    JobShell,
    JobTime,
    ShellCreated,
    ShellCreatedData,
    ShellDeleted,
    ShellDeletedData,
    ShellExited,
    ShellExitedData,
    ShellInfo,
    ShellInfoTime,
    ShellOutputResponseData,
)


if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic import BaseModel


logger = get_logger(__name__)

MAX_OUTPUT_BYTES = 1_000_000
"""Output kept per shell; older output is dropped (and reported as truncated)."""

type ShellStatus = Literal["running", "exited", "timeout", "killed"]


def default_shell() -> str:
    """The user's shell (falls back to /bin/sh)."""
    return os.environ.get("SHELL") or shutil.which("bash") or "/bin/sh"


@dataclass
class ShellProcess:
    """A running or finished shell command."""

    info: ShellInfo
    directory: str
    output: bytearray = field(default_factory=bytearray)
    dropped: int = 0
    """Bytes dropped from the front of ``output`` (keeps cursors absolute)."""
    process: asyncio.subprocess.Process | None = None
    done: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task[None] | None = None

    @property
    def size(self) -> int:
        """Total output size in bytes (including dropped output)."""
        return self.dropped + len(self.output)

    def append(self, chunk: bytes) -> None:
        """Append output, dropping old output beyond the size limit."""
        self.output.extend(chunk)
        overflow = len(self.output) - MAX_OUTPUT_BYTES
        if overflow > 0:
            del self.output[:overflow]
            self.dropped += overflow

    def read(self, cursor: int = 0, limit: int | None = None) -> ShellOutputResponseData:
        """Output from an absolute byte cursor."""
        start = max(cursor - self.dropped, 0)
        end = len(self.output) if limit is None else min(len(self.output), start + limit)
        chunk = bytes(self.output[start:end])
        return ShellOutputResponseData(
            output=chunk.decode(errors="replace"),
            cursor=self.dropped + end,
            size=self.size,
            truncated=cursor < self.dropped,
        )

    def job(self) -> JobShell | None:
        """The shell as an OpenCode job, if a session started it (``sessionID`` metadata)."""
        owner = self.info.metadata.get("sessionID")
        if not isinstance(owner, str):
            return None
        status: Literal["running", "completed", "error", "cancelled"]
        match self.info.status:
            case "running":
                status = "running"
            case "exited":
                status = "completed" if self.info.exit == 0 else "error"
            case "timeout":
                status = "error"
            case "killed":
                status = "cancelled"
        return JobShell(
            id=self.info.id,
            session_id=owner,
            status=status,
            title=self.info.command,
            time=JobTime(started=self.info.time.started, completed=self.info.time.completed),
            shell_id=self.info.id,
            command=self.info.command,
        )


@dataclass
class ShellManager:
    """Runs and tracks background shell commands."""

    publish: Callable[[BaseModel, str, str], None]
    """Publishes ``(event, event_type, directory)``."""
    shells: dict[str, ShellProcess] = field(default_factory=dict)

    async def start(
        self,
        command: str,
        *,
        directory: str,
        cwd: str | None = None,
        timeout: int | None = None,
        metadata: dict[str, Any] | None = None,
        shell_id: str | None = None,
        env: dict[str, str] | None = None,
        on_exit: Callable[[ShellProcess], None] | None = None,
    ) -> ShellProcess:
        """Start a shell command in the background.

        Args:
            command: Command line
            directory: Location directory (default working directory)
            cwd: Working directory override
            timeout: Timeout in milliseconds
            metadata: Client metadata stored on the shell
            shell_id: Optional explicit ID
            env: Extra environment variables
            on_exit: Callback once the process finished
        """
        shell = default_shell()
        sid = shell_id or identifiers.ascending("shell")
        workdir = cwd or directory
        process = await asyncio.create_subprocess_exec(
            shell,
            "-c",
            command,
            cwd=workdir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            stdin=asyncio.subprocess.DEVNULL,
            env={**os.environ, **env} if env else None,
        )
        info = ShellInfo(
            id=sid,
            status="running",
            command=command,
            cwd=workdir,
            shell=shell,
            file=shell,
            pid=process.pid,
            metadata=metadata or {},
            time=ShellInfoTime(started=now_ms()),
        )
        entry = ShellProcess(info=info, directory=directory, process=process)
        self.shells[sid] = entry
        self.publish(
            _event(ShellCreated, ShellCreatedData(info=info), directory), "shell.created", directory
        )
        self._publish_job(entry)
        entry.task = asyncio.create_task(self._run(entry, timeout, on_exit), name=f"shell-{sid}")
        return entry

    async def _run(
        self,
        entry: ShellProcess,
        timeout: int | None,
        on_exit: Callable[[ShellProcess], None] | None,
    ) -> None:
        process = entry.process
        assert process is not None
        assert process.stdout is not None
        status: ShellStatus = "exited"

        async def pump() -> None:
            assert process.stdout is not None
            while chunk := await process.stdout.read(65536):
                entry.append(chunk)

        try:
            await asyncio.wait_for(pump(), timeout=timeout / 1000 if timeout else None)
            await process.wait()
        except TimeoutError:
            status = "timeout"
            process.kill()
            await process.wait()
        except asyncio.CancelledError:
            status = "killed"
            if process.returncode is None:
                process.kill()
                await process.wait()
        if entry.info.status == "killed":
            status = "killed"
        exit_code = process.returncode
        entry.info = entry.info.model_copy(
            update={
                "status": status,
                "exit": float(exit_code) if exit_code is not None else None,
                "time": entry.info.time.model_copy(update={"completed": now_ms()}),
            }
        )
        entry.done.set()
        data = ShellExitedData(id=entry.info.id, exit=entry.info.exit, status=status)
        self.publish(_event(ShellExited, data, entry.directory), "shell.exited", entry.directory)
        self._publish_job(entry)
        if on_exit is not None:
            on_exit(entry)

    def _publish_job(self, entry: ShellProcess) -> None:
        """Publish ``experimental.job.updated`` for a shell a session started."""
        if (job := entry.job()) is None:
            return
        data = ExperimentalJobUpdatedData(info=job)
        event = _event(ExperimentalJobUpdated, data, entry.directory)
        self.publish(event, "experimental.job.updated", entry.directory)

    def get(self, shell_id: str) -> ShellProcess | None:
        """A tracked shell."""
        return self.shells.get(shell_id)

    async def kill(self, shell_id: str) -> bool:
        """Kill a running shell, keeping it (status ``killed``). False if it isn't running."""
        entry = self.shells.get(shell_id)
        if entry is None or entry.process is None or entry.process.returncode is not None:
            return False
        entry.info = entry.info.model_copy(update={"status": "killed"})
        entry.process.kill()
        if entry.task is not None:
            await asyncio.wait({entry.task}, timeout=5)
        return True

    async def remove(self, shell_id: str) -> bool:
        """Kill (if running) and forget a shell."""
        entry = self.shells.pop(shell_id, None)
        if entry is None:
            return False
        if entry.process is not None and entry.process.returncode is None:
            entry.info = entry.info.model_copy(update={"status": "killed"})
            entry.process.kill()
            if entry.task is not None:
                await asyncio.wait({entry.task}, timeout=5)
        data = ShellDeletedData(id=shell_id)
        self.publish(_event(ShellDeleted, data, entry.directory), "shell.deleted", entry.directory)
        return True

    async def shutdown(self) -> None:
        """Kill all running shells."""
        for shell_id in list(self.shells):
            entry = self.shells[shell_id]
            if entry.process is not None and entry.process.returncode is None:
                entry.process.kill()
        tasks = [e.task for e in self.shells.values() if e.task is not None]
        if tasks:
            await asyncio.wait(tasks, timeout=5)


def _event[E: BaseModel](event_cls: type[E], data: BaseModel, directory: str) -> E:
    return build_event(event_cls, data, directory=directory)
