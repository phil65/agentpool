"""What an agent is, apart from the instance running it: its resolved config, versioned.

The pool builds each agent of the manifest from its config; the agent keeps the
:class:`AgentDefinition` it was built from (``BaseAgent.definition``). Its ``version`` is a
content hash of the config, so sessions can record which definition they ran with
(``SessionData.definition_version``, design rule R2) and a resumed session can tell when
the agent changed since (a config reload, an edited manifest).

Agents constructed in Python (``Agent(tools=[greet])``) have no definition yet: their
callables, ``Model`` instances and types aren't part of a config, and a hash without them
would claim more than it knows (the "extras" of ``ARCHITECTURE_SESSIONS_AGENTS.md`` §9.1).
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import TYPE_CHECKING, Self


if TYPE_CHECKING:
    from agentpool_config.nodes import BaseAgentConfig


VERSION_LENGTH = 16
"""Hex digits of the content hash kept as the version."""


@dataclass(frozen=True)
class AgentDefinition:
    """An agent's resolved config and its version."""

    config: BaseAgentConfig
    """The config the agent was built from (frozen, resolved: no ``INHERIT``, no env vars)."""

    version: str
    """Content hash of the config: equal configs, equal versions."""

    @classmethod
    def from_config(cls, config: BaseAgentConfig) -> Self:
        """The definition of ``config``, versioned by its content (its name aside)."""
        content = config.model_dump_json(exclude={"name"}, round_trip=True)
        digest = hashlib.sha256(content.encode()).hexdigest()
        return cls(config=config, version=digest[:VERSION_LENGTH])

    @property
    def name(self) -> str | None:
        """The agent's name."""
        return self.config.name
