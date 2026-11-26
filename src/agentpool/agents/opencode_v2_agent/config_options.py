"""Config options of the OpenCode V2 agent."""

from typing import TYPE_CHECKING, Any, cast

from agentpool.agents.modes import ConfigOption, ModeInfo, ModelOption


if TYPE_CHECKING:
    from agentpool.agents.opencode_v2_agent.opencode_v2_agent import OpenCodeV2Agent
    from agentpool.models.opencode_v2_agents import OpenCodePermissionMode

    type AnyOpenCodeV2Agent = OpenCodeV2Agent[Any]


PERMISSION_MODES: list[ModeInfo] = [
    ModeInfo(
        value="allow",
        name="Allow",
        description="Auto-approve tool permissions",
        category_id="mode",
    ),
    ModeInfo(
        value="ask",
        name="Ask",
        description="Request confirmation through AgentPool's input provider",
        category_id="mode",
    ),
    ModeInfo(
        value="deny",
        name="Deny",
        description="Reject tool permission requests",
        category_id="mode",
    ),
]


class PermissionModeOption(ConfigOption["AnyOpenCodeV2Agent"]):
    """Tool permission handling mode."""

    id = "mode"
    name = "Permissions"
    category = "mode"

    async def choices(self, agent: AnyOpenCodeV2Agent) -> list[ModeInfo]:
        return PERMISSION_MODES

    def current(self, agent: AnyOpenCodeV2Agent) -> str:
        return agent._permission_mode

    async def _apply(self, agent: AnyOpenCodeV2Agent, value: str) -> None:
        agent._permission_mode = cast("OpenCodePermissionMode", value)
        await agent._sync_session_permissions()


class OpenCodeAgentOption(ConfigOption["AnyOpenCodeV2Agent"]):
    """Active OpenCode agent on the session (when the server exposes multiple agents)."""

    id = "agent"
    name = "Agent"
    category = "other"

    async def choices(self, agent: AnyOpenCodeV2Agent) -> list[ModeInfo]:
        agents = await agent._fetch_server_agents()
        return [
            ModeInfo(
                value=info.id,
                name=info.name or info.id,
                description=info.description or "",
                category_id="agent",
            )
            for info in agents
            if not info.hidden and info.mode != "subagent"
        ]

    def current(self, agent: AnyOpenCodeV2Agent) -> str:
        return agent._opencode_agent or ""

    async def _apply(self, agent: AnyOpenCodeV2Agent, value: str) -> None:
        agent._opencode_agent = value
        if agent._client is not None and agent._sdk_session_id is not None:
            await agent._client.switch_agent(agent._sdk_session_id, value)


class VariantOption(ConfigOption["AnyOpenCodeV2Agent"]):
    """Model variant (e.g. reasoning effort) for the currently selected model."""

    id = "thought_level"
    name = "Variant"
    category = "thought_level"

    async def choices(self, agent: AnyOpenCodeV2Agent) -> list[ModeInfo]:
        variants = await agent._current_model_variants()
        if not variants:
            return []
        return [
            ModeInfo(
                value=v,
                name=v.replace("-", " ").title(),
                description="",
                category_id="thought_level",
            )
            for v in variants
        ]

    def current(self, agent: AnyOpenCodeV2Agent) -> str:
        return agent._variant or ""

    async def _apply(self, agent: AnyOpenCodeV2Agent, value: str) -> None:
        agent._variant = value
        await agent._sync_session_model()


class OpenCodeModelOption(ModelOption["AnyOpenCodeV2Agent"]):
    """Model selection as ``provider/model``."""

    async def _apply(self, agent: AnyOpenCodeV2Agent, value: str) -> None:
        agent._model = value
        await agent._sync_session_model()


CONFIG_OPTIONS: tuple[ConfigOption[AnyOpenCodeV2Agent], ...] = (
    PermissionModeOption(),
    OpenCodeAgentOption(),
    VariantOption(),
    OpenCodeModelOption(),
)
"""Options of every OpenCode V2 agent, in display order."""
