"""The user-owned abort interlock command."""

from __future__ import annotations

import json

import pytest

from cu import cli, config, exits, status


def test_abort_reports_its_state(capsys):
    assert cli.main(["abort"]) == exits.OK
    assert capsys.readouterr().out == "abort: clear\n"

    assert cli.main(["abort", "--json"]) == exits.OK
    assert json.loads(capsys.readouterr().out) == {"aborted": False}


def test_abort_set_creates_the_missing_runtime_directory(tmp_path, monkeypatch, capsys):
    runtime = tmp_path / "runtime-without-cu"
    runtime.mkdir()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))

    assert cli.main(["abort", "--set"]) == exits.OK
    assert capsys.readouterr().out == "abort: set\n"
    assert config.abort_path().is_file()


def test_abort_clear_reports_clear_and_returns_whether_it_was_set(capsys):
    config.set_abort()

    assert config.clear_abort() is True
    assert cli.main(["abort", "--clear"]) == exits.OK
    assert capsys.readouterr().out == "abort: clear\n"
    assert config.clear_abort() is False


@pytest.mark.parametrize(
    ("argv", "environment"),
    [
        (["abort", "--set"], True),
        (["abort", "--clear"], True),
        (["abort", "--session", "agent", "--set"], False),
        (["abort", "--session", "agent", "--clear"], False),
    ],
    ids=["environment-set", "environment-clear", "option-set", "option-clear"],
)
def test_an_agent_cannot_change_the_abort(argv, environment, monkeypatch, capsys):
    if environment:
        monkeypatch.setenv(status.SESSION_ENV, "agent")

    assert cli.main(argv) == exits.ABORT_CONTROLLED_BY_USER
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Ask them to set or clear it" in captured.err


def test_abort_does_not_publish_desktop_activity():
    assert cli.main(["abort"]) == exits.OK
    assert status.read()["active"] is False


def test_abort_rejects_the_global_activity_reason():
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--why", "stopping", "abort"])
    assert exit_info.value.code == exits.USAGE


def test_abort_control_refusal_is_not_retryable():
    assert exits.ABORT_CONTROLLED_BY_USER not in exits.RETRYABLE
