"""Declared runtime capabilities for AgentPool agent implementations."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AgentCapabilities:
    """Feature flags advertising what an agent runtime supports natively.

    Allows servers, pools, and orchestration code to inspect runtime support
    explicitly instead of probing or catching ``NotImplementedError``.
    """

    native_steer: bool = False
    """Backend natively accepts mid-turn steering input without ending the turn."""

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

    background_events: bool = False
    """Backend streams between-turn / unsolicited events via ``BaseAgent.emit_event``."""
