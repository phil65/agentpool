"""Command hook implementation."""

import asyncio
from pathlib import Path
import shlex
from typing import TYPE_CHECKING, Any

import anyenv

from agentpool.hooks.base import Hook, HookResult
from agentpool.log import get_logger


if TYPE_CHECKING:
    from exxec import ExecutionEnvironment

    from agentpool.hooks.base import HookEvent, HookInput


logger = get_logger(__name__)


class CommandHook(Hook):
    """Hook that executes a shell command.

    The command receives hook input as JSON via stdin and should return
    JSON output via stdout.

    Exit codes:
    - 0: Success, stdout parsed as JSON for result
    - 2: Block/deny, stderr used as reason
    - Other: Non-blocking error, logged but execution continues
    """

    def __init__(
        self,
        event: HookEvent,
        command: str,
        matcher: str | None = None,
        timeout: float = 60.0,
        enabled: bool = True,
        env: dict[str, str] | None = None,
        execution_env: ExecutionEnvironment | None = None,
    ) -> None:
        """Initialize command hook.

        Args:
            event: The lifecycle event this hook handles.
            command: Shell command to execute.
            matcher: Regex pattern for matching.
            timeout: Maximum execution time in seconds.
            enabled: Whether this hook is active.
            env: Additional environment variables.
            execution_env: Per-hook execution environment override.
                If set, this takes priority over the agent's environment.
        """
        super().__init__(event=event, matcher=matcher, timeout=timeout, enabled=enabled)
        self.command = command
        self.env = env or {}
        self._execution_env = execution_env

    async def execute(
        self,
        input_data: HookInput,
        env: ExecutionEnvironment | None = None,
    ) -> HookResult:
        """Execute the shell command.

        Uses the execution environment (per-hook override > agent env > local)
        to run the command. The hook input data is passed as JSON via stdin.

        Args:
            input_data: The hook input data, passed as JSON to stdin.
            env: Agent's execution environment. Used if no per-hook override is set.

        Returns:
            Hook result parsed from command output.
        """
        from exxec.local_provider import LocalExecutionEnvironment

        # Resolve execution environment: per-hook override > agent env > local fallback
        given_env = self._execution_env or env
        effective_env = given_env or LocalExecutionEnvironment(
            env_vars=self.env or None, inherit_env=True
        )

        # Expand $PROJECT_DIR if present
        command = self.command
        if "$PROJECT_DIR" in command:
            project_dir = self.env.get("PROJECT_DIR", str(Path.cwd()))
            command = command.replace("$PROJECT_DIR", shlex.quote(project_dir))

        # Serialize input and pipe via shell
        input_json = anyenv.dump_json(dict(input_data))
        full_command = f"echo {shlex.quote(input_json)} | {command}"
        if self.env and given_env is not None:
            # A given environment doesn't know the hook's variables: set them here
            exports = "; ".join(
                f"export {name}={shlex.quote(value)}" for name, value in self.env.items()
            )
            full_command = f"{exports}; {full_command}"

        try:
            result = await asyncio.wait_for(
                effective_env.execute_command(full_command, timeout=self.timeout),
                timeout=self.timeout + 5,  # outer timeout as safety net
            )
            stdout_str = (result.stdout or "").strip()
            stderr_str = (result.stderr or "").strip()
            exit_code = result.exit_code

            if exit_code == 0:
                return _parse_success_output(stdout_str)
            if exit_code == 2:  # noqa: PLR2004
                reason = stderr_str or "Hook denied the operation"
                return HookResult(decision="deny", reason=reason)
            logger.warning("Hook command failed", returncode=exit_code, stderr=stderr_str)
            return HookResult(decision="allow")

        except TimeoutError:
            logger.exception("Hook command timed out", timeout=self.timeout, command=command)
            return HookResult(decision="allow")
        except Exception as e:
            logger.exception("Hook command failed", command=command)
            return HookResult(decision="allow", reason=str(e))


def _parse_success_output(stdout: str) -> HookResult:
    """Parse successful command output.

    Args:
        stdout: Command stdout.

    Returns:
        Parsed hook result.
    """
    if not stdout:
        return HookResult(decision="allow")

    try:
        data = anyenv.load_json(stdout)
        return _normalize_result(data)
    except anyenv.JsonLoadError:
        # Plain text output treated as additional context
        return HookResult(decision="allow", additional_context=stdout)


def _normalize_result(data: dict[str, Any]) -> HookResult:
    """Normalize command output to HookResult.

    Args:
        data: Parsed JSON data.

    Returns:
        Normalized hook result.
    """
    # Handle decision field (support various naming conventions)
    match data.get("decision") or data.get("permissionDecision"):
        # Normalize decision values (none given: the other fields still apply)
        case "approve" | "allow" | None:
            result = HookResult(decision="allow")
        case "block" | "deny":
            result = HookResult(decision="deny")
        case "ask":
            result = HookResult(decision="ask")
        case _ as decision:
            raise ValueError(f"Invalid decision: {decision}")

    # Handle reason field
    reason = data.get("reason") or data.get("permissionDecisionReason")
    if reason:
        result["reason"] = reason

    # Handle modified input
    if "modified_input" in data:
        result["modified_input"] = data["modified_input"]
    elif "updatedInput" in data:
        result["modified_input"] = data["updatedInput"]

    # Handle additional context
    if "additional_context" in data:
        result["additional_context"] = data["additional_context"]
    elif "additionalContext" in data:
        result["additional_context"] = data["additionalContext"]

    # Handle continue flag
    if "continue" in data:
        result["continue_"] = data["continue"]
    elif "continue_" in data:
        result["continue_"] = data["continue_"]

    return result
