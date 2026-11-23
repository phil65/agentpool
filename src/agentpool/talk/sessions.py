"""The sessions routed messages and triggered events run in (session policies)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentpool.sessions import Session


if TYPE_CHECKING:
    from agentpool.agents.base_agent import BaseAgent
    from agentpool.common_types import PromptCompatible
    from agentpool.messaging import ChatMessage, MessageNode


class SessionPicker:
    """Opens the sessions of an agent that a router's connections and triggers run in.

    In a pool they live in its session registry (kept, persisted, resumed); outside one,
    here, for as long as the router lives.
    """

    def __init__(self) -> None:
        self._local: dict[str, Session] = {}

    async def open(
        self, agent: BaseAgent[Any, Any], session_id: str, *, parent_id: str | None
    ) -> Session:
        """The session ``session_id`` of ``agent``: the existing one, else a new one."""
        if (pool := agent.agent_pool) is not None:
            parent = pool.sessions.get(parent_id) if parent_id is not None else None
            return await pool.sessions.open(
                session_id,
                agent.name,
                parent_id=parent_id,
                cwd=parent.cwd if parent is not None else None,
                agent_type=agent.AGENT_TYPE,
            )
        if (session := self._local.get(session_id)) is None:
            session = Session.create(
                agent.name,
                session_id=session_id,
                parent_id=parent_id,
                agent_type=agent.AGENT_TYPE,
                storage=agent.storage,
            )
            self._local[session_id] = session
        return session

    def new(self, agent: BaseAgent[Any, Any], *, parent_id: str | None) -> Session:
        """A new session of ``agent``."""
        if (pool := agent.agent_pool) is not None:
            return pool.sessions.create(
                agent.name, parent_id=parent_id, agent_type=agent.AGENT_TYPE
            )
        return Session.create(
            agent.name, parent_id=parent_id, agent_type=agent.AGENT_TYPE, storage=agent.storage
        )


async def run_in(
    node: MessageNode[Any, Any],
    prompt: PromptCompatible,
    session: Session,
    *,
    parent_id: str | None = None,
) -> ChatMessage[Any]:
    """Run ``prompt`` on agent ``node`` in ``session``.

    An agent with one native session per instance takes its turn lock for another session
    (``BaseAgent.session_turn``), as any caller running one of its sessions does.
    """
    from agentpool.agents.base_agent import BaseAgent

    if not isinstance(node, BaseAgent):
        raise TypeError(f"{node.name}: only agents run in a session picked for them")
    async with node.session_turn():
        return await node.run(prompt, session=session, parent_id=parent_id)


def check_policy_target(node: MessageNode[Any, Any], policy: str, *, what: str) -> None:
    """Session policies other than ``target`` pick sessions of agents, not of teams."""
    from agentpool.agents.base_agent import BaseAgent

    if policy != "target" and not isinstance(node, BaseAgent):
        msg = (
            f"{what}: session {policy!r} needs an agent, but {node.name!r} is a team "
            "(a team runs its members in sessions of its own); use 'target'"
        )
        raise ValueError(msg)
