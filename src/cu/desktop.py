"""The desktop is one resource, and only one agent may drive it at a time.

Every other interlock in this repository asks whether *this* action is safe.
This one asks a question none of them can see: whether someone else is already
acting. Nothing about a second agent's pointer warp is visible to the first
agent's checks, and the two failures it produces are both silent.

**The pointer is shared.** `cu click` warps, then presses. It re-reads the
pointer immediately before the button event, which catches the user reaching
for the mouse — but a second `cu` warping in that same window is
indistinguishable from nothing having happened, and the click lands, at exit 0,
on whatever is now under the cursor. That is the exact failure the frame
contract exists to prevent, arriving through a door the frame contract does not
cover.

**Focus and the clipboard are shared too.** Two `type` commands interleave
their keystrokes into whichever window holds focus, and each restores the
clipboard the other saved.

So injection is serialized host-wide, with `flock` on a file under
`XDG_RUNTIME_DIR`. Three properties matter, and all three come from the kernel
rather than from this code: the lock is between *processes*, which is the only
scope that means anything for a one-shot CLI; it is released when the holder
exits, so an agent killed mid-action cannot wedge the desktop; and it needs no
daemon to arbitrate.

Waiting is bounded. Two agents reaching for the desktop at nearly the same
moment is ordinary and worth absorbing; an agent that holds it for longer than
that is a fact the caller needs told, not hidden inside a stall. Past the wait
the answer is exit 42 with the holder named — a refusal the caller can act on,
in a repository whose contract is its exit codes.
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from . import exits
from .safety import Refused

#: How often the wait re-tries. Short enough to be imperceptible against the
#: latency of the action being waited for, which is dominated by device
#: enumeration.
POLL = 0.05


def state_dir() -> Path | None:
    """`cu`'s runtime directory, or None when this host has no XDG_RUNTIME_DIR.

    Shared with the status registry and the frame store: one directory, 0700,
    on tmpfs, cleared on reboot. None rather than an exception, because the
    callers differ on whether its absence is fatal — for the status feed it is
    a courtesy withheld, for the desktop lock it is a refusal.
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


def lock_path() -> Path | None:
    directory = state_dir()
    return directory / "desktop.lock" if directory else None


def holder() -> dict[str, Any] | None:
    """Who is driving the desktop, as they described themselves.

    Read without taking the lock, because the only caller is a refusal that has
    already failed to take it. The contents are advisory — the lock itself is
    the authority, and this is what makes its refusal say something useful.
    """
    path = lock_path()
    if path is None or not path.is_file():
        return None
    try:
        record = json.loads(path.read_text() or "null")
    except (OSError, json.JSONDecodeError):
        return None
    return record if isinstance(record, dict) else None


def describe_holder() -> str:
    current = holder()
    if not current:
        return "another agent"
    detail = current.get("detail") or current.get("command") or "an action"
    return f"pid {current.get('pid', '?')} ({detail})"


@contextmanager
def held(command: str, detail: str = "", *, wait: float) -> Iterator[None]:
    """Drive the desktop exclusively for the length of one command.

    The holder writes its own identity into the lock file so that whoever it
    turns away can be told who to wait for. That is written after the lock is
    taken and cleared before it is dropped, so a stale identity is never read
    as a live one: an unlocked file's contents are never consulted.
    """
    path = lock_path()
    if path is None:
        # Nowhere to put the lock means no way to serialize, and injecting
        # anyway would silently drop a safety property rather than fail. The
        # same absence already fails `cu doctor`.
        raise Refused(
            exits.PRECONDITION_FAILED,
            "XDG_RUNTIME_DIR is unset, so cu cannot serialize access to the desktop",
        )
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        _acquire(fd, wait)
        os.ftruncate(fd, 0)
        os.pwrite(
            fd,
            json.dumps({"pid": os.getpid(), "command": command, "detail": detail}).encode(),
            0,
        )
        try:
            yield
        finally:
            os.ftruncate(fd, 0)
    finally:
        os.close(fd)


def _acquire(fd: int, wait: float) -> None:
    """Take the lock, waiting up to `wait` seconds, or refuse naming the holder."""
    deadline = time.monotonic() + max(wait, 0.0)
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EAGAIN):
                raise
            if time.monotonic() >= deadline:
                raise Refused(
                    exits.DESKTOP_BUSY,
                    f"{describe_holder()} is driving the desktop",
                ) from exc
            time.sleep(POLL)
