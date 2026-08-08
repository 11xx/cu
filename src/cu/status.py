"""What is driving this desktop right now, published for anything that shows
the user.

A person whose desktop is being driven should be able to tell at a glance, from
whatever they already look at. That is a different question from what the
action log answers. The log is history: append-only, complete, and read after
the fact. This is the present tense, and a status bar polling it twice a second
should not have to parse a growing file to find the end of it.

So the current state is **one small JSON object, replaced atomically**. A
reader gets a whole state or the previous whole state, never half of either,
and never pays for history it does not want. The append-only log stays where it
is and keeps answering the other question.

**It is a registry, not a record.** Nothing stops two harnesses from driving
one desktop, and a single last-writer-wins record answers the user's question
wrongly in exactly that case: the second agent's first command erases the
first's, and the bar shows one agent while two are typing. Each driver is its
own entry, so the honest answer to "what is happening" is a list — and the bar
that renders it shows the user how many things to reckon with, not just that
there is at least one.

Because it is read-modify-written by concurrent processes, every mutation takes
a lock. Atomic replacement alone keeps a *reader* whole; it does nothing to
stop two writers from each building a new registry on the same stale copy and
one of them winning.

Two further things make the state honest rather than merely present:

**It expires.** `cu` has no daemon, so nothing runs to write "finished" if a
process is killed between the two halves of an action. Each entry therefore
carries the moment it stops being true, and a reader drops entries past it.
Failing to a stuck "an agent is driving your desktop" is the one failure mode
that would train the user to ignore it.

**It survives nothing.** Like the frame store and the desktop lock, this lives
under `XDG_RUNTIME_DIR`: session state, gone on reboot, never a stale claim
about a session that ended.
"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from . import config
from .config import ConfigError
from .desktop import state_dir

#: How long one command's entry stays true without being renewed. Long enough
#: to bridge the gap between two commands while a model decides what to do
#: next, short enough that a killed process stops claiming the desktop.
ACTION_TTL = 45.0

#: A session entry is renewed by every command inside it, so its expiry only
#: matters once that agent has stopped doing anything at all.
SESSION_TTL = 300.0

#: The environment variable that binds a command to the session that owns it.
#: An agent that cannot set it passes --session instead.
SESSION_ENV = "CU_SESSION"

#: Re-exported so that `state_dir` reads as part of this module's surface to
#: its callers, rather than as a detail of where the desktop lock happens to
#: live. The two share one directory by design.
__all__ = [
    "ACTION_TTL",
    "SESSION_ENV",
    "SESSION_TTL",
    "alive",
    "begin",
    "current_session",
    "describe",
    "end",
    "publish",
    "read",
    "state_dir",
    "status_path",
    "summarize",
]


def status_path() -> Path | None:
    directory = state_dir()
    return directory / "status.json" if directory else None


def _lock_path() -> Path | None:
    directory = state_dir()
    return directory / "status.lock" if directory else None


def current_session() -> str | None:
    """The session this command belongs to, if its caller named one.

    Never inferred. Two agents on one desktop make process ancestry a guess
    about which stretch of work a command belongs to, and a wrong guess files
    one agent's action under the other's intent — which is worse than an
    unbracketed entry, because it reads as true.
    """
    return os.environ.get(SESSION_ENV) or None


def alive(pid: int) -> bool:
    return Path(f"/proc/{pid}").is_dir()


def _live(drivers: Any, now: float) -> list[dict[str, Any]]:
    """The entries still describing something that is happening.

    Expiry is applied on the way out of the file rather than trusted from it,
    so every consumer agrees about when an entry stopped being true without
    implementing the rule itself. A watched process that has exited retires its
    entry immediately, without waiting out the timeout.
    """
    if not isinstance(drivers, list):
        return []
    live = []
    for driver in drivers:
        if not isinstance(driver, dict) or not driver.get("id"):
            continue
        if driver.get("expires_at", 0) <= now:
            continue
        watched = driver.get("agent_pid")
        if watched and not alive(watched):
            continue
        live.append(driver)
    return live


def _load(now: float) -> list[dict[str, Any]]:
    path = status_path()
    if path is None or not path.is_file():
        return []
    try:
        record = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(record, dict):
        return []
    return _live(record.get("drivers"), now)


def _write(drivers: list[dict[str, Any]]) -> None:
    """Replace the registry atomically, or leave the previous one alone.

    Written to a neighbouring file and renamed, because a reader polling this
    must never see a partial object. A failure here is silent for the same
    reason the action log's is: the desktop has already been acted on, and a
    courtesy that raises would turn a working action into a failed one.
    """
    path = status_path()
    if path is None:
        return
    record = {"active": bool(drivers), "drivers": drivers}
    fd, temporary = tempfile.mkstemp(prefix=".status-", suffix=".new", dir=path.parent)
    os.close(fd)
    scratch = Path(temporary)
    try:
        scratch.write_text(json.dumps(record))
        scratch.replace(path)
    except OSError:
        scratch.unlink(missing_ok=True)


@contextmanager
def _registry() -> Iterator[list[dict[str, Any]]]:
    """The live drivers, mutable, with the whole read-modify-write serialized.

    Held across both halves: two agents publishing at once would otherwise each
    read the same registry, add themselves to it, and write back a version
    missing the other. The lock is a separate file from the status itself,
    since the status is replaced by rename and a lock on a replaced inode
    guards nothing.
    """
    path = _lock_path()
    if path is None:
        yield []
        return
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError:
        yield []
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        drivers = _load(time.time())
        yield drivers
        _write(drivers)
    finally:
        os.close(fd)


def _aborted() -> bool:
    """Whether the user's abort flag is set, for a reader that must show it.

    `config.aborted` answers True when there is no runtime directory, which is
    the right answer for an injection deciding whether to act and the wrong one
    for a bar deciding what to draw: there is no session, so there is nothing
    to report. The flag is only reported where it could actually have been set.
    """
    try:
        return config.abort_path().exists()
    except (ConfigError, OSError):
        return False


def read() -> dict[str, Any]:
    """Every agent currently driving this desktop, as a reader should see it.

    The abort flag rides along because an indicator has to show it. The user
    presses their abort and each agent stops at its next command; without this
    the bar would show nothing changing in between, at the one moment they most
    need to see that their stop landed.
    """
    drivers = _load(time.time())
    return {"active": bool(drivers), "aborted": _aborted(), "drivers": drivers}


def _find(drivers: list[dict[str, Any]], key: str) -> dict[str, Any] | None:
    return next((d for d in drivers if d.get("id") == key), None)


def publish(command: str, detail: str = "", session: str | None = None) -> dict[str, Any]:
    """Say what is happening, now.

    `detail` is the agent's own sentence about why, which is the part worth
    showing a person: "clicking Save" tells them something that "click" does
    not.

    A command inside a session renews that session's entry and takes its
    detail when it offers none of its own.

    A command outside one is its own driver, watching its own process. That is
    the whole of what an unbracketed command honestly claims: something is
    happening for exactly as long as the command runs. Letting it expire on a
    timeout instead would leave one entry behind per invocation, and an agent
    working without a session would grow a row of marks on the user's bar for
    work that finished seconds ago.
    """
    now = time.time()
    loose = session is None and current_session() is None
    key = session or current_session() or f"pid:{os.getpid()}"
    with _registry() as drivers:
        driver = _find(drivers, key)
        if driver is None:
            driver = {
                "id": key,
                "session": False,
                "agent_pid": os.getpid() if loose else None,
                "started_at": now,
            }
            drivers.append(driver)
        driver.update(
            {
                "state": "working",
                "command": command,
                "detail": detail or driver.get("detail", ""),
                "pid": os.getpid(),
                "updated_at": now,
                "expires_at": now + (SESSION_TTL if driver.get("session") else ACTION_TTL),
            }
        )
        return dict(driver)


def begin(detail: str, pid: int | None = None) -> dict[str, Any]:
    """Open a bracket that outlives the individual commands inside it.

    Without one the indicator blinks out whenever a model spends longer
    thinking than a single action's expiry, which reads as "it stopped" at
    exactly the moment the user most wants to know it has not.

    The returned id is how later commands say they belong here, through
    `CU_SESSION` or `--session`. `pid` is the agent's own process where it has
    one; given, the session ends when that process does, so a killed agent
    cannot leave the desktop claimed for the length of the timeout.
    """
    now = time.time()
    driver = {
        "id": uuid.uuid4().hex[:12],
        "session": True,
        "agent_pid": pid,
        "state": "working",
        "command": "session",
        "detail": detail,
        "pid": os.getpid(),
        "started_at": now,
        "updated_at": now,
        "expires_at": now + SESSION_TTL,
    }
    with _registry() as drivers:
        drivers.append(driver)
    return dict(driver)


def end(session: str | None = None) -> dict[str, Any]:
    """Close one bracket, rather than waiting for it to expire.

    Ending is scoped to a single driver because the registry is shared: an
    agent that finished has no business retiring another one's claim, and the
    desktop stays claimed while anyone is still driving it.

    With no session named and exactly one driver present, that one is meant —
    there is nothing to confuse it with. With several, the caller is asked
    which, since guessing would clear an indicator that is still true.
    """
    key = session or current_session()
    with _registry() as drivers:
        if key is None:
            if len(drivers) != 1:
                ids = ", ".join(d["id"] for d in drivers) or "none"
                raise ValueError(
                    f"name the session to end with --session; {len(drivers)} are open ({ids})"
                )
            key = drivers[0]["id"]
        ended = _find(drivers, key)
        drivers[:] = [d for d in drivers if d.get("id") != key]
        remaining = len(drivers)
    return {"ended": key, "was_open": ended is not None, "remaining": remaining}


def describe(driver: dict[str, Any]) -> str:
    """One line for a status bar, which has room for very little."""
    detail = driver.get("detail") or driver.get("command") or "working"
    return str(detail)


def summarize(record: dict[str, Any]) -> str:
    """Every driver, one per line, and nothing at all when nobody is driving."""
    return "\n".join(describe(d) for d in record.get("drivers", []))
