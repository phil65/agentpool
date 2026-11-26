"""YAML connections set what ``connect_to`` takes: ``parallel`` and ``stats_history``."""

from typing import TYPE_CHECKING

from agentpool import AgentPool, AgentsManifest
from agentpool.talk.stats import TALK_STATS_HISTORY
from agentpool_config.forward_targets import NodeConnectionConfig


if TYPE_CHECKING:
    from pathlib import Path


CONFIG = """\
agents:
    source:
        type: native
        model: test
        connections:
            - type: node
              name: one
              parallel: true
              stats_history: 2
            - type: node
              name: two
              parallel: true
              stats_history: null
            - type: node
              name: three
    one:
        type: native
        model: test
    two:
        type: native
        model: test
    three:
        type: native
        model: test
"""


async def test_parallel_and_stats_history_from_yaml(tmp_path: Path) -> None:
    config = tmp_path / "agents.yml"
    config.write_text(CONFIG)
    async with AgentPool(AgentsManifest.from_file(config)) as pool:
        connections = pool.get_agent("source").connections.get_connections()
        talks = {talk.targets[0].name: talk for talk in connections}
        assert talks["one"].parallel
        assert talks["two"].parallel
        assert not talks["three"].parallel
        assert talks["one"].stats.messages.maxlen == 2
        assert talks["two"].stats.messages.maxlen is None
        assert talks["three"].stats.messages.maxlen == TALK_STATS_HISTORY


def test_default_stats_history_is_the_talks() -> None:
    assert NodeConnectionConfig(name="x").stats_history == TALK_STATS_HISTORY


async def test_connect_to_takes_stats_history() -> None:
    from agentpool import Agent

    source, target = Agent(name="s", model="test"), Agent(name="t", model="test")
    talk = source.connect_to(target, stats_history=3)
    assert talk.stats.messages.maxlen == 3
