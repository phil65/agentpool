"""Where a V2 session's turns run, and what the agent should know about the session.

A V2 session names its own directory, and the pool's agents serve every session: its turns
run in an execution environment rooted there (with the session's environment variables),
made current while the turn runs (``agentpool.sessions.environment``). Its instruction
entries and activated skills are its instructions: agents that take session instructions
(native ones) get the current set with every run, as system instructions.
"""

from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import TYPE_CHECKING

from exxec import LocalExecutionEnvironment

from opencode_v2_sdk.models import SessionMessageSkill


if TYPE_CHECKING:
    from exxec import ExecutionEnvironment

    from agentpool.agents.base_agent import BaseAgent
    from agentpool_server.opencode_v2_server.projector import SessionProjection


def session_instructions(projection: SessionProjection) -> str:
    """The session's instructions: its instruction entries, then its activated skills."""
    parts: list[str] = []
    if projection.instructions:
        entries = "\n".join(f"- {key}: {value}" for key, value in projection.instructions.items())
        parts.append(f"Session instructions:\n{entries}")
    skills: dict[str, SessionMessageSkill] = {}
    for message in projection.messages:  # The latest activation of each skill
        if isinstance(message, SessionMessageSkill):
            skills.pop(message.skill, None)
            skills[message.skill] = message
    parts.extend(
        f"<skill name={skill.name!r}>\n{skill.text}\n</skill>" for skill in skills.values()
    )
    return "\n\n".join(parts)


def _directory_of(env: ExecutionEnvironment) -> str:
    return os.path.normpath(env.cwd or Path.cwd())


@dataclass
class SessionEnvironments:
    """The execution environments of a session's turns, by agent (created on first use)."""

    _envs: dict[str, LocalExecutionEnvironment] = field(default_factory=dict)

    async def get(
        self, agent: BaseAgent, directory: str, env_vars: dict[str, str]
    ) -> ExecutionEnvironment:
        """Where ``agent`` runs the session's turns: in ``directory``, with ``env_vars``.

        The agent's own environment when it is there already. Agents with a non-local one
        (a container, a remote host) keep it: the session's directory is this host's.
        """
        own = agent.env
        if not isinstance(own, LocalExecutionEnvironment):
            return own
        directory = os.path.normpath(directory)
        if _directory_of(own) == directory and not env_vars:
            return own
        wanted = {**own.env_vars, **env_vars}
        cached = self._envs.get(agent.name)
        if cached is not None and _directory_of(cached) == directory and cached.env_vars == wanted:
            return cached
        if cached is not None:
            await cached.__aexit__(None, None, None)
        # Like the agent's own, rooted in the session's directory (its tool server and
        # dependencies stay the agent's)
        env = LocalExecutionEnvironment(
            default_command_timeout=own.default_command_timeout,
            isolated=own.isolated,
            executable=own.executable,
            language=own.language,
            root_path=own.root_path,
            cwd=directory,
            env_vars=wanted,
            inherit_env=own.inherit_env,
        )
        await env.__aenter__()
        self._envs[agent.name] = env
        return env

    async def close(self) -> None:
        """Close the environments created for the session."""
        envs, self._envs = list(self._envs.values()), {}
        for env in envs:
            await env.__aexit__(None, None, None)
