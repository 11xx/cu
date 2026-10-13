"""Test-wide isolation from the session `cu` is actually running in.

`cu` keeps its live state — frames, the abort flag, the status a status bar
reads — under `XDG_RUNTIME_DIR`. A test that exercises a command through
`cli.main` writes there like any other caller, so without this the suite
publishes into the desktop it is being run on: the indicator claims an agent is
driving because pytest said so.

Action logging resolves an explicit configured path independently of XDG
state directories. Tests redirect that resolver so desktop audit records
contain only actions from real sessions.
"""

from __future__ import annotations

import pytest

from cu import config


@pytest.fixture(autouse=True)
def isolated_action_log(tmp_path, monkeypatch):
    path = tmp_path / "state" / "actions.jsonl"
    monkeypatch.setattr(config, "log_path", lambda _cfg: path)


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    (tmp_path / "runtime").mkdir(parents=True, exist_ok=True)
    # A session the developer happens to have open would otherwise adopt every
    # entry the suite publishes, so the tests would agree with each other while
    # describing the wrong driver.
    monkeypatch.delenv("CU_SESSION", raising=False)
