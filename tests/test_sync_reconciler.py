"""AgentReconciler must never return the no-changes sentinel as file content."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from agentpool_sync.handlers import AgentReconciler
from agentpool_sync.models import FileChange, SyncMetadata


@dataclass
class _Result:
    data: Any


class _FakeAgent:
    def __init__(self, answer: Any) -> None:
        self.answer = answer

    async def run(self, prompt: str) -> _Result:
        return _Result(self.answer)


def _change() -> FileChange:
    meta = SyncMetadata(agent="sync")
    return FileChange(path="doc.md", metadata=meta, changed_deps=["src/a.py"], diff="+x")


@pytest.mark.parametrize(
    "answer",
    [
        "NO_CHANGES_NEEDED",
        "NO_CHANGES_NEEDED\n",
        "  NO_CHANGES_NEEDED.  ",
        "`NO_CHANGES_NEEDED`",
        "```\nNO_CHANGES_NEEDED\n```",
        "```text\nNO_CHANGES_NEEDED\n```",
        "",
        None,
    ],
)
async def test_sentinel_means_no_changes(answer: Any):
    reconciler = AgentReconciler(lambda _: _FakeAgent(answer), include_file_content=False)  # type: ignore[arg-type,return-value]
    assert await reconciler(_change()) is None


async def test_real_content_is_returned():
    content = "# Doc\n\nUpdated, NO_CHANGES_NEEDED was not the answer.\n"
    reconciler = AgentReconciler(lambda _: _FakeAgent(content), include_file_content=False)  # type: ignore[arg-type,return-value]
    assert await reconciler(_change()) == content
