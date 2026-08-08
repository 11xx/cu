"""Bare `cu` teaches; every mistyped invocation still fails.

Making the subcommand optional is what puts the exit-code contract at risk, so
the contract is asserted directly rather than inferred from the guide printing.
"""

from __future__ import annotations

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


def test_the_guide_states_the_codes_whose_response_is_not_retry():
    # These three are the ones a caller gets wrong at real cost: 40 must not be
    # retried at all, 25 must not be retyped blindly, and 50 must not be guessed.
    for code in (exits.ABORTED_BY_USER, exits.VERIFY_FAILED, exits.NOT_MAPPED):
        assert f"\n  {code}  " in guide.GUIDE


def test_the_guide_points_at_per_command_help():
    assert "cu <command> --help" in guide.GUIDE
