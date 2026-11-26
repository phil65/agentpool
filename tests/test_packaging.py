"""The built wheel ships every top-level package of ``src`` (except the unshipped ones)."""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import zipfile

import pytest


ROOT = Path(__file__).parent.parent
NOT_SHIPPED = {"agentpool_sync"}
"""Packages in ``src`` that are deliberately not in the wheel.

``agentpool_sync`` is an unfinished experiment: nothing in ``src`` imports it, no config
or CLI entry reaches it, and it has no docs.
"""


@pytest.mark.skipif(shutil.which("uv") is None, reason="needs uv")
def test_wheel_contains_all_packages(tmp_path: Path) -> None:
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(tmp_path), "-q"],
        cwd=ROOT,
        check=True,
    )
    [wheel] = tmp_path.glob("*.whl")
    with zipfile.ZipFile(wheel) as zf:
        top_level = {name.split("/")[0] for name in zf.namelist()}
    shipped = {name for name in top_level if not name.endswith(".dist-info")}
    in_src = {p.parent.name for p in (ROOT / "src").glob("*/__init__.py")}
    assert shipped == in_src - NOT_SHIPPED
