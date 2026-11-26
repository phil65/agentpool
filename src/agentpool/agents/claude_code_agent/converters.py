"""Claude Agent SDK to native event converters.

This module provides conversion from Claude Agent SDK message types to native
agentpool streaming events, enabling ClaudeCodeAgent to yield the same
event types as native agents.
"""

from collections.abc import Mapping
from dataclasses import dataclass
import re
from typing import TYPE_CHECKING, Any, assert_never, cast

from clawd_code_sdk.models import (
    BashOutput,
    EditOutput,
    McpHttpServerConfig,
    McpSSEServerConfig,
    McpStdioServerConfig,
    ReadTextOutput,
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

from agentpool.agents.events import DiffContentItem, PlanUpdateEvent, ToolCallProgressEvent
from agentpool.common_types import MCPServerStatus
from agentpool.tools.decisions import Approve, Deny
from agentpool.utils.todos import PlanEntry


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
        PreToolUseHookSpecificOutput,
        ResultMessage,
        StopReason,
        SyncHookJSONOutput,
        ToolUseResult,
        Usage,
        UserPrompt,
    )
    from clawd_code_sdk.models.server_info import ClaudeCodeAccountInfo, ClaudeCodeModelInfo
    from exxec import ExecutionEnvironment
    from pydantic_ai import FinishReason, UserContent
    from tokonomics.model_discovery.model_info import ModelInfo

    from agentpool.agents.auth import AuthStatus
    from agentpool.hooks import AgentHooks
    from agentpool.tools.decisions import ToolDecision
    from agentpool_config.mcp_server import MCPServerConfig as NativeMCPServerConfig


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


def decision_to_native(decision: ToolDecision, tool_name: str | None) -> PermissionResult:
    """The CLI's permission result for the user's decision on a tool call."""
    from clawd_code_sdk import PermissionResultAllow, PermissionResultDeny

    match decision:
        case Approve(args=args):
            return PermissionResultAllow(updated_input=args)
        case Deny(stop=stop) as denial:
            return PermissionResultDeny(message=denial.reason(tool_name), interrupt=stop)


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


def tool_result_events(  # noqa: PLR0911
    tc_id: str,
    tool_name: str,
    tool_use_result: Mapping[str, Any] | ToolUseResult | str | None,
) -> list[ToolCallProgressEvent | PlanUpdateEvent]:
    """What a tool's structured result (``tool_use_result``) shows beyond its text.

    - Edit, Write: the whole file before and after (the start showed only the edited
      snippet), as a diff; the turn records it as a file change (diffs, reverts).
    - TodoWrite: the todo list, as the plan.
    - Read of part of a file: which lines, in the title.
    """
    if not isinstance(tool_use_result, Mapping):
        return []
    match tool_name.lower(), tool_use_result:
        case "edit", {"filePath": str(path), "newString": str(new), "oldString": str(old)}:
            edit = cast(EditOutput, tool_use_result)
            before = edit["originalFile"]
            after = new if before is None else _apply_edit(before, old, new, edit["replaceAll"])
            diff = DiffContentItem(path=path, old_text=before, new_text=after)
            return [_diff_progress(tc_id, tool_name, diff)]
        case "write", {"filePath": str(path), "content": str(content)}:
            write = cast(WriteOutput, tool_use_result)
            before = write["originalFile"] if write["type"] == "update" else None
            diff = DiffContentItem(path=path, old_text=before, new_text=content)
            return [_diff_progress(tc_id, tool_name, diff)]
        case "todowrite", {"newTodos": list()}:
            todos = cast(TodoWriteOutput, tool_use_result)["newTodos"]
            entries = [PlanEntry(content=t["content"], status=t["status"]) for t in todos]
            return [PlanUpdateEvent(entries=entries, tool_call_id=tc_id)]
        case "read", {"type": "text", "file": {"filePath": str(path)}}:
            info = cast(ReadTextOutput, tool_use_result)["file"]
            first, count, total = info["startLine"], info["numLines"], info["totalLines"]
            if first <= 1 and count >= total:
                return []
            title = f"Read {path} (lines {first}-{first + max(count, 1) - 1} of {total})"
            return [ToolCallProgressEvent(tool_call_id=tc_id, tool_name=tool_name, title=title)]
        case _:
            return []


def _apply_edit(original: str, old: str, new: str, replace_all: bool) -> str:
    return original.replace(old, new) if replace_all else original.replace(old, new, 1)


def _diff_progress(tc_id: str, tool_name: str, diff: DiffContentItem) -> ToolCallProgressEvent:
    """The whole-file diff, replacing the snippet the start showed."""
    return ToolCallProgressEvent(
        tool_call_id=tc_id, tool_name=tool_name, items=[diff], replace_content=True
    )


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

            # "ask": the CLI asks for permission (our tool confirmation) even if its mode
            # wouldn't; modified input goes along either way
            output: SyncHookJSONOutput = {}
            specific: PreToolUseHookSpecificOutput = {"hookEventName": "PreToolUse"}
            if decision == "ask":
                specific["permissionDecision"] = "ask"
                if reason := pre_result.get("reason"):
                    specific["permissionDecisionReason"] = reason
            if modified := pre_result.get("modified_input"):
                specific["updatedInput"] = modified
            if len(specific) > 1:
                output["hookSpecificOutput"] = specific
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


MANAGED_SDK_OPTIONS = frozenset({
    "cwd",
    "allowed_tools",
    "disallowed_tools",
    "system_prompt",
    "include_builtin_system_prompt",
    "model",
    "max_turns",
    "max_budget_usd",
    "effort",
    "permission_mode",
    "env",
    "agents",
    "add_dirs",
    "tools",
    "fallback_model",
    "prompt_suggestions",
    "enable_tool_search",
    "on_permission",
    "on_user_question",
    "on_elicitation",
    "output_schema",
    "mcp_servers",
    "hooks",
    "setting_sources",
    "chrome",
    "session",
    "stderr",
    "allow_dangerously_skip_permissions",
})
"""``ClaudeAgentOptions`` fields agentpool sets itself (not passed through ``sdk_options``)."""


def claude_sdk_options(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Pass-through ``ClaudeAgentOptions`` fields, converted to the SDK's types.

    Raises:
        ValueError: An unknown or agentpool-managed field, or a value of the wrong type
    """
    if not raw:
        return {}
    import importlib

    from clawd_code_sdk import ClaudeAgentOptions

    from agentpool.agents.backend_options import check_backend_options, dataclass_field_types

    # The options module names its field types only for type checking: resolve them here
    namespace: dict[str, Any] = dict(vars(importlib.import_module("clawd_code_sdk.models")))
    for module in ("agents", "base", "hooks", "mcp", "permissions", "settings", "thinking"):
        namespace |= vars(importlib.import_module(f"clawd_code_sdk.models.{module}"))
    namespace |= vars(importlib.import_module("clawd_code_sdk.models.options"))
    fields = dataclass_field_types(ClaudeAgentOptions, namespace)
    return check_backend_options(
        raw, fields, managed=MANAGED_SDK_OPTIONS, backend="Claude Code SDK"
    )
