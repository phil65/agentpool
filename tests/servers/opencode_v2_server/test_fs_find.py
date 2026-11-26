"""``GET /api/fs/find`` walks and scores off the event loop (it runs per keystroke)."""

from __future__ import annotations

import json
import threading
from typing import TYPE_CHECKING

from agentpool_server.opencode_v2_server.routes import files


if TYPE_CHECKING:
    from pathlib import Path

    import pytest


async def test_find_ranks_files_off_the_event_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "alpha.py").write_text("")
    (tmp_path / "beta.txt").write_text("")
    score = files._fuzzy_score
    threads: set[int] = set()

    def recording(query: str, candidate: str) -> int | None:
        threads.add(threading.get_ident())
        return score(query, candidate)

    monkeypatch.setattr(files, "_fuzzy_score", recording)
    response = await files.fs_find(directory=str(tmp_path), query="alp", entry_type=None)
    paths = [entry["path"] for entry in json.loads(response.body)["data"]]
    assert paths[0] == str(tmp_path / "src" / "alpha.py")
    assert threads
    assert threading.get_ident() not in threads
