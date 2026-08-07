"""What `cu` is doing right now, published for anything that shows the user.

A person whose desktop is being driven should be able to tell at a glance, from
whatever they already look at. That is a different question from what the
action log answers. The log is history: append-only, complete, and read after
the fact. This is the present tense, and a status bar polling it twice a second
should not have to parse a growing file to find the end of it.

So the current state is **one small JSON object, replaced atomically**. A
reader gets a whole state or the previous whole state, never half of either,
and never pays for history it does not want. The append-only log stays where it
is and keeps answering the other question.

Two things make the state honest rather than merely present:

**It expires.** `cu` has no daemon, so nothing runs to write "finished" if a
process is killed between the two halves of an action. A record therefore
carries the moment it stops being true, and a reader that finds an expired
record reports idle. Failing to a stuck "an agent is driving your desktop" is
the one failure mode that would train the user to ignore it.

**It survives nothing.** Like the frame store and the abort flag, this lives
under `XDG_RUNTIME_DIR`: session state, gone on reboot, never a stale claim
about a session that ended.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

#: How long a single action's record stays true without being renewed. Long
#: enough to bridge the gap between two commands while a model decides what to
#: do next, short enough that a killed process stops claiming the desktop.
ACTION_TTL = 45.0

#: A session bracket is renewed by every command inside it, so its expiry only
#: matters once nothing is happening at all.
SESSION_TTL = 300.0

IDLE: dict[str, Any] = {"active": False, "state": "idle"}


def state_dir() -> Path | None:
    """Where the status lives, or None when this host has no runtime dir.

    None rather than an error: publishing status is a courtesy to the user and
    must never be the reason an action fails.
    """
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime:
        return None
    path = Path(runtime) / "cu"
    try:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError:
        return None
    return path


def status_path() -> Path | None:
    directory = state_dir()
    return directory / "status.json" if directory else None


def _write(record: dict[str, Any]) -> None:
    """Replace the status atomically, or leave the previous one alone.

    Written to a neighbouring file and renamed, because a reader polling this
    must never see a partial object. A failure here is silent for the same
    reason the action log's is: the desktop has already been acted on, and a
    courtesy that raises would turn a working action into a failed one.
    """
    path = status_path()
    if path is None:
        return
    scratch = path.with_suffix(".json.new")
    try:
        scratch.write_text(json.dumps(record))
        scratch.replace(path)
    except OSError:
        scratch.unlink(missing_ok=True)


def read() -> dict[str, Any]:
    """The current status, as a reader should understand it.

    Expiry is applied here rather than trusted from the file, so every consumer
    agrees about when a record stopped being true without implementing the rule
    itself.
    """
    path = status_path()
    if path is None or not path.is_file():
        return dict(IDLE)
    try:
        record = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return dict(IDLE)
    if not isinstance(record, dict):
        return dict(IDLE)
    if record.get("expires_at", 0) <= time.time():
        return {**IDLE, "expired": True, "last": record.get("detail", "")}
    return record


def _session() -> dict[str, Any] | None:
    """The bracket a command belongs to, if one is open and still live.

    Liveness is expiry, not a process. `cu` is one-shot, so the process that
    opened a session has already exited by the time the next command runs, and
    watching its pid would end every session immediately. A caller that does
    have a long-lived process to tie the session to says so with `--pid`, and
    only then is it watched.
    """
    session = read().get("session")
    if not session:
        return None
    if session.get("expires_at", 0) <= time.time():
        return None
    watched = session.get("pid")
    if watched and not alive(watched):
        return None
    return session


def alive(pid: int) -> bool:
    return Path(f"/proc/{pid}").is_dir()


def publish(command: str, detail: str = "", **extra: Any) -> dict[str, Any]:
    """Say what is happening, now.

    `detail` is the agent's own sentence about why, which is the part worth
    showing a person: "clicking Save" tells them something that "click" does
    not.
    """
    now = time.time()
    session = _session()
    record = {
        "active": True,
        "state": "working",
        "command": command,
        "detail": detail or (session or {}).get("detail", ""),
        "pid": os.getpid(),
        "at": now,
        "expires_at": now + ACTION_TTL,
        **extra,
    }
    if session:
        record["session"] = {**session, "expires_at": now + SESSION_TTL}
    _write(record)
    return record


def begin(detail: str, pid: int | None = None) -> dict[str, Any]:
    """Open a bracket that outlives the individual commands inside it.

    Without one the indicator blinks out whenever a model spends longer
    thinking than a single action's expiry, which reads as "it stopped" at
    exactly the moment the user most wants to know it has not.

    `pid` is the agent's own process where it has one. Given, the session ends
    when that process does, so a killed agent cannot leave the desktop claimed
    for the length of the timeout.
    """
    now = time.time()
    session = {
        "detail": detail,
        "pid": pid,
        "started_at": now,
        "expires_at": now + SESSION_TTL,
    }
    record = {
        "active": True,
        "state": "working",
        "command": "session",
        "detail": detail,
        "pid": os.getpid(),
        "at": now,
        "expires_at": now + SESSION_TTL,
        "session": session,
    }
    _write(record)
    return record


def end() -> dict[str, Any]:
    """Close the bracket and say so, rather than waiting for it to expire."""
    record = {**IDLE, "at": time.time(), "expires_at": 0}
    _write(record)
    return record


def describe(record: dict[str, Any]) -> str:
    """One line for a status bar, which has room for very little."""
    if not record.get("active"):
        return ""
    detail = record.get("detail") or record.get("command") or "working"
    return str(detail)
