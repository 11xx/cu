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


# --- the auto-hidden-UI tactic -------------------------------------------

def _section(title):
    """One guide section, up to the next unindented heading."""
    rest = guide.GUIDE[guide.GUIDE.index(title) + len(title):]
    lines = []
    for line in rest.splitlines():
        if line and not line.startswith(" "):
            break
        lines.append(line)
    return "\n".join(lines)


HIDDEN_UI = _section("CONTROLS THAT HIDE UNTIL THE POINTER ARRIVES")


def test_the_tactic_names_no_browser_and_no_fixed_edge():
    """The edge and the controls behind it are the user's configuration. A
    named browser or a named side would turn one host's reading into a false
    product invariant."""
    section = HIDDEN_UI.lower()
    for host_reading in ("helium", "zen", "vivaldi", "firefox", "the top edge is"):
        assert host_reading not in section


def test_the_tactic_asks_what_was_learned_before_probing():
    assert "cu know show" in HIDDEN_UI
    assert "learned state" in HIDDEN_UI


def test_the_tactic_requires_a_fresh_capture_before_acting():
    """A revealed control is not in the frame that was hovered from, and a
    coordinate is only ever valid in its own frame."""
    assert "cu shot --after" in HIDDEN_UI
    assert "fresh frame" in HIDDEN_UI
    assert "coordinates never cross frames" in HIDDEN_UI


def test_the_tactic_prefers_widening_to_guessing_an_edge():
    assert "widen the capture rather than hovering each edge" in HIDDEN_UI


POSTCONDITION = _section("CHOOSE THE POSTCONDITION BEFORE INPUT")


def test_postcondition_guidance_names_the_four_evidence_channels():
    for channel in ("compositor event", "watched pixels", "granted `cu web wait`", "file count"):
        assert channel in POSTCONDITION
    assert "input receipt says that an event was dispatched" in POSTCONDITION


def test_bulk_guidance_requires_representative_selection_and_recovery():
    assert "inspect one representative item" in POSTCONDITION
    assert "verify the visible selection count" in POSTCONDITION
    assert "recover only the failed items" in POSTCONDITION
    assert "failed or unobserved reveal" in POSTCONDITION


@pytest.mark.parametrize("command", ["cu know show", "cu move --frame", "cu shot --after",
                                     "cu know note"])
def test_every_command_the_tactic_names_exists(command):
    """A guide that promises more than the tool does is worse than none."""
    parser = cli.build_parser()
    name = command.split()[1]
    subparsers = [a for a in parser._actions if hasattr(a, "choices") and a.choices]
    assert any(name in (a.choices or {}) for a in subparsers)
    assert command in guide.GUIDE
