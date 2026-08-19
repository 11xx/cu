"""The interlocks every injection passes through, and the log it leaves.

The target is a live desktop with a person sitting at it, so the question is
never only "can this be done" but "is it still the right moment". Each guard
here answers that at *injection* time rather than at planning time: the
dangerous race is the user alt-tabbing between an agent deciding to type a
password and the keystrokes arriving.

Refusals carry distinct exit codes because the correct reaction differs. A
stale frame asks for a re-capture; a user abort asks for a full stop.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from . import checks, config, exits, frames


class Refused(RuntimeError):
    """An interlock said no. The code is the contract; the message is for a human."""

    def __init__(self, code: int, reason: str) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason


def _matches(value: str, needles: list[str]) -> str | None:
    lowered = value.lower()
    return next((n for n in needles if n.lower() in lowered), None)


def check_denylist(cfg: dict[str, Any], window: dict[str, Any] | None) -> None:
    """Refuse windows that must never receive synthetic input.

    Both halves matter. A class covers the password manager itself; a title
    covers the ordinary browser window currently showing a login form, which is
    the same secret behind a different toolkit.
    """
    if window is None:
        return
    safety = cfg["safety"]
    hit = _matches(window.get("class", ""), safety["denylist_classes"])
    if hit:
        raise Refused(
            exits.DENIED_BY_POLICY,
            f"window class {window['class']!r} matches denylisted {hit!r}",
        )
    hit = _matches(window.get("title", ""), safety["denylist_titles"])
    if hit:
        raise Refused(
            exits.DENIED_BY_POLICY,
            f"window title matches denylisted {hit!r}; refusing to inject",
        )


def check_abort() -> None:
    """The one refusal the agent cannot clear.

    `cu` never removes this file. Its removal is a physical act by the user,
    which is exactly what makes it a deadman switch rather than a suggestion.
    """
    if config.aborted():
        raise Refused(
            exits.ABORTED_BY_USER,
            f"the user's abort flag is set ({config.abort_path()}); stop and tell them",
        )


def check_session(processes: list[tuple[int, list[str]]] | None = None) -> None:
    locked = checks.session_unlocked(processes)
    if locked["status"] == checks.FAIL:
        raise Refused(exits.DENIED_BY_POLICY, locked["detail"])


def check_remapper(processes: list[tuple[int, list[str]]] | None = None) -> None:
    """An unfiltered remapper silently rewrites what gets injected.

    This is a property of the running process rather than of its config file,
    and it fails silently in the worst way: the keystroke arrives, as some
    other keystroke.
    """
    state = checks.xremap_filter(processes)
    if state["status"] == checks.FAIL:
        raise Refused(exits.PRECONDITION_FAILED, state["detail"])


def check_focus(cfg: dict[str, Any], expected: dict[str, Any] | None, live_focus) -> None:
    """Assert the window about to receive keystrokes is the intended one.

    This guards text and keys only. The race it exists for is the user
    alt-tabbing between an agent deciding to type and the keystrokes landing,
    which is how a password reaches the wrong application.
    """
    if not cfg["safety"]["require_focus"] or expected is None:
        return
    confirm_focus(expected, live_focus)


def confirm_focus(expected: dict[str, Any], live_focus: dict[str, Any] | None) -> None:
    """Confirm a focus request took effect, regardless of injection policy."""
    if live_focus is None or live_focus["address"] != expected["address"]:
        got = (
            f"{live_focus['class']} ({live_focus['title'][:40]!r})"
            if live_focus
            else "nothing"
        )
        raise Refused(
            exits.FOCUS_CHANGED,
            f"expected {expected['class']} ({expected['title'][:40]!r}) to hold "
            f"focus, but {got} does",
        )


def check_target(frame: dict[str, Any], under: dict[str, Any] | None) -> None:
    """Assert the point is still on the window the frame said was there.

    Focus is the wrong question for a pointer: clicking an unfocused window is
    how it becomes focused. What matters is that nothing has moved *over* the
    place being aimed at, because a click that lands on a window which appeared
    after the capture is exactly the "acted on a screenshot of a screen that
    has since changed" failure the frame contract exists to refuse.

    A frame depicting no particular window — a whole output, or a region —
    asserts nothing here. Its output geometry is checked by `check_frame`, and
    the denylist still applies to whatever is under the aimed point.
    """
    declared = frame.get("window")
    if declared is None:
        return
    if under is None or under["address"] != declared["address"]:
        got = under["class"] if under else "nothing"
        raise Refused(
            exits.FOCUS_CHANGED,
            f"frame {frame['id']} depicts {declared['class']}, but {got} is now "
            "at that point; re-capture before acting",
        )


def check_frame(
    cfg: dict[str, Any],
    frame: dict[str, Any],
    windows: list[dict[str, Any]],
    monitors: list[dict[str, Any]],
) -> None:
    why = frames.staleness(
        frame, windows, monitors, cfg["safety"]["frame_max_age"]
    )
    if why:
        raise Refused(exits.STALE_FRAME, f"frame {frame['id']}: {why}")
    check_content(cfg, frame, windows)


def check_content(
    cfg: dict[str, Any], frame: dict[str, Any], windows: list[dict[str, Any]]
) -> None:
    """Refuse when the window still holds the frame's place but shows something else.

    A separate code from staleness because the caller's next move differs: a
    stale frame is recaptured and the plan stands, while content that changed
    underneath means the plan was made about something else.

    Called again immediately before the event, not only when the frame is
    resolved. Device enumeration takes long enough for a page to navigate, and
    a check that stops at the plan cannot see that.
    """
    if not cfg["safety"]["frame_content_identity"]:
        return
    drifted = frames.content_drift(
        frame, windows, cfg["safety"]["title_drift_classes"]
    )
    if drifted:
        raise Refused(exits.CONTENT_CHANGED, drifted)


def confirm_pointer(cfg: dict[str, Any], want: tuple[float, float], got: dict[str, float]) -> None:
    """Refuse when the pointer is not where cu just put it.

    A mismatch means a hand is on the mouse. Fighting a person for the pointer
    is worse than giving up: they are trying to take control back, and the
    agent's next click would land wherever the contest left it.
    """
    tolerance = cfg["safety"]["cursor_drift_tolerance"]
    dx, dy = abs(got["x"] - want[0]), abs(got["y"] - want[1])
    if dx > tolerance or dy > tolerance:
        raise Refused(
            exits.HUMAN_ACTIVE,
            f"pointer is at ({got['x']},{got['y']}) after a warp to "
            f"({want[0]:.0f},{want[1]:.0f}); the user is moving the mouse",
        )


def record(cfg: dict[str, Any], entry: dict[str, Any]) -> None:
    """Append one action to the log.

    Append-only and never rotated in place: this is the raw material a recipe
    is promoted from, and it is the only record of what an agent did to a
    desktop while nobody was watching. A logging failure is not worth aborting
    an action that already happened.
    """
    path = config.log_path(cfg)
    line = json.dumps({"ts": time.time(), "pid": os.getpid(), **entry})
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as handle:
            handle.write(line + "\n")
    except OSError:
        pass


def _common(cfg: dict[str, Any], window: dict[str, Any] | None) -> None:
    """The guards that apply to any injection whatsoever.

    Ordered by the severity of what each protects, so the message a caller sees
    names the most important reason rather than the first one checked.
    """
    check_abort()
    processes = checks._processes(fresh=True)
    check_session(processes)
    check_denylist(cfg, window)
    check_remapper(processes)


def preflight_pointer(
    cfg: dict[str, Any],
    under: dict[str, Any] | None,
    frame: dict[str, Any] | None = None,
) -> None:
    """Guards for a command aimed by coordinates.

    Deliberately says nothing about focus: a pointer acts where it is pointed,
    and demanding the target already be focused would forbid the ordinary act
    of clicking a window to focus it.
    """
    _common(cfg, under)
    if frame is not None:
        check_target(frame, under)


def preflight_keyboard(
    cfg: dict[str, Any],
    window: dict[str, Any] | None,
    live_focus: dict[str, Any] | None,
) -> None:
    """Guards for a command delivered to whatever holds focus."""
    _common(cfg, window)
    check_focus(cfg, window, live_focus)
