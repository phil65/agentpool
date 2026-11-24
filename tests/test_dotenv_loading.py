"""Only the CLI loads .env files; importing agentpool as a library must not."""

from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    import pytest


_PROBE = """
import sys
import dotenv, dotenv.main

callers = []

def spy(*args, **kwargs):
    callers.append(sys._getframe(1).f_globals.get("__name__", "?"))
    return False

dotenv.load_dotenv = dotenv.main.load_dotenv = spy
import agentpool, agentpool_server, agentpool_storage, agentpool_config, agentpool_toolsets
print(sorted({c for c in callers if c.split(".")[0].startswith("agentpool")}))
"""


def test_importing_agentpool_does_not_load_dotenv() -> None:
    """Agentpool's own modules don't call load_dotenv on import (dependencies aside)."""
    result = subprocess.run(
        [sys.executable, "-c", _PROBE], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "[]"


def test_the_cli_callback_loads_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    import dotenv

    from agentpool_cli.__main__ import main

    calls: list[bool] = []
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: calls.append(True))
    ctx = SimpleNamespace(obj={}, ensure_object=lambda _type: None)
    main(ctx, version=False, log_level=SimpleNamespace(upper=lambda: "WARNING"))  # type: ignore[arg-type]
    assert calls == [True]
