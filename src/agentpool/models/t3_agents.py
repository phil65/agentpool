"""Configuration models for T3 Code agents."""

from typing import TYPE_CHECKING, Any, Literal, Self

from pydantic import ConfigDict, Field, model_validator

from agentpool_config.nodes import BaseAgentConfig


if TYPE_CHECKING:
    from collections.abc import Sequence

    from agentpool.agents.t3_agent import T3Agent
    from agentpool.common_types import AnyEventHandlerType
    from agentpool.delegation import AgentPool
    from agentpool.ui.base import InputProvider


type T3RuntimeMode = Literal["approval-required", "auto-accept-edits", "auto", "full-access"]
"""How freely the T3 thread's agent may act (T3's permission modes)."""

type T3InteractionMode = Literal["default", "plan"]


class T3AgentConfig(BaseAgentConfig):
    """An agent that runs as a thread in a T3 Code environment.

    T3 Code (https://github.com/pingdotgg/t3code) drives coding agents (Claude Code,
    Codex, Cursor, Grok, OpenCode, Kiro, ACP agents, ...) on the machine it runs on,
    with worktrees and checkpoints. This agent hands its prompts to a thread there,
    through the environment's MCP endpoint (via ``pyt3client``).

    Sign in once with ``pyt3 login <url> <pairing-code> --name <alias>`` and use the
    alias as ``login``, or give ``url`` and ``token``. With a native sign-in as well
    (``pyt3 login-native <url> <pairing-token>``, or ``native_token``), replies stream
    in as they are written and approvals go through agentpool's input provider.

    Example:
        ```yaml
        agents:
          cursor_on_homelab:
            type: t3
            login: homelab
            project: /home/me/src/app
            provider: cursor
            model: composer-2
            runtime_mode: auto
        ```
    """

    model_config = ConfigDict(
        json_schema_extra={"title": "T3 Code Agent Configuration", "x-icon": "mdi:server-network"}
    )

    type: Literal["t3"] = Field(default="t3", init=False)
    """Top-level discriminator for agent type."""

    login: str | None = Field(default=None, examples=["homelab"])
    """Alias of a sign-in saved by ``pyt3 login``."""

    url: str | None = Field(default=None, examples=["https://t3.example.ts.net"])
    """Environment origin; with ``token`` instead of ``login``."""

    token: str | None = Field(default=None, examples=["${T3_TOKEN}"])
    """MCP bearer token for ``url`` (``${VAR}`` references are expanded)."""

    project: str = Field(examples=["/home/me/src/app", "proj_123"])
    """Project the threads run in: its id, or its folder on the T3 host."""

    provider: str | None = Field(default=None, examples=["claudeAgent", "codex", "cursor"])
    """Provider instance; the project's default when omitted (needs ``model``)."""

    model: str | None = Field(default=None, examples=["claude-sonnet-5-5"])
    """Model of the provider; the project's default when omitted."""

    options: dict[str, str | bool] = Field(default_factory=dict, examples=[{"effort": "high"}])
    """Model options, as ``orchestrator_capabilities`` lists them."""

    runtime_mode: T3RuntimeMode = "auto"
    """Permission mode of the thread. Approvals cannot be given through this agent
    (T3's MCP endpoint answers questions only), so ``approval-required`` turns wait
    until someone approves in T3 Code."""

    interaction_mode: T3InteractionMode = "default"

    worktree: str | None = Field(default=None, examples=["origin/main"])
    """Run each new thread in a new worktree from this base ref."""

    poll_interval: float = Field(default=0.5, gt=0, le=10)
    """Seconds between reads of a running turn's progress, without live updates."""

    native: bool | None = None
    """Use T3's native socket for live updates and approvals: by default when a native
    sign-in exists; ``true`` requires one, ``false`` never uses it."""

    native_token: str | None = Field(default=None, examples=["${T3_NATIVE_TOKEN}"])
    """Native bearer for ``url`` (``${VAR}`` references are expanded). ``/mcp`` and the
    native socket each refuse the other's token, so this is a second sign-in."""

    @model_validator(mode="after")
    def _one_way_to_sign_in(self) -> Self:
        if self.login is None and (self.url is None or self.token is None):
            msg = "a T3 agent needs `login`, or both `url` and `token`"
            raise ValueError(msg)
        if self.model is not None and self.provider is None:
            msg = "`model` needs `provider`"
            raise ValueError(msg)
        return self

    def get_token(self) -> str | None:
        """The token, with ``${VAR}`` references resolved."""
        from agentpool.utils.env_refs import expand_env_refs

        return None if self.token is None else expand_env_refs({"token": self.token})["token"]

    def get_native_token(self) -> str | None:
        """The native token, with ``${VAR}`` references resolved."""
        from agentpool.utils.env_refs import expand_env_refs

        if self.native_token is None:
            return None
        return expand_env_refs({"token": self.native_token})["token"]

    def get_agent[TDeps](
        self,
        event_handlers: Sequence[AnyEventHandlerType] | None = None,
        input_provider: InputProvider | None = None,
        pool: AgentPool[Any] | None = None,
        deps_type: type[TDeps] | None = None,  # type: ignore[valid-type]
    ) -> T3Agent[TDeps]:
        from agentpool.agents.t3_agent import T3Agent

        return T3Agent[TDeps].from_config(
            self,
            event_handlers=event_handlers,
            input_provider=input_provider,
            agent_pool=pool,
            deps_type=deps_type,
        )
