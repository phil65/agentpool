"""Claude Agent SDK to native event converters.

This module provides conversion from Claude Agent SDK message types to native
agentpool streaming events, enabling ClaudeCodeAgent to yield the same
event types as native agents.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
import re
from typing import TYPE_CHECKING, Any, Literal, assert_never, cast

from clawd_code_sdk.models import (
    BashInput,
    BashOutput,
    EditOutput,
    McpHttpServerConfig,
    McpSSEServerConfig,
    McpStdioServerConfig,
    ReadOutput,
    ResultErrorMessage,
    ResultSuccessMessage,
    TodoWriteOutput,
    UserDocumentPrompt,
    UserDocumentURLPrompt,
    UserFilePrompt,
    UserImagePrompt,
    UserImageURLPrompt,
    UserTextPrompt,
    WriteOutput,
)
from pydantic_ai import (
    AudioUrl,
    BinaryContent,
    CachePoint,
    DocumentUrl,
    ImageUrl,
    RequestUsage,
    RunUsage,
    TextContent,
    UploadedFile,
    VideoUrl,
)

from agentpool.common_types import MCPServerStatus
from agentpool.utils.diffs import compute_unified_diff
from opencode_sdk.models.tool_metadata import (
    BashMetadata,
    EditMetadata,
    FileDiff,
    ReadMetadata,
    TodoInfo,
    TodoMetadata,
    WriteMetadata,
)


if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from clawd_code_sdk.models import (
        HookContext,
        HookEvent,
        HookJSONOutput,
        HookMatcher,
        McpServerConfig,
        McpServerStatusEntry,
        PermissionResult,
        PostToolUseHookInput,
        PreToolUseHookInput,
        ResultMessage,
        StopReason,
        StructuredPatchHunk,
        SyncHookJSONOutput,
        ToolInput,
        ToolUseResult,
        Usage,
        UserPrompt,
    )
    from clawd_code_sdk.models.server_info import ClaudeCodeAccountInfo, ClaudeCodeModelInfo
    from exxec import ExecutionEnvironment
    from pydantic_ai import FinishReason, UserContent
    from tokonomics.model_discovery.model_info import ModelInfo

    from agentpool.agents.auth import AuthStatus
    from agentpool.agents.context import ConfirmationResult
    from agentpool.hooks import AgentHooks
    from agentpool_config.mcp_server import MCPServerConfig as NativeMCPServerConfig
    from opencode_sdk.models.tool_metadata import ToolMetadata


def to_auth_status(account: ClaudeCodeAccountInfo | None) -> AuthStatus:
    from agentpool.agents.auth import AuthAccount, AuthStatus

    if account is None:
        return AuthStatus(state="unknown")
    if account.api_provider not in (None, "firstParty"):
        # Third-party backends (Bedrock, Vertex, ...) use external credentials, no sign-in
        external = AuthAccount(label=account.api_provider, description="External credentials")
        return AuthStatus(state="not_required", accounts=[external])
    source = account.token_source or account.api_key_source
    # Inferred: not verified what a logged-out CLI reports in its init account info.
    if not (source or account.email):
        return AuthStatus(state="unauthenticated")
    label = account.subscription_type or (
        "Anthropic API key" if account.api_key_source else "Claude account"
    )
    auth_account = AuthAccount(
        label=label,
        description=source,
        active=True,
        email=account.email,
        organization=account.organization,
    )
    return AuthStatus(state="authenticated", accounts=[auth_account])


DEFAULT_CONTEXT_WINDOW = 1_000_000
"""Context window of a model until the CLI reports it (what current Claude models have)."""


def to_model_info(model: ClaudeCodeModelInfo, *, context_window: int) -> ModelInfo:
    """A model the CLI offers, with its ID as the CLI takes it (``opus``, ``sonnet[1m]``).

    The model list does not report context windows: the caller passes what it knows.
    """
    from tokonomics.model_discovery.model_info import ModelInfo

    return ModelInfo(
        id=model.value,
        name=model.display_name,
        provider="anthropic",
        description=model.description,
        context_window=context_window,
        input_modalities={"text", "image"},
        id_override=model.value,
    )


def to_mcp_server_status(server: McpServerStatusEntry) -> MCPServerStatus:
    return MCPServerStatus(
        name=server.name,
        status=server.status,
        server_type=server.config.type if server.config else "unknown",
        server_name=server.server_info.name if server.server_info else None,
        server_version=server.server_info.version if server.server_info else None,
    )


def to_prompt_input(content: Sequence[UserContent]) -> Iterator[UserPrompt]:
    from agentpool.agents.claude_code_agent.slash_commands import to_cli_command_text

    first_text = True
    for item in content:
        match item:
            case BinaryContent(
                media_type=("image/png" | "image/jpeg" | "image/gif" | "image/webp") as mime,
                base64=b64,
            ):
                yield UserImagePrompt(image_data=b64, media_type=mime)
            case BinaryContent(media_type="application/pdf", base64=b64):
                yield UserDocumentPrompt(document_data=b64, media_type="application/pdf")
            case BinaryContent(media_type=str() as mime, data=data) if mime.startswith("text/"):
                text = data.decode(errors="replace")
                yield UserTextPrompt(text=f'<context ref="{item.identifier}">\n{text}\n</context>')
            case BinaryContent(media_type=mime) if item.is_image or item.is_document:
                raise ValueError(f"Unsupported media type for Claude Code prompt: {mime}")
            case ImageUrl(url=url):
                yield UserImageURLPrompt(url=url)
            case (
                AudioUrl(url=url, identifier=identifier)
                | VideoUrl(url=url, identifier=identifier)
                | DocumentUrl(url=url, identifier=identifier)
            ):
                yield UserDocumentURLPrompt(url=url, title=identifier)
            case UploadedFile(provider_name="anthropic", file_id=file_id):
                yield UserFilePrompt(file_id=file_id)
            case UploadedFile(file_id=file_id, provider_name=provider_name):
                raise ValueError(f"Unsupported UploadedFile: {provider_name=} {file_id=}")
            case str(text) | TextContent(content=text):
                # A command is the first text: give MCP prompt commands the CLI's spelling
                if first_text:
                    text = to_cli_command_text(text)
                    first_text = False
                yield UserTextPrompt(text=text)
            case BinaryContent():
                pass  # video/audio not handled yet
            case CachePoint():
                pass  # can get ignored
            case _ as unreachable:
                assert_never(unreachable)


def to_run_usage(usage: Usage) -> RunUsage:
    """Convert SDK Usage to RunUsage."""
    return RunUsage(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=usage.cache_read_input_tokens,
        cache_write_tokens=usage.cache_creation_input_tokens,
    )


def to_request_usage(usage: Usage) -> RequestUsage:
    """Convert SDK Usage to RequestUsage."""
    return RequestUsage(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=usage.cache_read_input_tokens,
        cache_write_tokens=usage.cache_creation_input_tokens,
    )


def confirmation_result_to_native(result: ConfirmationResult) -> PermissionResult:
    from clawd_code_sdk import PermissionResultAllow, PermissionResultDeny

    match result:
        case "allow":
            return PermissionResultAllow()
        case "skip":
            return PermissionResultDeny(message="User skipped tool execution")
        case "abort_run" | "abort_chain":
            return PermissionResultDeny(message="User aborted execution", interrupt=True)
        case _ as unreachable:
            raise assert_never(unreachable)


FINISH_REASON_MAP: dict[StopReason, FinishReason] = {
    "end_turn": "stop",
    "max_tokens": "length",
    "stop_sequence": "stop",
    "tool_use": "tool_call",
    "pause_turn": "stop",
    "refusal": "content_filter",
    "model_context_window_exceeded": "length",
}


def to_finish_reason(reason: StopReason) -> FinishReason:
    return FINISH_REASON_MAP[reason]


_LOGIN_REQUIRED = re.compile(r"\brun /login\b", re.IGNORECASE)
"""How the CLI asks to sign in ("Not logged in · Run /login", "Login expired · Run /login ...")."""


@dataclass(frozen=True, slots=True)
class TurnError:
    """A turn that Claude Code's result reports as failed."""

    message: str
    """Claude Code's error text."""
    code: str
    """``login_required``, ``error`` or the result's error subtype (e.g. ``error_max_turns``)."""


def to_turn_error(result: ResultMessage) -> TurnError | None:
    """The failure a result reports, or None for a turn that ended normally.

    A refusal and a ``max_tokens`` stop end the turn without failing it, also on an
    error result: their stop reason says why the turn ended. An interrupt is a
    cancellation, not a failure.
    """
    if (
        not result.is_error
        or result.stop_reason in ("refusal", "max_tokens")
        or result.terminal_reason in ("aborted_streaming", "aborted_tools")
    ):
        return None
    match result:
        case ResultSuccessMessage(result=str() as text) if _LOGIN_REQUIRED.search(text):
            return TurnError(message=text, code="login_required")
        case ResultSuccessMessage(result=text):
            return TurnError(message=text or "Claude Code reported an error", code="error")
        case ResultErrorMessage(subtype=subtype, errors=errors):
            return TurnError(message=", ".join(errors or []) or subtype, code=subtype)
        case _ as unreachable:
            assert_never(unreachable)


def convert_mcp_servers_to_sdk_format(
    mcp_servers: list[NativeMCPServerConfig],
) -> dict[str, McpServerConfig]:
    """Convert internal MCPServerConfig to Claude SDK format.

    Returns:
        Dict mapping server names to SDK-compatible config dicts
    """
    from urllib.parse import urlparse

    from agentpool_config.mcp_server import (
        SSEMCPServerConfig,
        StdioMCPServerConfig,
        StreamableHTTPMCPServerConfig,
    )

    result: dict[str, McpServerConfig] = {}

    for idx, server in enumerate(mcp_servers):
        # Determine server name
        match server:
            case _ if server.name:
                name = server.name
            case StdioMCPServerConfig() if server.args:
                name = server.args[-1].split("/")[-1].split("@")[0]
            case StdioMCPServerConfig():
                name = server.command
            case SSEMCPServerConfig() | StreamableHTTPMCPServerConfig():
                name = urlparse(str(server.url)).hostname or f"server_{idx}"
            case _ as unreachable:
                assert_never(unreachable)

        # Build SDK-compatible config (None fields are omitted from --mcp-config)
        config: McpServerConfig
        always_load = True if server.always_load else None
        match server:
            case StdioMCPServerConfig(command=command, args=args):
                config = McpStdioServerConfig(command=command, args=args, alwaysLoad=always_load)
                if server.env:
                    config.env = server.get_env_vars()
            case SSEMCPServerConfig(url=url):
                config = McpSSEServerConfig(url=str(url), alwaysLoad=always_load)
                if server.headers:
                    config.headers = server.headers
            case StreamableHTTPMCPServerConfig(url=url):
                config = McpHttpServerConfig(url=str(url), alwaysLoad=always_load)
                if server.headers:
                    config.headers = server.headers
            case _ as unreachable:
                assert_never(unreachable)

        result[name] = config

    return result


def convert_to_opencode_metadata(
    tool_name: str,
    tool_use_result: Mapping[str, Any] | ToolUseResult | str | None,
    tool_input: ToolInput | dict[str, Any] | None = None,
) -> ToolMetadata | None:
    """Convert Claude Code SDK tool_use_result to OpenCode metadata format."""
    # Handle None or string results (bash errors come as plain strings)
    if tool_use_result is None or not isinstance(tool_use_result, dict):
        return None
    tool_input = tool_input or {}
    # Dispatch to appropriate converter based on tool name
    match tool_name.lower():
        case "write":
            return _convert_write_result(cast(WriteOutput, tool_use_result))
        case "edit":
            return _convert_edit_result(cast(EditOutput, tool_use_result))
        case "read":
            return _convert_read_result(cast(ReadOutput, tool_use_result))
        case "todowrite":
            return _convert_todowrite_result(cast(TodoWriteOutput, tool_use_result))
        case _:
            return None


def _convert_write_result(result: WriteOutput) -> WriteMetadata:
    """Convert Write tool result to OpenCode metadata."""
    return WriteMetadata(filepath=result["filePath"], exists=True, diagnostics={})


def _convert_edit_result(result: EditOutput) -> EditMetadata:
    """Convert Edit tool result to OpenCode metadata."""
    file_path = result["filePath"]
    original_file = result["originalFile"]
    structured_patch = result["structuredPatch"]
    # Compute the "after" content by applying the edit
    after_content = original_file
    if original_file is not None and (old := result["oldString"]) and (new := result["newString"]):
        after_content = original_file.replace(old, new, 1)

    # Build unified diff from structuredPatch or compute it
    diff = _build_unified_diff(file_path, original_file, after_content, structured_patch)
    # Count additions and deletions
    additions, deletions = _count_diff_changes(structured_patch)
    filediff = FileDiff(
        file=file_path,
        before=original_file or "",
        after=after_content or "",
        additions=additions,
        deletions=deletions,
    )
    return EditMetadata(diff=diff, filediff=filediff, diagnostics={})


def _convert_read_result(result: ReadOutput) -> ReadMetadata:
    """Convert Read tool result to OpenCode metadata."""
    match result:
        case {
            "type": "text",
            "file": {"content": str(content), "numLines": int(num), "totalLines": int(total)},
        }:
            lines = content.splitlines()
            preview = "\n".join(lines[:20])
            return ReadMetadata(preview=preview, truncated=num < total, loaded=[])
        case _:  # Only text reads have metadata support for Opencode
            return ReadMetadata(preview="", truncated=False, loaded=[])


_EXIT_CODE = re.compile(r"^(?:Error: )?Exit code (\d+)(?:\n|$)")
"""How the CLI starts the result of a command that exited with a failing code."""


@dataclass(frozen=True, slots=True)
class BashDisplay:
    """What a Bash call shows: its output and how it exited."""

    output: str
    """stdout and stderr, without the notes the CLI adds for the model."""
    exit_code: int | None
    """None when unknown: a failure without a code, an interrupt, a non-zero exit that is
    no error (``grep`` without matches), or a command still running in the background."""
    interrupted: bool = False


def to_bash_display(
    text: str, structured: ToolUseResult | Mapping[str, Any] | str | None, *, is_error: bool
) -> BashDisplay:
    """The display of a Bash call, from its result text and structured ``tool_use_result``.

    The structured output holds the command's own stdout and stderr; the text is what the
    model got (with the CLI's notes, e.g. the ``<persisted-output>`` wrapper of a large
    output). A failed command's result is text only, starting with ``Exit code N``.
    """
    output = text
    exit_code: int | None = None if is_error else 0
    interrupted = False
    match structured:
        case {"stdout": str(stdout), "stderr": str(stderr), "backgroundTaskId": str()}:
            exit_code = None  # Still running: the text announces it
        case {"stdout": str(stdout), "stderr": str(stderr)} if not structured.get("isImage"):
            bash = cast(BashOutput, structured)
            output = "\n".join(part for part in (stdout, stderr) if part)
            if bash["interrupted"]:
                output = _with_note(output, "[Command was aborted before completion]")
                exit_code = None
                interrupted = True
            if (interpretation := bash.get("returnCodeInterpretation")) is not None:
                exit_code = None
                output = output or interpretation
            if (path := bash.get("persistedOutputPath")) is not None:
                size = bash.get("persistedOutputSize")
                total = f" ({size} bytes total)" if size is not None else ""
                output = _with_note(
                    output, f"[Output truncated{total}: full output saved to {path}]"
                )
        case _:
            pass
    if is_error and (match := _EXIT_CODE.match(output)):
        exit_code = int(match[1])
        output = output[match.end() :]
    return BashDisplay(output=output, exit_code=exit_code, interrupted=interrupted)


def _with_note(output: str, note: str) -> str:
    return f"{output}\n{note}" if output else note


def to_bash_metadata(display: BashDisplay, tool_input: BashInput) -> BashMetadata:
    """OpenCode metadata of a Bash call."""
    description = tool_input.get("description") or tool_input["command"]
    return BashMetadata(output=display.output, exit=display.exit_code, description=description)


def structured_results(
    tool_use_result: Sequence[ToolUseResult | dict[str, Any]]
    | ToolUseResult
    | dict[str, Any]
    | str
    | None,
) -> list[ToolUseResult | Mapping[str, Any] | str]:
    """A user message's structured tool results, one per tool result block.

    The CLI sends a single result (an object, or a string for failures) for a message
    with one tool result, and a list for several.
    """
    match tool_use_result:
        case None:
            return []
        case str() | Mapping():
            return [tool_use_result]
        case _:
            return list(tool_use_result)


def _convert_todowrite_result(result: TodoWriteOutput) -> TodoMetadata | None:
    """Convert TodoWrite tool result to OpenCode metadata."""
    new_todos = result["newTodos"]
    todos: list[TodoInfo] = []
    for i, todo in enumerate(new_todos):
        content = todo["content"]
        priority = _infer_priority(content, i, len(new_todos))
        todos.append(TodoInfo(content=content, status=todo["status"], priority=priority))
    return TodoMetadata(todos=todos)


# Priority thresholds for position-based inference
_HIGH_PRIORITY_THRESHOLD = 0.33
_MEDIUM_PRIORITY_THRESHOLD = 0.67


def _infer_priority(content: str, index: int, total: int) -> Literal["low", "medium", "high"]:
    """Infer priority from content keywords or position."""
    content_lower = content.lower()

    # Check for explicit priority keywords
    high_keywords = ("critical", "urgent", "asap", "immediately", "important", "soon", "priority")
    low_keywords = ("later", "eventually", "low priority", "nice to have")

    if any(kw in content_lower for kw in high_keywords):
        return "high"
    if any(kw in content_lower for kw in low_keywords):
        return "low"

    # Fall back to position-based priority
    # First third = high, middle third = medium, last third = low
    if total <= 1:
        return "medium"
    position_ratio = index / (total - 1) if total > 1 else 0
    if position_ratio < _HIGH_PRIORITY_THRESHOLD:
        return "high"
    if position_ratio < _MEDIUM_PRIORITY_THRESHOLD:
        return "medium"
    return "low"


def _build_unified_diff(
    file_path: str,
    before: str | None,
    after: str | None,
    structured_patch: list[StructuredPatchHunk],
) -> str:
    """Build unified diff string from structured patch or content."""
    # If we have both before and after, compute proper diff
    if before is not None and after is not None:
        name = Path(file_path).name
        return compute_unified_diff(
            before,
            after,
            fromfile=f"a/{name}",
            tofile=f"b/{name}",
            ensure_trailing_newline=True,
        )

    # Fallback: reconstruct from structuredPatch
    if structured_patch:
        return _structured_patch_to_diff(file_path, structured_patch)

    return ""


def _structured_patch_to_diff(file_path: str, structured_patch: list[StructuredPatchHunk]) -> str:
    """Convert Claude Code's structuredPatch to unified diff format.

    structuredPatch format:
        [
            {
                "oldStart": 1,
                "oldLines": 4,
                "newStart": 1,
                "newLines": 5,
                "lines": [" def hello_world():", "+    \"\"\"Docstring.\"\"\"", ...]
            }
        ]

    The lines array uses prefixes: " " (context), "+" (added), "-" (removed)
    """
    name = Path(file_path).name
    lines = [f"--- a/{name}", f"+++ b/{name}"]
    for p in structured_patch:
        # Add hunk header
        lines.append(f"@@ -{p['oldStart']},{p['oldLines']} +{p['newStart']},{p['newLines']} @@")
        # Add the diff lines (already prefixed with ' ', '+', or '-')
        lines.extend(p["lines"])
    return "\n".join(lines) + "\n" if lines else ""


def _count_diff_changes(structured_patch: list[StructuredPatchHunk]) -> tuple[int, int]:
    """Count additions and deletions from structured patch."""
    additions = 0
    deletions = 0

    for hunk in structured_patch:
        for line in hunk["lines"]:
            if line.startswith("+"):
                additions += 1
            elif line.startswith("-"):
                deletions += 1

    return additions, deletions


def build_sdk_hooks_from_agent_hooks(
    hooks: AgentHooks,
    agent_name: str,
    env: ExecutionEnvironment | None = None,
) -> dict[HookEvent, list[HookMatcher]]:
    """Convert AgentHooks to Claude SDK hooks format.

    Args:
        hooks: AgentHooks instance with pre/post tool hooks
        agent_name: Name of the agent for context
        env: Agent's execution environment, passed to command hooks

    Returns:
        Dictionary mapping hook event names to HookMatcher lists
    """
    from clawd_code_sdk.models import HookMatcher

    result: dict[HookEvent, list[HookMatcher]] = {}
    if hooks.pre_tool_use:

        async def on_pre_tool_use(
            input_data: PreToolUseHookInput,
            tool_use_id: str | None,
            context: HookContext,
        ) -> HookJSONOutput:
            """Adapter for pre_tool_use hooks."""
            pre_result = await hooks.run_pre_tool_hooks(
                agent_name=agent_name,
                tool_name=input_data["tool_name"],
                tool_input=input_data["tool_input"],
                session_id=input_data.get("session_id"),
                env=env,
            )
            # Convert our hook result to SDK format
            decision = pre_result.get("decision")
            if decision == "deny":
                reason = pre_result.get("reason", "Blocked by pre-tool hook")
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": reason,
                    }
                }

            # Check for modified input
            output: SyncHookJSONOutput = {}
            if modified := pre_result.get("modified_input"):
                output["hookSpecificOutput"] = {
                    "hookEventName": "PreToolUse",
                    "updatedInput": modified,
                }

            return output

        result["PreToolUse"] = [HookMatcher(matcher="*", hooks=[on_pre_tool_use])]  # pyright: ignore[reportArgumentType]

    if hooks.post_tool_use:

        async def on_post_tool_use(
            input_data: PostToolUseHookInput,
            tool_use_id: str | None,
            context: HookContext,
        ) -> dict[str, Any]:
            """Adapter for post_tool_use hooks."""
            await hooks.run_post_tool_hooks(
                agent_name=agent_name,
                tool_name=input_data["tool_name"],
                tool_input=input_data["tool_input"],
                tool_output=input_data["tool_response"],
                duration_ms=0,  # SDK doesn't provide timing
                session_id=input_data.get("session_id"),
                env=env,
            )

            # Post hooks are observation-only in SDK, can add context
            return {}

        result["PostToolUse"] = [HookMatcher(matcher="*", hooks=[on_post_tool_use])]  # pyright: ignore[reportArgumentType]

    return result
