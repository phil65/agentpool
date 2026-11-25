"""Security regressions for the diagnostics runner (issue #45.1)."""

from __future__ import annotations

import os
import shlex
import sys
from typing import TYPE_CHECKING

from exxec.local_provider import LocalExecutionEnvironment
import pytest

from agentpool_toolsets.builtin.code import CodeTools
from agentpool_toolsets.fsspec_toolset.diagnostics import DiagnosticsConfig, DiagnosticsManager


if TYPE_CHECKING:
    from pathlib import Path


def _ty_server(mgr: DiagnosticsManager, name: str):
    return next(s for s in mgr.get_servers_for_file(name) if s.id == "ty")


def test_build_command_quotes_file_names():
    mgr = DiagnosticsManager(LocalExecutionEnvironment(), config=DiagnosticsConfig())
    names = ["a.py;touch PWNED;#.py", "my file.py", "$(id).py"]
    server = _ty_server(mgr, names[0])
    command = server.build_command(names)
    assert shlex.split(command) == ["ty", "check", "--output-format", "gitlab", *names]
    assert server.build_argv(names)[-3:] == names


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell injection")
async def test_crafted_file_name_does_not_run_shell_command(tmp_path: Path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "ty"
    # Fake checker: records its argv, one argument per line
    fake.write_text(f'#!/bin/sh\nfor a in "$@"; do echo "$a"; done > {tmp_path}/argv.txt\n')
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    evil = "a.py;touch PWNED;#.py"
    (tmp_path / evil).write_text("x = 1\n")
    env = LocalExecutionEnvironment(cwd=str(tmp_path))
    async with env:
        mgr = DiagnosticsManager(env, config=DiagnosticsConfig(preferred_servers=["ty"]))
        await mgr.run_for_file(evil)
    assert not (tmp_path / "PWNED").exists()
    argv = (tmp_path / "argv.txt").read_text().splitlines()
    assert argv[-1] == evil


async def test_run_diagnostics_requires_confirmation():
    """run_diagnostics executes external programs, so it is an execute tool."""
    tools = {t.name: t for t in await CodeTools().get_tools()}
    tool = tools["run_diagnostics"]
    assert tool.category == "execute"
    assert tool.requires_confirmation
