"""The present tense: what a status bar reads, and when it stops being true."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import time

import pytest

from cu import cli, config, exits, status


def details(record=None):
    record = record or status.read()
    return [d["detail"] for d in record["drivers"]]


def test_nothing_published_reads_as_idle():
    assert status.read()["active"] is False
    assert status.summarize(status.read()) == ""


def test_follow_emits_the_first_status_immediately():
    feed = status.follow(interval=0.001)

    assert next(feed) == {"active": False, "aborted": False, "drivers": []}


def test_follow_skips_an_unchanged_status_before_the_next_change(monkeypatch):
    idle = {"active": False, "aborted": False, "drivers": []}
    active = {"active": True, "aborted": False, "drivers": [{"id": "run"}]}
    records = iter((idle, idle, active))
    monkeypatch.setattr(status, "read", lambda: next(records))

    feed = status.follow(interval=0.0)
    assert next(feed) == idle
    assert next(feed) == active


def test_follow_emits_each_derived_transition_once():
    feed = status.follow(interval=0.001)
    records = [next(feed)]

    status.begin("a run")
    records.append(next(feed))

    path = status.status_path()
    record = json.loads(path.read_text())
    record["drivers"][0]["expires_at"] = time.time() - 1
    path.write_text(json.dumps(record))
    records.append(next(feed))

    config.set_abort()
    records.append(next(feed))

    assert [record["active"] for record in records] == [False, True, False, False]
    assert [record["aborted"] for record in records] == [False, False, False, True]
    assert len(records[1]["drivers"]) == 1
    assert records[1]["drivers"][0]["detail"] == "a run"


def test_status_follow_writes_compact_json_lines(monkeypatch, capsys):
    records = (
        {"active": False, "aborted": False, "drivers": []},
        {"active": True, "aborted": False, "drivers": [{"id": "run"}]},
        {"active": False, "aborted": False, "drivers": []},
        {"active": False, "aborted": True, "drivers": []},
    )
    monkeypatch.setattr(status, "follow", lambda: iter(records))

    assert cli.main(["status", "--follow", "--json"]) == exits.OK
    assert capsys.readouterr().out.splitlines() == [
        json.dumps(record, separators=(",", ":")) for record in records
    ]


def test_status_follow_requires_json(capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["status", "--follow"])

    assert exit_info.value.code == exits.USAGE
    assert "requires --json" in capsys.readouterr().err


def test_a_published_action_says_what_it_is_for():
    status.publish("click", detail="clicking Submit")
    current = status.read()
    assert current["active"] is True
    assert status.summarize(current) == "clicking Submit"


def test_without_a_reason_the_command_is_the_answer():
    """Better than nothing, and never a sentence cu invented on the agent's
    behalf."""
    status.publish("shot")
    assert status.summarize(status.read()) == "shot"


def test_an_expired_entry_reads_as_idle():
    """cu has no daemon, so nothing writes 'finished' for a process that was
    killed mid-action. A stuck claim on the user's desktop is the one failure
    that would teach them to ignore the indicator."""
    status.publish("type", detail="typing a password")
    path = status.status_path()
    record = json.loads(path.read_text())
    record["drivers"][0]["expires_at"] = time.time() - 1
    path.write_text(json.dumps(record))

    assert status.read()["active"] is False


def test_one_agents_expiry_does_not_retire_another():
    """Entries expire one at a time because they describe different agents;
    a shared expiry would clear a bar that is still true."""
    stale = status.begin("a run that stalled")
    status.publish("click", detail="still going", session=status.begin("a live run")["id"])
    path = status.status_path()
    record = json.loads(path.read_text())
    for driver in record["drivers"]:
        if driver["id"] == stale["id"]:
            driver["expires_at"] = time.time() - 1
    path.write_text(json.dumps(record))

    assert details() == ["still going"]


def test_a_session_covers_the_commands_inside_it():
    """A model deciding what to do next routinely takes longer than one
    action's expiry; the indicator must not read as 'it finished'."""
    session = status.begin("filling in the expense form")["id"]
    status.publish("shot", session=session)
    assert details() == ["filling in the expense form"]


def test_a_command_with_its_own_reason_overrides_the_session():
    session = status.begin("filling in the expense form")["id"]
    status.publish("click", detail="clicking Submit", session=session)
    assert details() == ["clicking Submit"]


def test_a_session_is_named_rather_than_inferred(monkeypatch):
    """Process ancestry is a guess, and with two agents on one desktop a wrong
    guess files one agent's action under the other's intent — which reads as
    true. An unbracketed command is its own driver instead."""
    monkeypatch.delenv(status.SESSION_ENV, raising=False)
    status.begin("filling in the expense form")
    status.publish("click", detail="clicking Submit")
    assert sorted(details()) == ["clicking Submit", "filling in the expense form"]


RUNNER = """
import sys
from cu import status
status.publish("observe", detail=sys.argv[1])
"""


def test_unbracketed_commands_do_not_accumulate_on_the_bar(monkeypatch):
    """Each cu is a new short-lived process, so an unbracketed command that
    expired on a timeout would leave one entry per invocation and grow a row of
    marks for work that finished seconds ago. It claims the desktop for exactly
    as long as its process runs."""
    monkeypatch.delenv(status.SESSION_ENV, raising=False)
    for n in range(5):
        subprocess.run(
            [sys.executable, "-c", textwrap.dedent(RUNNER), f"action {n}"],
            env=os.environ.copy(),
            check=True,
        )

    assert status.read()["drivers"] == []


def test_the_environment_binds_a_command_to_its_session(monkeypatch):
    session = status.begin("booking a flight")["id"]
    monkeypatch.setenv(status.SESSION_ENV, session)
    status.publish("click", detail="accepting the fare")
    assert details() == ["accepting the fare"]


def test_two_agents_driving_are_both_visible():
    """The case a single record answers wrongly: the second agent's first
    command used to erase the first, and the bar showed one of two."""
    first = status.begin("booking a flight")["id"]
    second = status.begin("renaming the photos")["id"]
    status.publish("click", detail="accepting the fare", session=first)
    status.publish("type", detail="entering a filename", session=second)

    assert sorted(details()) == ["accepting the fare", "entering a filename"]


def test_ending_one_session_leaves_the_other_driving():
    """An agent that finished has no business clearing an indicator that is
    still true for someone else."""
    first = status.begin("booking a flight")["id"]
    second = status.begin("renaming the photos")["id"]
    status.end(first)

    assert details() == ["renaming the photos"]
    assert status.read()["active"] is True
    status.end(second)
    assert status.read()["active"] is False


def test_ending_without_naming_a_session_refuses_when_several_are_open(monkeypatch):
    """Guessing would clear an indicator that is still true, so the caller is
    asked which — and told what the choices are."""
    monkeypatch.delenv(status.SESSION_ENV, raising=False)
    first = status.begin("booking a flight")["id"]
    status.begin("renaming the photos")

    with pytest.raises(ValueError) as refusal:
        status.end()
    assert first in str(refusal.value)
    assert status.read()["active"] is True


def test_ending_the_only_session_needs_no_name(monkeypatch):
    """With one driver there is nothing to confuse it with, which keeps the
    single-agent flow a begin and an end."""
    monkeypatch.delenv(status.SESSION_ENV, raising=False)
    status.begin("doing something")
    status.end()
    assert status.read()["active"] is False


def test_a_session_tied_to_a_dead_process_stops_claiming_the_desktop():
    """Given a pid, the bracket is only as alive as the agent that opened it."""
    status.begin("a run that was killed", pid=999_999)
    assert status.read()["active"] is False


def test_a_session_without_a_pid_is_not_watched():
    """cu is one-shot: the process that opened the session has already exited,
    so watching it by default would end every session immediately."""
    status.begin("an ordinary run")
    assert details() == ["an ordinary run"]


def test_an_exclusive_session_refuses_unnamed_injection(monkeypatch, capsys):
    holder = status.begin("editing the report", exclusive=True)
    monkeypatch.setattr(cli, "cmd_key", lambda _args: exits.OK)

    assert holder["exclusive"] is True
    assert cli.main(["key", "--chord", "a"]) == exits.DESKTOP_RESERVED
    refusal = capsys.readouterr().err
    assert holder["id"] in refusal
    assert holder["detail"] in refusal


def test_an_exclusive_session_allows_its_named_injection(monkeypatch):
    holder = status.begin("editing the report", exclusive=True)
    monkeypatch.setattr(cli, "cmd_key", lambda _args: exits.OK)

    assert (
        cli.main(["key", "--chord", "a", "--session", holder["id"]])
        == exits.OK
    )
    monkeypatch.setenv(status.SESSION_ENV, holder["id"])
    assert cli.main(["key", "--chord", "a"]) == exits.OK


@pytest.mark.parametrize(
    ("handler", "argv"),
    [
        ("cmd_observe", ["observe"]),
        ("cmd_shot", ["shot"]),
        ("cmd_wait", ["wait", "focus"]),
        ("cmd_status", ["status"]),
        ("cmd_doctor", ["doctor"]),
        ("cmd_profiles", ["profiles"]),
    ],
)
def test_an_exclusive_session_never_refuses_read_only_commands(
    monkeypatch, handler, argv
):
    status.begin("editing the report", exclusive=True)

    def unexpected_check(_session=None):
        pytest.fail("a read-only command consulted the exclusive reservation")

    monkeypatch.setattr(status, "check_exclusive", unexpected_check)
    monkeypatch.setattr(cli, handler, lambda _args: exits.OK)
    assert cli.main(argv) == exits.OK


def test_a_second_exclusive_session_refuses_and_names_the_holder(capsys):
    assert (
        cli.main(["session", "begin", "--exclusive", "--why", "editing the report"])
        == exits.OK
    )
    holder = capsys.readouterr().out.strip()

    assert (
        cli.main(["session", "begin", "--exclusive", "--why", "second agent"])
        == exits.DESKTOP_RESERVED
    )
    refusal = capsys.readouterr().err
    assert holder in refusal
    assert "editing the report" in refusal


def test_a_nonexclusive_session_refuses_no_injection(monkeypatch):
    record = status.begin("ordinary work")
    monkeypatch.setattr(cli, "cmd_key", lambda _args: exits.OK)

    assert record["exclusive"] is False
    assert cli.main(["key", "--chord", "a"]) == exits.OK


def test_an_exclusive_session_with_a_dead_pid_refuses_no_injection(monkeypatch):
    status.begin("abandoned work", pid=999_999, exclusive=True)
    monkeypatch.setattr(cli, "cmd_key", lambda _args: exits.OK)

    assert cli.main(["key", "--chord", "a"]) == exits.OK


def test_an_expired_exclusive_session_refuses_no_injection(monkeypatch):
    status.begin("stale work", exclusive=True)
    path = status.status_path()
    record = json.loads(path.read_text())
    record["drivers"][0]["expires_at"] = time.time() - 1
    path.write_text(json.dumps(record))
    monkeypatch.setattr(cli, "cmd_key", lambda _args: exits.OK)

    assert cli.main(["key", "--chord", "a"]) == exits.OK


def test_desktop_reserved_is_retryable_and_has_a_response():
    assert exits.DESKTOP_RESERVED in exits.RETRYABLE
    assert exits.DESKTOP_RESERVED in exits.RESPONSE


def test_exclusive_begin_returns_no_id_when_registry_write_fails(
    monkeypatch, capsys
):
    def unwritable(*_args, **_kwargs):
        raise PermissionError("registry is unwritable")

    monkeypatch.setattr(status.tempfile, "mkstemp", unwritable)

    result = cli.main(
        ["session", "begin", "--exclusive", "--why", "editing the report"]
    )
    captured = capsys.readouterr()
    assert result == exits.RESERVATION_UNAVAILABLE
    assert captured.out == ""
    assert "registry is unwritable" in captured.err


def test_injection_refuses_when_reservation_registry_cannot_be_read(
    monkeypatch, capsys
):
    status.status_path().mkdir()
    monkeypatch.setattr(cli, "cmd_key", lambda _args: exits.OK)

    assert cli.main(["key", "--chord", "a"]) == exits.RESERVATION_UNAVAILABLE
    assert "reservation registry is unavailable" in capsys.readouterr().err


def test_injection_refuses_when_reservation_registry_has_no_driver_list(
    monkeypatch
):
    status.status_path().write_text("{}")
    monkeypatch.setattr(cli, "cmd_key", lambda _args: exits.OK)

    assert cli.main(["key", "--chord", "a"]) == exits.RESERVATION_UNAVAILABLE


def test_ordinary_publication_stays_best_effort_when_registry_is_unwritable():
    status.status_path().mkdir()
    published = status.publish("click", detail="still allowed")
    assert published["detail"] == "still allowed"
    assert status.read()["active"] is False


def test_ordinary_publication_stays_best_effort_when_scratch_cannot_be_created(
    monkeypatch
):
    monkeypatch.setattr(
        status.tempfile,
        "mkstemp",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError("read-only")),
    )
    published = status.publish("click", detail="still allowed")
    assert published["detail"] == "still allowed"


def test_reservation_unavailable_is_documented_and_not_retryable():
    assert exits.RESERVATION_UNAVAILABLE in exits.RESPONSE
    assert exits.RESERVATION_UNAVAILABLE not in exits.RETRYABLE


def test_a_corrupt_status_reads_as_idle_rather_than_raising():
    status.publish("shot")
    status.status_path().write_text("{ this is not json")
    assert status.read()["active"] is False


def test_a_host_with_no_runtime_dir_publishes_nothing_and_does_not_raise(monkeypatch):
    """Telling the user what is happening is a courtesy, and a courtesy must
    never be the reason an action fails."""
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    assert status.status_path() is None
    status.publish("click", detail="still fine")
    assert status.read()["active"] is False


def test_the_write_is_atomic_so_a_poller_never_sees_half_a_record():
    """A bar polling twice a second must get a whole state or the previous
    whole state; a torn read would render as an empty or malformed indicator."""
    session = status.begin("a run")["id"]
    for _ in range(50):
        status.publish("click", detail="second", session=session)
        assert details() == ["second"]
    assert not list(status.state_dir().glob("*.new"))


WRITER = """
import os, sys
from cu import status
status.begin(sys.argv[1], pid=None)
"""


def test_concurrent_agents_do_not_lose_each_others_entries():
    """Atomic replacement keeps a reader whole; it does nothing to stop two
    writers building a new registry on the same stale copy and one winning.
    Every one of these is a separate process, which is the only scope in which
    the question means anything for a one-shot CLI."""
    writers = [
        subprocess.Popen(
            [sys.executable, "-c", textwrap.dedent(WRITER), f"agent {n}"],
            env=os.environ.copy(),
        )
        for n in range(12)
    ]
    for writer in writers:
        assert writer.wait() == 0

    assert sorted(details()) == sorted(f"agent {n}" for n in range(12))
