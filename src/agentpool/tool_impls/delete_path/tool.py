"""Delete path tool implementation."""

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agentpool.agents.context import AgentContext  # noqa: TC001
from agentpool.log import get_logger
from agentpool.tools.base import Tool


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from exxec import ExecutionEnvironment
    from fsspec.asyn import AsyncFileSystem


logger = get_logger(__name__)


@dataclass
class DeletePathTool(Tool[dict[str, Any]]):
    """Delete files or directories from the filesystem.

    A standalone tool for deleting paths with:
    - File and directory deletion
    - Recursive directory deletion with safety checks
    - Empty directory validation
    - Detailed operation results

    Use create_delete_path_tool() factory for convenient instantiation.
    """

    # Tool-specific configuration
    env: ExecutionEnvironment | None = None
    """Execution environment to use. Falls back to agent.env if not set."""

    fs: AsyncFileSystem | None = None
    """Filesystem to operate on (takes precedence over ``env``).

    Set for a custom filesystem: the tool then never touches the agent's
    environment (no host filesystem, cwd or subprocesses).
    """

    cwd: str | None = None
    """Working directory for resolving relative paths."""

    def get_callable(self) -> Callable[..., Awaitable[dict[str, Any]]]:
        """Return the delete_path method as the callable."""
        return self._delete_path

    def _get_fs(self, ctx: AgentContext) -> AsyncFileSystem:
        """Get the filesystem: explicit fs, then env, then the agent's env."""
        if self.fs is not None:
            return self.fs
        return ctx.agent.env.get_fs() if self.env is None else self.env.get_fs()

    def _resolve_path(self, path: str, ctx: AgentContext) -> str:
        """Resolve a potentially relative path to an absolute path."""
        cwd: str | None = None
        if self.cwd:
            cwd = self.cwd
        elif self.env and self.env.cwd:
            cwd = self.env.cwd
        elif self.fs is None and ctx.agent.env and ctx.agent.env.cwd:
            cwd = ctx.agent.env.cwd

        if cwd and not (path.startswith("/") or (len(path) > 1 and path[1] == ":")):
            return str(Path(cwd) / path)
        return path

    async def _delete_path(
        self,
        ctx: AgentContext,
        path: str,
        recursive: bool = False,
    ) -> dict[str, Any]:
        """Delete a file or directory.

        Args:
            ctx: Agent context for event emission and filesystem access
            path: Path to delete
            recursive: Whether to delete directories recursively

        Returns:
            Dictionary with operation result
        """
        path = self._resolve_path(path, ctx)
        msg = f"Deleting path: {path}"
        await ctx.events.tool_call_start(title=msg, kind="delete", locations=[path])

        try:
            # Check if path exists and get its type
            fs = self._get_fs(ctx)
            try:
                info = await fs._info(path)
                path_type = info.get("type", "unknown")
            except FileNotFoundError:
                msg = f"Path does not exist: {path}"
                await ctx.events.file_operation("delete", path=path, success=False, error=msg)
                return {"error": msg}
            except (OSError, ValueError) as e:
                msg = f"Could not check path {path}: {e}"
                await ctx.events.file_operation("delete", path=path, success=False, error=msg)
                return {"error": msg}

            if path_type == "directory":
                if not recursive:
                    try:
                        contents = await fs._ls(path)
                        if contents:  # Check if directory is empty
                            error_msg = (
                                f"Directory {path} is not empty. "
                                f"Use recursive=True to delete non-empty directories"
                            )

                            # Emit failure event
                            await ctx.events.file_operation(
                                "delete", path=path, success=False, error=error_msg
                            )

                            return {"error": error_msg}
                    except OSError, ValueError:
                        pass  # Continue with deletion attempt

                await fs._rm(path, recursive=recursive)
            else:  # It's a file
                await fs._rm(path)

        except Exception as e:  # noqa: BLE001
            await ctx.events.file_operation("delete", path=path, success=False, error=str(e))
            return {"error": f"Failed to delete {path}: {e}"}
        else:
            result = {
                "path": path,
                "deleted": True,
                "type": path_type,
                "recursive": recursive,
            }
            await ctx.events.file_operation("delete", path=path, success=True)
            return result
