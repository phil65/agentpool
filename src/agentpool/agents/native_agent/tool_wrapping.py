"""Tool wrapping utilities for pydantic-ai integration."""

from __future__ import annotations

from dataclasses import replace
from functools import wraps
import time
from typing import TYPE_CHECKING, Any

from pydantic_ai import RunContext, ToolApproved, ToolDenied
from pydantic_ai.capabilities import HandleDeferredToolCalls
from pydantic_ai.exceptions import ApprovalRequired
from pydantic_ai.messages import ToolReturn
from pydantic_ai.tools import DeferredToolResults

from agentpool.agents.context import AgentContext
from agentpool.tools.base import ToolResult
from agentpool.tools.decisions import Approve, Deny
from agentpool.utils.inspection import execute, get_argument_key
from agentpool.utils.pydantic_ai_helpers import safe_args_as_dict
from agentpool.utils.signatures import (
    create_modified_signature,
    get_forwardref_signature,
    update_signature,
)


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from pydantic_ai.tools import DeferredToolApprovalResult, DeferredToolRequests

    from agentpool.agents.native_agent.hook_manager import NativeAgentHookManager
    from agentpool.hooks import HookResult
    from agentpool.tools.base import Tool


HOOK_INPUT_KEY = "hook_input"
"""Approval metadata key of the pre-tool hooks' input changes (applied once approved)."""

RUN_CTX = "_run_ctx"
"""Name of the RunContext parameter pydantic-ai sees on a wrapped tool that takes none."""


def _inject_additional_context(result: Any, additional: str) -> ToolReturn:
    """Inject additional context into a tool result.

    Wraps or modifies the result to include additional context that will
    be visible to the model after the tool execution.

    The additional context is expected to already be wrapped in XML tags
    by the PromptInjectionManager.

    Args:
        result: Original tool result (any type or ToolReturn)
        additional: Additional context to inject (already XML-wrapped)

    Returns:
        ToolReturn with the additional context appended to content
    """
    if isinstance(result, ToolReturn):
        # Append to existing content
        existing = result.content
        if existing is None:
            return replace(result, content=additional)
        if isinstance(existing, str):
            return replace(result, content=f"{existing}\n\n{additional}")
        # Sequence of UserContent - append as string
        return replace(result, content=[*existing, f"\n\n{additional}"])
    # Wrap in ToolReturn to add content
    return ToolReturn(return_value=result, content=additional)


def wrap_tool[TReturn](
    tool: Tool[TReturn],
    agent_ctx: AgentContext,
    hooks: NativeAgentHookManager | None = None,
) -> Callable[..., Awaitable[TReturn | ToolReturn | ToolDenied]]:
    """Wrap a tool for pydantic-ai: approval, AgentContext injection and hooks.

    - pydantic-ai sees a RunContext first (injected if the tool takes none) and never the
      AgentContext, which is injected per call.
    - A call that needs the user's decision raises ``ApprovalRequired`` and is answered by
      :func:`approval_handler`; pydantic-ai runs it again once approved.
    - A pre-tool hook's denial is returned as ``ToolDenied``: the model is told and goes on.

    Args:
        tool: The tool to wrap.
        agent_ctx: Agent context for confirmation handling and dependency injection.
        hooks: Optional AgentHooks for pre/post tool execution hooks.
    """
    fn = tool.get_callable()
    run_ctx_key = get_argument_key(fn, RunContext)
    agent_ctx_key = get_argument_key(fn, AgentContext)

    # Validate parameter order if RunContext is present
    if run_ctx_key:
        param_names = list(get_forwardref_signature(fn).parameters.keys())
        run_ctx_index = param_names.index(run_ctx_key)
        if run_ctx_index != 0:
            msg = f"Tool {tool.name!r}: RunContext param {run_ctx_key!r} must come first."
            raise ValueError(msg)

    async def _execute_with_hooks(
        execute_fn: Callable[..., Awaitable[TReturn]],
        tool_input: dict[str, Any],
        *args: Any,
        **kwargs: Any,
    ) -> TReturn | ToolReturn:
        """Execute the tool, then run the post-tool hooks."""
        start_time = time.perf_counter()
        result: TReturn | ToolResult | ToolReturn = await execute_fn(*args, **kwargs)
        duration_ms = (time.perf_counter() - start_time) * 1000
        # Convert AgentPool ToolResult to pydantic-ai ToolReturn
        if isinstance(result, ToolResult):
            result = result.to_pydantic_ai()
        if hooks:
            post_result = await hooks.run_post_tool_hooks(
                agent_name=agent_ctx.node_name,
                tool_name=tool.name,
                tool_input=tool_input,
                tool_output=result,
                duration_ms=duration_ms,
                session_id=agent_ctx.turn_session_id,
                env=agent_ctx.agent.env,
            )
            # Inject additional context if provided by hooks
            if additional := post_result.get("additional_context"):
                result = _inject_additional_context(result, additional)
        return result

    async def wrapped(
        ctx: RunContext, *args: Any, **kwargs: Any
    ) -> TReturn | ToolReturn | ToolDenied:
        if (denied := await _gate_call(tool, agent_ctx, hooks, ctx, kwargs)) is not None:
            return denied
        if agent_ctx.data is None:
            agent_ctx.data = ctx.deps
        if agent_ctx_key:  # Inject a per-call AgentContext (avoids races between calls)
            model_name = f"{ctx.model.system}:{ctx.model.model_name}" if ctx.model else None
            kwargs[agent_ctx_key] = replace(
                agent_ctx,
                tool_name=ctx.tool_name,
                tool_call_id=ctx.tool_call_id,
                tool_input=kwargs.copy(),
                model_name=model_name,
            )
        tool_input = {k: v for k, v in kwargs.items() if k != agent_ctx_key}
        if run_ctx_key:  # The function takes the RunContext (as its first parameter)
            return await _execute_with_hooks(
                lambda *a, **kw: execute(fn, ctx, *a, **kw), tool_input, *args, **kwargs
            )
        return await _execute_with_hooks(
            lambda *a, **kw: execute(fn, *a, **kw), tool_input, *args, **kwargs
        )

    # Apply wraps first
    wraps(fn)(wrapped)
    wrapped.__doc__ = tool.description
    wrapped.__name__ = tool.name
    # Signature for pydantic-ai (after wraps, which would overwrite it): a RunContext first
    # (the wrapper needs it), no AgentContext. The RunContext parameter is re-added: its
    # annotation may name RunContext only under TYPE_CHECKING (unresolvable names become
    # Any). update_signature also replaces the annotations wraps copied (`__annotate__`),
    # which would raise NameError for such names.
    remove = [key for key in (agent_ctx_key, run_ctx_key) if key]
    new_sig = create_modified_signature(
        fn, remove=remove, inject={run_ctx_key or RUN_CTX: RunContext}
    )
    update_signature(wrapped, new_sig)
    return wrapped


async def _gate_call(
    tool: Tool,
    agent_ctx: AgentContext,
    hooks: NativeAgentHookManager | None,
    ctx: RunContext,
    kwargs: dict[str, Any],
) -> ToolDenied | None:
    """Pre-tool hooks and the user's decision for a call, before it runs.

    The hooks run first, once: a denial is returned (told to the model), ``"ask"`` asks the
    user even for a tool that needs no confirmation. Asking defers the call through
    pydantic-ai (``ApprovalRequired``, answered by :func:`approval_handler`); the approved
    call comes back (``tool_call_approved``) with the hooks' input changes kept in the
    approval. ``kwargs`` get those changes.

    Raises:
        ApprovalRequired: The call waits for the user's decision
    """
    if ctx.tool_call_approved:
        approval = ctx.tool_call_metadata
        if isinstance(approval, dict) and isinstance(kept := approval.get(HOOK_INPUT_KEY), dict):
            kwargs.update(kept)
        return None
    pre_result: HookResult = {}
    if hooks:
        pre_result = await hooks.run_pre_tool_hooks(
            agent_name=agent_ctx.node_name,
            tool_name=tool.name,
            tool_input=dict(kwargs),
            session_id=agent_ctx.turn_session_id,
            env=agent_ctx.agent.env,
        )
    modified = pre_result.get("modified_input") or {}
    match pre_result.get("decision"):
        case "deny":
            reason = pre_result.get("reason", "Blocked by pre-tool hook")
            return ToolDenied(f"Tool {tool.name!r} was blocked: {reason}")
        case "ask":
            raise ApprovalRequired(metadata={HOOK_INPUT_KEY: modified})
    if needs_approval(tool, agent_ctx):
        raise ApprovalRequired(metadata={HOOK_INPUT_KEY: modified})
    kwargs.update(modified)
    return None


def needs_approval(tool: Tool, agent_ctx: AgentContext) -> bool:
    """Whether a call of ``tool`` waits for the user's decision (the agent's confirmation mode)."""
    match agent_ctx.native_agent.tool_confirmation_mode:
        case "always":
            return True
        case "never":
            return False
        case "per_tool":
            return tool.requires_confirmation


def approval_handler(agent_ctx: AgentContext, tools: Mapping[str, Tool]) -> HandleDeferredToolCalls:
    """The capability that asks the user about the tool calls awaiting approval.

    An approved call runs (with the user's arguments, if edited); a denied one is reported
    to the model as ``outcome="denied"`` and the turn goes on. A denial that stops the turn
    denies the batch's remaining calls unasked and interrupts the turn once answered.

    Args:
        agent_ctx: The run's agent context
        tools: The run's tools by the name the model calls them
    """

    async def decide(ctx: RunContext[Any], requests: DeferredToolRequests) -> DeferredToolResults:
        decisions: dict[str, bool | DeferredToolApprovalResult] = {}
        metadata: dict[str, dict[str, Any]] = {}
        stopped = False
        for call in requests.approvals:
            if stopped:
                decisions[call.tool_call_id] = ToolDenied(Deny(stop=True).reason(call.tool_name))
                continue
            hook_input = requests.metadata.get(call.tool_call_id, {}).get(HOOK_INPUT_KEY) or {}
            call_ctx = replace(
                agent_ctx,
                tool_name=call.tool_name,
                tool_call_id=call.tool_call_id,
                tool_input=safe_args_as_dict(call) | hook_input,  # What would run
            )
            tool = tools.get(call.tool_name)
            match await call_ctx.decide_tool(tool.description or "" if tool else ""):
                case Approve(args=None):
                    decisions[call.tool_call_id] = ToolApproved()
                    metadata[call.tool_call_id] = {HOOK_INPUT_KEY: hook_input}
                case Approve(args=args):  # The user's arguments, as they edited them
                    decisions[call.tool_call_id] = ToolApproved(override_args=args)
                case Deny() as denial:
                    decisions[call.tool_call_id] = ToolDenied(denial.reason(call.tool_name))
                    stopped = denial.stop
        if stopped:
            agent_ctx.stop_turn()
        return DeferredToolResults(approvals=decisions, metadata=metadata)

    return HandleDeferredToolCalls(handler=decide)
