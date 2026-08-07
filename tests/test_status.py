"""The present tense: what a status bar reads, and when it stops being true."""

from __future__ import annotations

import json
import time

from cu import status


def test_nothing_published_reads_as_idle():
    assert status.read()["active"] is False
    assert status.describe(status.read()) == ""


def test_a_published_action_says_what_it_is_for():
    status.publish("click", detail="clicking Submit")
    current = status.read()
    assert current["active"] is True
    assert status.describe(current) == "clicking Submit"


def test_without_a_reason_the_command_is_the_answer():
    """Better than nothing, and never a sentence cu invented on the agent's
    behalf."""
    status.publish("shot")
    assert status.describe(status.read()) == "shot"


def test_an_expired_record_reads_as_idle():
    """cu has no daemon, so nothing writes 'finished' for a process that was
    killed mid-action. A stuck claim on the user's desktop is the one failure
    that would teach them to ignore the indicator."""
    status.publish("type", detail="typing a password")
    path = status.status_path()
    record = json.loads(path.read_text())
    record["expires_at"] = time.time() - 1
    path.write_text(json.dumps(record))

    current = status.read()
    assert current["active"] is False
    assert current["expired"] is True


def test_a_session_covers_the_commands_inside_it():
    """A model deciding what to do next routinely takes longer than one
    action's expiry; the indicator must not read as 'it finished'."""
    status.begin("filling in the expense form")
    status.publish("shot")
    assert status.describe(status.read()) == "filling in the expense form"


def test_a_command_with_its_own_reason_overrides_the_session():
    status.begin("filling in the expense form")
    status.publish("click", detail="clicking Submit")
    assert status.describe(status.read()) == "clicking Submit"


def test_ending_a_session_is_immediate_rather_than_waiting_for_expiry():
    status.begin("doing something")
    status.end()
    assert status.read()["active"] is False


def test_a_session_tied_to_a_dead_process_stops_claiming_the_desktop():
    """Given a pid, the bracket is only as alive as the agent that opened it."""
    status.begin("a run that was killed", pid=999_999)
    status.publish("shot")
    assert status.describe(status.read()) == "shot"


def test_a_session_without_a_pid_is_not_watched():
    """cu is one-shot: the process that opened the session has already exited,
    so watching it by default would end every session immediately."""
    status.begin("an ordinary run")
    status.publish("shot")
    assert status.describe(status.read()) == "an ordinary run"


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


def test_the_write_is_atomic_so_a_poller_never_sees_half_a_record(tmp_path):
    """A bar polling twice a second must get a whole state or the previous
    whole state; a torn read would render as an empty or malformed indicator."""
    status.publish("click", detail="first")
    seen = []
    for _ in range(50):
        status.publish("click", detail="second")
        seen.append(status.read())
    assert all(r.get("detail") == "second" for r in seen)
    assert not list(status.state_dir().glob("*.new"))
