"""One desktop, one driver at a time — enforced across processes, not threads.

Every probe here spawns a real second process. A lock that is only tested
in-process proves nothing: `flock` is held per open file description, so the
interesting question — does a *separate* `cu` invocation see it — is the one a
single-process test cannot ask.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time

import pytest

from cu import cli, desktop, exits
from cu.safety import Refused

HOLDER = """
import sys, time
from cu import desktop
with desktop.held(sys.argv[1], sys.argv[2], wait=1.0):
    print("held", flush=True)
    time.sleep(float(sys.argv[3]))
"""


def hold(command: str, detail: str, seconds: float) -> subprocess.Popen:
    """A separate process holding the desktop, returned once it truly has it."""
    # os.environ carries the XDG_RUNTIME_DIR the suite is isolated into, so the
    # child locks the test's directory rather than the real session's.
    proc = subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent(HOLDER), command, detail, str(seconds)],
        stdout=subprocess.PIPE,
        text=True,
        env=os.environ.copy(),
    )
    assert proc.stdout is not None
    assert proc.stdout.readline().strip() == "held"
    return proc


def test_a_second_agent_is_refused_rather_than_interleaved():
    """The failure this prevents is silent: two agents warping the same pointer
    produce a click on the wrong window at exit 0."""
    other = hold("click", "clicking Submit", 5.0)
    try:
        with pytest.raises(Refused) as refusal, desktop.held("type", "a password", wait=0.2):
            pytest.fail("acquired a desktop another process was holding")
        assert refusal.value.code == exits.DESKTOP_BUSY
    finally:
        other.kill()
        other.wait()


def test_the_refusal_names_who_to_wait_for():
    """'Busy' alone leaves the caller nothing to tell the user or to reason
    about; the holder's own --why is the part worth relaying."""
    other = hold("type", "filling the login form", 5.0)
    try:
        with pytest.raises(Refused) as refusal, desktop.held("click", wait=0.05):
            pass
        assert "filling the login form" in refusal.value.reason
        assert str(other.pid) in refusal.value.reason
    finally:
        other.kill()
        other.wait()


def test_waiting_absorbs_an_ordinary_overlap_instead_of_refusing():
    """Two agents reaching for the desktop within a moment of each other is
    normal traffic, not an error worth surfacing."""
    other = hold("click", "a brief action", 0.3)
    try:
        started = time.monotonic()
        with desktop.held("click", "the next one", wait=5.0):
            waited = time.monotonic() - started
        assert waited >= 0.2, "returned without ever waiting for the holder"
    finally:
        other.kill()
        other.wait()


def test_a_killed_holder_does_not_wedge_the_desktop():
    """cu has no daemon to clean up after a killed agent. The kernel drops the
    lock when the process leaves, which is why the lock is the kernel's and not
    a file this code writes and removes."""
    other = hold("type", "a run about to be killed", 30.0)
    other.kill()
    other.wait()
    with desktop.held("click", "after the kill", wait=2.0):
        pass


def test_the_holder_is_not_read_from_an_unlocked_file():
    """The identity is advisory; the lock is the authority. A holder that has
    exited must not still be described as driving."""
    other = hold("type", "gone now", 0.15)
    other.wait()
    assert desktop.holder() is None


def test_a_read_only_command_never_queues_behind_a_driving_agent(capsys):
    """Observation has to stay available precisely while someone is driving —
    it is how the other agent, and the user, find out what is going on."""
    other = hold("type", "a long action", 5.0)
    try:
        started = time.monotonic()
        assert cli.main(["status", "--json"]) == exits.OK
        assert time.monotonic() - started < 1.0
        capsys.readouterr()
    finally:
        other.kill()
        other.wait()


def test_without_a_runtime_dir_injection_refuses_rather_than_losing_the_lock(monkeypatch):
    """Nowhere to serialize is a precondition failure, not a licence to inject
    unserialized. Dropping a safety property quietly is the failure mode this
    repository is about."""
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    with pytest.raises(Refused) as refusal, desktop.held("click", wait=0.0):
        pass
    assert refusal.value.code == exits.PRECONDITION_FAILED
