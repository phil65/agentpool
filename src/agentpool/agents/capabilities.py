"""Declared runtime capabilities for AgentPool agent implementations."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AgentCapabilities:
    """Feature flags advertising what an agent runtime supports natively.

    Allows servers, pools, and orchestration code to inspect runtime support
    explicitly instead of probing or catching ``NotImplementedError``.
    """

    native_steer: bool = False
    """Backend accepts mid-turn steering input without ending the turn (``steer()``,
    ``run_stream(mode="steer")``; native agents take it in after the running tool call)."""

    structured_output: bool = False
    """Backend natively enforces a JSON Schema / Pydantic output type."""

    multimodal_input: bool = False
    """Backend accepts non-text attachments (images, audio, files, URLs)."""

    live_model_discovery: bool = False
    """Backend queries available models dynamically at runtime."""

    native_compaction: bool = False
    """``compact()`` compacts what the model sees on demand (otherwise it raises
    ``UnsupportedOperationError``). Backends that only compact on their own don't count."""

    native_fork: bool = False
    """``fork_session()`` forks the conversation the agent continues from (otherwise it
    raises ``UnsupportedOperationError``); some agents only fork whole sessions."""

    concurrent_sessions: bool = False
    """Runs turns of several sessions at once on one instance (each on its own history and
    turn state). Otherwise ``session_turn()`` serializes them: one native session each."""

    session_instructions: bool = False
    """Takes the session's instructions (``SessionEnvironment.instructions``) itself: native
    agents as system instructions with every run; Claude Code and Codex when their backend
    session starts (a process's system prompt, a thread's developer instructions), and
    changes since with the next turn's prompt. Otherwise servers put them (or their changes)
    into prompts."""

    background_events: bool = False
    """Backend streams between-turn / unsolicited events via ``BaseAgent.emit_event``."""
