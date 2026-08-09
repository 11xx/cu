"""Bare `cu` teaches; every mistyped invocation still fails.

Making the subcommand optional is what puts the exit-code contract at risk, so
the contract is asserted directly rather than inferred from the guide printing.
"""

from __future__ import annotations

import json

import pytest

from cu import cli, exits, guide


def test_a_bare_invocation_prints_the_guide(capsys):
    assert cli.main([]) == exits.OK
    assert capsys.readouterr().out == guide.GUIDE


def test_flags_without_a_command_are_still_a_guide_request(capsys):
    # The guide is prose, so --json has nothing to change about it.
    assert cli.main(["--json"]) == exits.OK
    assert capsys.readouterr().out == guide.GUIDE


@pytest.mark.parametrize(
    "argv",
    [
        ["bogus"],
        ["--nope"],
        ["know"],
        ["know", "show"],
        ["observe", "--nope"],
    ],
    ids=["unknown-command", "unknown-flag", "missing-subcommand", "missing-argument",
         "unknown-flag-after-command"],
)
def test_a_malformed_invocation_is_a_usage_error(argv):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(argv)
    assert exit_info.value.code == exits.USAGE


def test_session_begin_accepts_a_positional_purpose(capsys):
    assert cli.main(["session", "begin", "booking a flight"]) == exits.OK
    session = capsys.readouterr().out.strip()

    assert cli.main(["session", "end", "--session", session]) == exits.OK


def test_session_begin_accepts_why_and_status_reports_its_purpose(capsys):
    assert cli.main(["session", "begin", "--why", "booking a flight"]) == exits.OK
    session = capsys.readouterr().out.strip()

    assert cli.main(["status", "--json"]) == exits.OK
    assert json.loads(capsys.readouterr().out)["drivers"][0]["detail"] == "booking a flight"
    assert cli.main(["session", "end", "--session", session]) == exits.OK


@pytest.mark.parametrize(
    "argv",
    [
        ["session", "begin"],
        ["session", "begin", "positional", "--why", "flag"],
    ],
    ids=["neither-purpose", "both-purposes"],
)
def test_session_begin_requires_exactly_one_purpose(argv, capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(argv)

    assert exit_info.value.code == exits.USAGE
    assert "PURPOSE or --why TEXT" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv",
    [
        ["session", "--why", "x", "begin", "y"],
        ["status", "--why", "x"],
        ["status", "--session", "x"],
    ],
    ids=["session-before-subcommand", "status-why", "status-session"],
)
def test_status_and_session_reject_action_options(argv):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(argv)
    assert exit_info.value.code == exits.USAGE


def test_the_guide_states_the_codes_whose_response_is_not_retry():
    # These three are the ones a caller gets wrong at real cost: 40 must not be
    # retried at all, 25 must not be retyped blindly, and 50 must not be guessed.
    for code in (exits.ABORTED_BY_USER, exits.VERIFY_FAILED, exits.NOT_MAPPED):
        assert f"\n  {code}  " in guide.GUIDE


def test_the_guide_points_at_per_command_help():
    assert "cu <command> --help" in guide.GUIDE
