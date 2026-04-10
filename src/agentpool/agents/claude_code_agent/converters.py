"""Claude Agent SDK to native event converters.

This module provides conversion from Claude Agent SDK message types to native
agentpool streaming events, enabling ClaudeCodeAgent to yield the same
event types as native agents.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, assert_never, cast

from clawd_code_sdk.models import (
    BashInput,
    BashOutput,
    EditOutput,
    McpHttpServerConfig,
    McpSSEServerConfig,
    McpStdioServerConfig,
    ReadOutput,
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
from pydantic_ai.models.anthropic import _FINISH_REASON_MAP as FINISH_REASON_MAP

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
        StopReason,
        StructuredPatchHunk,
        SyncHookJSONOutput,
        ThinkingConfig,
        ToolInput,
        ToolUseResult,
        Usage,
        UserPrompt,
    )
    from exxec import ExecutionEnvironment
    from pydantic_ai import FinishReason, UserContent

    from agentpool.agents.context import ConfirmationResult
    from agentpool.hooks import AgentHooks
    from agentpool_config.mcp_server import MCPServerConfig as NativeMCPServerConfig
    from opencode_sdk.models.tool_metadata import ToolMetadata


def to_thinking_config(
    max_thinking_tokens: int | Literal["adaptive"] | None,
) -> ThinkingConfig | None:
    from clawd_code_sdk import ThinkingConfigAdaptive, ThinkingConfigDisabled, ThinkingConfigEnabled

    match max_thinking_tokens:
        case "adaptive":
            return ThinkingConfigAdaptive()
        case 0:
            return ThinkingConfigDisabled()
        case int(tokens):
            return ThinkingConfigEnabled(budget_tokens=tokens)
        case None:
            return None


def to_mcp_server_status(server: McpServerStatusEntry) -> MCPServerStatus:
    return MCPServerStatus(
        name=server.name,
        status=server.status,
        server_type=server.config.type if server.config else "unknown",
        server_name=server.server_info.name if server.server_info else None,
        server_version=server.server_info.version if server.server_info else None,
    )


def to_prompt_input(content: Sequence[UserContent]) -> Iterator[UserPrompt]:
    for item in content:
        match item:
            case BinaryContent(media_type=mime, base64=b64) if item.is_image:
                yield UserImagePrompt(image_data=b64, media_type=mime)  # type: ignore[arg-type]
            case BinaryContent(media_type=mime, base64=b64) if item.is_document:
                yield UserDocumentPrompt(document_data=b64, media_type=mime)  # type: ignore[arg-type]
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


def to_finish_reason(reason: StopReason) -> FinishReason:
    return FINISH_REASON_MAP[reason]


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

        # Build SDK-compatible config
        config: McpServerConfig
        match server:
            case StdioMCPServerConfig(command=command, args=args):
                config = McpStdioServerConfig(command=command, args=args)
                if server.env:
                    config.env = server.get_env_vars()
            case SSEMCPServerConfig(url=url):
                config = McpSSEServerConfig(url=str(url))
                if server.headers:
                    config.headers = server.headers
            case StreamableHTTPMCPServerConfig(url=url):
                config = McpHttpServerConfig(url=str(url))
                if server.headers:
                    config.headers = server.headers
            case _ as unreachable:
                assert_never(unreachable)

        result[name] = config

    return result


def convert_to_opencode_metadata(  # noqa: PLR0911
    tool_name: str,
    tool_use_result: dict[str, Any] | ToolUseResult | str | None,
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
        case "bash":
            return _convert_bash_result(
                cast(BashOutput, tool_use_result),
                cast(BashInput, tool_input),
            )
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


def _convert_bash_result(result: BashOutput, tool_input: BashInput) -> BashMetadata:
    """Convert Bash tool result to OpenCode metadata."""
    output = f"{result['stdout']}\n{result['stderr']}".strip()
    # Get description from tool input (Claude Code uses "description" field)
    description = tool_input.get("description") or tool_input["command"]
    # Note: Claude Code SDK doesn't provide exit code in the success result structure,
    # it's only available in error strings. For successful commands, exit is 0.
    # The SDK result doesn't have an exit_code field, so we infer:
    # - If we got here with a dict result, the command likely succeeded (exit 0)
    # - Errors come as strings, not dicts
    exit_code: int | None = 0
    if result["interrupted"]:
        exit_code = None  # Interrupted commands don't have a clean exit code
    return BashMetadata(output=output, exit=exit_code, description=description)


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

        result["PreToolUse"] = [HookMatcher(matcher="*", hooks=[on_pre_tool_use])]  # ty:ignore[invalid-argument-type]  # pyright: ignore[reportArgumentType]

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

        result["PostToolUse"] = [HookMatcher(matcher="*", hooks=[on_post_tool_use])]  # ty:ignore[invalid-argument-type]  # pyright: ignore[reportArgumentType]

    return result
