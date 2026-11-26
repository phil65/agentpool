"""SQL storage keeps a message's metadata (an answer's turn), also for databases from before."""

import sqlite3
from typing import TYPE_CHECKING

from agentpool.messaging import ChatMessage
from agentpool_config.storage import SQLStorageConfig
from agentpool_storage.sql_provider import SQLModelProvider, utils


if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _config(path: Path) -> SQLStorageConfig:
    return SQLStorageConfig(url=f"sqlite+aiosqlite:///{path}")


async def _round_trip(path: Path) -> dict[str, object]:
    turn = {"outcome": "completed", "parent": None}
    message = ChatMessage[str](
        content="done", role="assistant", session_id="s1", metadata={"turn": turn}
    )
    async with SQLModelProvider(_config(path)) as provider:
        await provider.log_message(message=message)
        [stored] = await provider.get_session_messages("s1")
    return dict(stored.metadata)


async def test_metadata_round_trips_on_a_new_database(tmp_path: Path) -> None:
    metadata = await _round_trip(tmp_path / "new.db")
    assert metadata == {"turn": {"outcome": "completed", "parent": None}}


async def test_a_database_from_before_gets_the_column(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "old.db"
    async with SQLModelProvider(_config(path)):
        pass  # Created at the current schema: now take it back to the previous one
    with sqlite3.connect(path) as conn:
        conn.execute("ALTER TABLE message DROP COLUMN metadata_json")
        conn.execute("UPDATE alembic_version SET version_num = 'b2c3d4e5f6a7'")
    monkeypatch.setattr(utils, "_migrated_databases", set())  # As a new process would
    metadata = await _round_trip(path)  # Opening it migrates it
    assert metadata == {"turn": {"outcome": "completed", "parent": None}}
    with sqlite3.connect(path) as conn:
        [(version,)] = conn.execute("SELECT version_num FROM alembic_version").fetchall()
    assert version == "c3d4e5f6a7b8"


async def test_migrating_twice_is_harmless(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "twice.db"
    async with SQLModelProvider(_config(path)):
        pass
    with sqlite3.connect(path) as conn:  # At the target already, but marked as before
        conn.execute("UPDATE alembic_version SET version_num = 'b2c3d4e5f6a7'")
    monkeypatch.setattr(utils, "_migrated_databases", set())
    assert await _round_trip(path) == {"turn": {"outcome": "completed", "parent": None}}
