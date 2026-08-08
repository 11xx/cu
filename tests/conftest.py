"""Test-wide isolation from the session `cu` is actually running in.

`cu` keeps its live state — frames, the abort flag, the status a status bar
reads — under `XDG_RUNTIME_DIR`. A test that exercises a command through
`cli.main` writes there like any other caller, so without this the suite
publishes into the desktop it is being run on: the indicator claims an agent is
driving because pytest said so.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "runtime"))
    (tmp_path / "runtime").mkdir(parents=True, exist_ok=True)
