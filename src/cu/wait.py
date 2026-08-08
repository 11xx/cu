"""Blocking on a compositor event instead of screenshotting in a loop.

Hyprland publishes a line per state change, so "wait until the dialog opens" is
a read on a socket rather than a capture every half second. That is the
difference between a few bytes and a few thousand tokens per attempt.

The stream is `event>>arg,arg,arg`. Addresses arrive there **without** the `0x`
prefix that `hyprctl clients` reports, so the two forms are normalised before
any comparison — left alone they simply never match, and the wait would run to
its timeout while the thing it waited for sat on screen.
"""

from __future__ import annotations

import selectors
import socket
import time
from collections.abc import Iterator
from contextlib import closing
from typing import Any

from . import hypr, observe

#: Which Hyprland events answer each condition `cu wait` accepts. The v2 forms
#: carry addresses rather than names and are preferred where both exist.
EVENTS = {
    "window-open": ("openwindow",),
    "window-close": ("closewindow",),
    "focus": ("activewindow", "activewindowv2"),
    "title": ("windowtitlev2", "activewindow"),
    "workspace": ("workspace", "workspacev2"),
}


class WaitTimeout(RuntimeError):
    pass


def normalise_address(value: str) -> str:
    """One spelling for an address, whichever side it came from."""
    return value[2:].lower() if value.startswith("0x") else value.lower()


def connect() -> socket.socket:
    """Subscribe to the event stream.

    Separate from reading it so that a caller can subscribe *before* asking
    whether what it waits for has already happened. Between those two acts the
    compositor is still running, and an event announced in the gap is one
    nobody is listening for — the wait would then run to its timeout and
    report that the thing never happened while it sat on screen.
    """
    path = hypr.event_socket()
    if path is None or not path.is_socket():
        raise hypr.HyprError("the Hyprland event socket is unavailable")
    conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    conn.connect(str(path))
    return conn


def stream(conn: socket.socket, deadline: float) -> Iterator[tuple[str, list[str]]]:
    """Yield `(event, args)` until the deadline passes.

    A partial line is held rather than parsed: the socket delivers bytes, and a
    line split across two reads would otherwise surface as two malformed events.
    """
    selector = selectors.DefaultSelector()
    selector.register(conn, selectors.EVENT_READ)
    buffered = ""
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            if not selector.select(timeout=remaining):
                return
            chunk = conn.recv(8192)
            if not chunk:
                return
            buffered += chunk.decode(errors="replace")
            while "\n" in buffered:
                line, buffered = buffered.split("\n", 1)
                if ">>" not in line:
                    continue
                name, _, rest = line.partition(">>")
                yield name, rest.split(",")
    finally:
        selector.close()


def _window_matches(window: dict[str, Any], cls: str | None, title: str | None) -> bool:
    return (not cls or cls.lower() in window["class"].lower()) and (
        not title or title.lower() in window["title"].lower()
    )


def already_true(
    condition: str,
    cls: str | None,
    title: str | None,
    snap: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Whether the thing being waited for has already happened.

    Checked immediately after subscribing. A wait for a window that is already
    open would otherwise block until its timeout, while subscribing first keeps
    an event in the snapshot gap from being lost.
    """
    snap = snap or observe.snapshot()
    if condition in {"window-open", "title"}:
        match = next(
            (w for w in snap["windows"] if _window_matches(w, cls, title)), None
        )
        return match
    if condition == "focus":
        focus = snap["focused"]
        if focus and _window_matches(focus, cls, title):
            return focus
    return None


def _resolve(args: list[str], snap_windows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The window an event refers to, found by address wherever one is given."""
    for arg in args:
        address = normalise_address(arg.strip())
        match = next(
            (w for w in snap_windows if normalise_address(w["address"]) == address), None
        )
        if match:
            return match
    return None


def until(
    condition: str,
    *,
    cls: str | None = None,
    title: str | None = None,
    timeout: float = 30.0,
    settle: float = 0.05,
) -> dict[str, Any]:
    """Block until the condition holds, and return what satisfied it."""
    if condition not in EVENTS:
        raise ValueError(f"unknown condition {condition!r}; expected one of {sorted(EVENTS)}")
    if condition == "workspace" and (cls or title):
        raise ValueError("workspace waits do not take window class or title filters")

    deadline = time.monotonic() + timeout
    wanted = EVENTS[condition]
    # Subscribed before asking. The snapshot answers the common case without
    # waiting; subscribing first is what makes losing that race cost nothing.
    with closing(connect()) as conn:
        snap = observe.snapshot()
        existing = already_true(condition, cls, title, snap)
        if existing is not None:
            return {"condition": condition, "matched": existing, "waited": False}
        known = {normalise_address(w["address"]): w for w in snap["windows"]}

        for name, args in stream(conn, deadline):
            if name not in wanted:
                continue
            if condition == "window-close":
                address = normalise_address(args[0])
                closed = known.pop(address, None)
                if (cls or title) and (closed is None or not _window_matches(closed, cls, title)):
                    continue
                return {
                    "condition": condition,
                    "matched": closed or {"address": args[0]},
                    "waited": True,
                }
            if condition == "workspace":
                return {
                    "condition": condition,
                    "matched": {
                        "workspace": args[-1],
                        **({"id": args[0]} if len(args) > 1 else {}),
                    },
                    "waited": True,
                }
            # The event names a window; its geometry and title come from a
            # fresh snapshot, because the event carries neither and an agent's
            # next move needs both.
            time.sleep(settle)  # a window is mapped a moment after it is announced
            snap = observe.snapshot()
            known.update({normalise_address(w["address"]): w for w in snap["windows"]})
            match = _resolve(args, snap["windows"])
            if match is None and condition == "focus":
                # The non-v2 focus event carries a class and title rather than
                # an address, so fall back to whatever now holds focus.
                match = next((w for w in snap["windows"] if w["focused"]), None)
            if match and _window_matches(match, cls, title):
                return {"condition": condition, "matched": match, "waited": True}

    raise WaitTimeout(
        f"no {condition} event matching "
        f"{'class ' + cls if cls else ''}{' title ' + repr(title) if title else ''}"
        f" within {timeout:g}s"
    )
