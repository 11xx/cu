"""Acting on the screen: aim, verify, inject, record.

Every command here follows the same four beats, and the order is the safety
property. Aiming resolves an image coordinate through its frame into logical
space. Verifying re-reads the world immediately before injecting, because the
plan was made in the past and the desktop is live. Injecting is the only step
that writes events. Recording happens either way, including under `--dry-run`,
so the log describes intent as faithfully as it describes action.

Positioning is the compositor's job and buttons are the kernel's. Hyprland's
warp is exact in logical coordinates; a relative uinput pointer travels through
an acceleration curve and lands somewhere near where it was aimed.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from typing import Any

from . import compositor, config, exits, frames, hypr, observe, safety, uinput
from .safety import Refused


def send_chords(cfg: dict[str, Any], chords: list[str], backend: str) -> None:
    """Emit each chord through the selected backend.

    The uinput device is built once for the whole sequence: constructing it
    costs about 100 ms, which is most of the price of an input command, and
    paying that per keystroke is what makes a batched command worth having.
    """
    if backend == "compositor":
        for chord in chords:
            compositor.send_chord(chord)
        return
    with uinput.Device(settle_ms=cfg["input"]["settle_ms"]) as device:
        for index, chord in enumerate(chords):
            if index:
                time.sleep(cfg["input"]["gap_ms"] / 1000)
            device.chord(chord, cfg["input"]["press_ms"])


def aim(frame: dict[str, Any], x: float, y: float, snap: dict[str, Any]) -> dict[str, Any]:
    """Resolve an image coordinate to a logical point and the window under it.

    The window matters even when the caller named none: the denylist has to
    apply to the thing actually being clicked, which on a full-output frame is
    whatever happens to be at that point.
    """
    lx, ly = frames.to_logical(frame, x, y)
    declared = frame.get("window")
    under = observe.window_at(lx, ly, snap["windows"])
    return {
        "logical": {"x": lx, "y": ly},
        # Where the point sits inside its window, as a fraction. Absolute
        # coordinates describe one moment; a fraction survives the window being
        # moved or resized, and is the only form of a click worth replaying.
        "relative": _relative_to(under, lx, ly),
        # A window frame asserts its own window; anything else takes whatever
        # is under the point.
        "window": (
            next(
                (w for w in snap["windows"] if w["address"] == declared["address"]),
                None,
            )
            if declared
            else under
        ),
        "window_under_point": under,
    }


def warp(cfg: dict[str, Any], x: float, y: float, *, dry_run: bool) -> dict[str, float]:
    """Place the pointer, then confirm it stayed placed.

    The readback is not paranoia about the compositor; it is how a human's hand
    on the mouse becomes visible. `cu` yields to it rather than fighting for
    the pointer.
    """
    if dry_run:
        return {"x": x, "y": y}
    hypr.move_cursor(x, y)
    landed = observe.cursor()
    safety.confirm_pointer(cfg, (x, y), landed)
    return landed


def focus(cfg: dict[str, Any], window: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    """Give a window keyboard focus.

    `type` and `key` act on whatever is focused, so without this an agent's
    only way to redirect them is to click — which needs a frame, and moves the
    pointer as a side effect. The dispatcher takes a table keyed by `window`;
    a bare selector string is rejected outright.
    """
    safety.check_abort()
    safety.check_session()
    safety.check_denylist(cfg, window)
    if not dry_run:
        hypr.lua(f'hl.dispatch(hl.dsp.focus({{window = "address:{window["address"]}"}}))')
        safety.confirm_focus(window, observe.focused())
    result = {"action": "focus", "window": _describe(window), "dry_run": dry_run}
    safety.record(cfg, result)
    return result


def resolve_target(
    frame_id: str | None,
    at: tuple[float, float] | None,
    cfg: dict[str, Any],
) -> dict[str, Any]:
    """Everything an aimed command needs, re-read at call time.

    The snapshot is deliberately taken here rather than passed in. A caller
    holding a stale snapshot is the exact bug the frame contract exists to
    prevent, and it would be silently reintroduced by an argument.
    """
    snap = observe.snapshot()
    if frame_id is None or at is None:
        return {"snapshot": snap, "frame": None, "aimed": None}
    frame = frames.load(frame_id)
    safety.check_frame(cfg, frame, snap["windows"])
    return {"snapshot": snap, "frame": frame, "aimed": aim(frame, at[0], at[1], snap)}


def move(
    cfg: dict[str, Any], frame_id: str, at: tuple[float, float], *, dry_run: bool
) -> dict[str, Any]:
    target = resolve_target(frame_id, at, cfg)
    aimed = target["aimed"]
    safety.preflight_pointer(cfg, aimed["window_under_point"], target["frame"])
    point = aimed["logical"]
    landed = warp(cfg, point["x"], point["y"], dry_run=dry_run)
    result = {
        "action": "move",
        "frame": frame_id,
        "at": {"x": at[0], "y": at[1]},
        "logical": point,
        "cursor": landed,
        "relative": aimed["relative"],
        "window": _describe(aimed["window_under_point"]),
        "dry_run": dry_run,
    }
    safety.record(cfg, result)
    return result


def click(
    cfg: dict[str, Any],
    frame_id: str,
    at: tuple[float, float],
    *,
    button: str = "left",
    count: int = 1,
    dry_run: bool,
) -> dict[str, Any]:
    if count < 1:
        raise ValueError("click count must be at least 1")
    target = resolve_target(frame_id, at, cfg)
    aimed = target["aimed"]
    under = aimed["window_under_point"]
    safety.preflight_pointer(cfg, under, target["frame"])
    point = aimed["logical"]
    landed = warp(cfg, point["x"], point["y"], dry_run=dry_run)

    if not dry_run:
        with uinput.Device(settle_ms=cfg["input"]["settle_ms"]) as device:
            # Device enumeration is most of this command's latency. Re-read
            # after it, immediately before the button event, so a window that
            # appeared during the wait cannot inherit a click aimed at another.
            arrived, landed = _live_pointer_target(
                cfg, target["frame"], point, target["snapshot"]["monitors"]
            )
            device.click(button, cfg["input"]["press_ms"], count)
        under = arrived

    result = {
        "action": "click",
        "frame": frame_id,
        "at": {"x": at[0], "y": at[1]},
        "logical": point,
        "cursor": landed,
        "relative": _relative_to(under, point["x"], point["y"]),
        "button": button,
        "count": count,
        "window": _describe(under),
        "dry_run": dry_run,
    }
    safety.record(cfg, result)
    return result


def scroll(
    cfg: dict[str, Any],
    *,
    dy: int,
    dx: int,
    frame_id: str | None,
    at: tuple[float, float] | None,
    dry_run: bool,
) -> dict[str, Any]:
    """Wheel events, optionally aimed first.

    Aiming is optional because scrolling without moving the pointer is a real
    request — the wheel goes wherever the pointer already is, which is often
    exactly where the last click left it.
    """
    target = resolve_target(frame_id, at, cfg)
    aimed = target["aimed"]
    under = aimed["window_under_point"] if aimed else observe.window_at(
        target["snapshot"]["cursor_logical"]["x"],
        target["snapshot"]["cursor_logical"]["y"],
        target["snapshot"]["windows"],
    )
    safety.preflight_pointer(cfg, under, target["frame"])
    landed = target["snapshot"]["cursor_logical"]
    if aimed:
        landed = warp(cfg, aimed["logical"]["x"], aimed["logical"]["y"], dry_run=dry_run)
    if not dry_run:
        with uinput.Device(settle_ms=cfg["input"]["settle_ms"]) as device:
            point = aimed["logical"] if aimed else None
            under, landed = _live_pointer_target(
                cfg, target["frame"], point, target["snapshot"]["monitors"]
            )
            device.scroll(dy=dy, dx=dx)
    result = {
        "action": "scroll",
        "dy": dy,
        "dx": dx,
        "frame": frame_id,
        "relative": _relative_to(under, landed["x"], landed["y"]) if aimed else None,
        "cursor": landed,
        "window": _describe(under),
        "dry_run": dry_run,
    }
    safety.record(cfg, result)
    return result


class ClipboardError(RuntimeError):
    pass


def _clipboard_read() -> bytes | None:
    """The current selection, or None when there is nothing restorable.

    wl-paste exits non-zero on an empty clipboard, which is not an error worth
    propagating — it just means there is nothing to put back.
    """
    if not shutil.which("wl-paste"):
        return None
    proc = subprocess.run(["wl-paste", "--no-newline"], capture_output=True, check=False)
    return proc.stdout if proc.returncode == 0 else None


def _clipboard_write(data: bytes) -> None:
    """Put data on the clipboard, without waiting for a process that never exits.

    A Wayland selection is owned by a live process, so `wl-copy` forks a server
    and the parent returns. That daemon inherits whatever stdout and stderr it
    was given, so capturing them means waiting for EOF on a pipe the daemon
    holds open for the lifetime of the clipboard — the command appears to hang
    forever. The descriptors go to /dev/null instead; the parent's exit status
    still reports whether the copy was accepted.
    """
    if not shutil.which("wl-copy"):
        raise ClipboardError("wl-copy is not installed; use --method keys")
    proc = subprocess.run(
        ["wl-copy"],
        input=data,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=5,
        check=False,
    )
    if proc.returncode != 0:
        raise ClipboardError(f"wl-copy exited {proc.returncode}")


def _clipboard_clear() -> None:
    """Restore the absence of a selection rather than leaving typed text behind."""
    if not shutil.which("wl-copy"):
        raise ClipboardError("wl-copy is not installed; cannot clear the clipboard")
    proc = subprocess.run(
        ["wl-copy", "--clear"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=5,
        check=False,
    )
    if proc.returncode != 0:
        raise ClipboardError(f"wl-copy --clear exited {proc.returncode}")


def type_text(
    cfg: dict[str, Any],
    text: str,
    *,
    method: str = "clipboard",
    restore_clipboard: bool = True,
    backend: str | None = None,
    verify: bool = False,
    dry_run: bool,
) -> dict[str, Any]:
    """Enter text into whatever holds focus.

    Clipboard is the default because it is layout-independent and pays the
    device cost once regardless of length. Its two failure modes are known —
    applications that reject synthetic paste, and a paste chord this desktop
    has rebound — so `--method keys` exists, and the resolved chord comes from
    configuration rather than from a universal assumption.
    """
    backend = backend or cfg["input"]["key_backend"]
    snap = observe.snapshot()
    focus = snap["focused"]
    window = next((w for w in snap["windows"] if focus and w["address"] == focus["address"]), None)
    safety.preflight_keyboard(cfg, window, focus)
    if window is None:
        raise Refused(
            exits.TARGET_NOT_FOUND, "no window holds focus; nothing would receive the text"
        )

    chord = None
    if method == "clipboard":
        chord = config.chord_for(cfg, window["class"], "paste")
        if chord is None:
            raise Refused(
                exits.NOT_MAPPED,
                f"no paste binding known for {window['class']}; "
                f"set keys.\"{window['class']}\".paste or use --method keys",
            )

    result = {
        "action": "type",
        "method": method,
        "length": len(text),
        "chord": chord,
        "backend": backend,
        "window": _describe(window),
        "dry_run": dry_run,
    }

    if dry_run:
        safety.record(cfg, result)
        return result

    if method == "clipboard":
        can_restore = restore_clipboard and shutil.which("wl-paste") is not None
        previous = _clipboard_read() if can_restore else None
        _clipboard_write(text.encode())
        try:
            send_chords(cfg, [chord], backend)
            # The paste has to be consumed before the clipboard reverts;
            # restoring too eagerly pastes the *previous* contents.
            time.sleep(0.12)
            if verify:
                result["verified"] = read_back(cfg, window, text, backend)
        finally:
            if can_restore:
                _clipboard_write(previous) if previous is not None else _clipboard_clear()
    else:
        unmapped = [c for c in text if uinput.char_to_key(c) is None]
        if unmapped:
            raise Refused(
                exits.NOT_MAPPED,
                f"cannot synthesize {unmapped[0]!r} on a US layout; use --method clipboard",
            )
        send_chords(
            cfg,
            [f"shift+{name}" if shifted else name
             for name, shifted in (uinput.char_to_key(c) for c in text)],
            backend,
        )
        if verify:
            previous = _clipboard_read() if restore_clipboard else None
            try:
                result["verified"] = read_back(cfg, window, text, backend)
            finally:
                if previous is not None:
                    _clipboard_write(previous)

    safety.record(cfg, result)
    if verify and result.get("verified", {}).get("status") == "mismatch":
        raise Refused(
            exits.VERIFY_FAILED,
            f"{result['verified']['detail']}. The text was sent but did not "
            "arrive intact; read the field before retyping.",
        )
    return result


def read_back(
    cfg: dict[str, Any],
    window: dict[str, Any],
    expected: str,
    backend: str | None,
) -> dict[str, Any]:
    """Read a field's contents back and compare them to what was sent.

    Silent partial input is the failure both text methods share: a paste an
    application rejects, or a synthesized run whose leading characters were
    dropped, look exactly like success at the call site. Reading the field back
    is the only thing that distinguishes them.

    The comparison is containment rather than equality, because select-all
    returns whatever else the field already held. And a field that cannot be
    read back reports `unverified` rather than `ok` — claiming verification
    that did not happen would be worse than not offering it.
    """
    select = config.chord_for(cfg, window["class"], "select-all")
    copy = config.chord_for(cfg, window["class"], "copy")
    if not select or not copy:
        return {
            "status": "unverified",
            "detail": f"no select-all or copy binding known for {window['class']}",
        }
    _clipboard_write(b"")
    send_chords(cfg, [select, copy], backend)
    time.sleep(0.12)
    got = (_clipboard_read() or b"").decode(errors="replace")
    return compare_text(expected, got)


def compare_text(expected: str, got: str) -> dict[str, Any]:
    """Judge a read-back against what was sent.

    Containment rather than equality, because select-all returns whatever else
    the field already held — a URL bar with existing text is not a failure. The
    count of leading characters that agree is reported because it distinguishes
    the two ways this goes wrong: nothing arrived, or the beginning was eaten
    while the rest landed.
    """
    if not got:
        return {
            "status": "unverified",
            "detail": "the field returned nothing; it may not support select-all",
        }
    if expected in got:
        return {"status": "ok", "detail": f"field contains all {len(expected)} characters"}
    shared = 0
    for sent, arrived in zip(expected, got, strict=False):
        if sent != arrived:
            break
        shared += 1
    return {
        "status": "mismatch",
        "detail": (
            f"sent {len(expected)} characters, field holds {len(got)}; "
            f"they agree for the first {shared}"
        ),
        "got_length": len(got),
    }


def browser_action(
    cfg: dict[str, Any],
    target: dict[str, Any],
    action: str,
    *,
    backend: str | None = None,
    dry_run: bool,
) -> dict[str, Any]:
    """Send a semantic browser action, logged by its name rather than its chord.

    Which matters for what happens later: a recipe built from this log must
    replay the *action*, so that re-resolving it picks up a keymap the user has
    since changed. A recorded chord would keep firing the old binding, which by
    then belongs to some other command.
    """
    from . import browser as browser_module

    chord, source = browser_module.resolve(target, action, cfg)
    if chord is None:
        raise Refused(
            exits.NOT_MAPPED,
            f"{target['name']} has no binding for {action!r}; "
            "add one under [keys] rather than guessing a chord",
        )
    result = key(cfg, action=None, chord=chord, backend=backend, dry_run=dry_run, record=False)
    result.update(
        {"action": "browser", "semantic": action, "browser": target["name"],
         "resolved_from": source}
    )
    safety.record(cfg, result)
    return result


def key(
    cfg: dict[str, Any],
    *,
    action: str | None,
    chord: str | None,
    repeat: int = 1,
    backend: str | None = None,
    record: bool = True,
    dry_run: bool,
) -> dict[str, Any]:
    """Send a semantic action, or a literal chord when the caller insists.

    An action with no mapping for the focused application is refused. Falling
    back to a documented default is the failure this project exists to
    prevent: the keystroke would arrive, and would do something else.
    """
    if repeat < 1:
        raise ValueError("key repeat must be at least 1")
    backend = backend or cfg["input"]["key_backend"]
    snap = observe.snapshot()
    focus = snap["focused"]
    window = next((w for w in snap["windows"] if focus and w["address"] == focus["address"]), None)
    safety.preflight_keyboard(cfg, window, focus)

    resolved = chord
    if action is not None:
        if window is None:
            raise Refused(exits.TARGET_NOT_FOUND, "no window holds focus; an action has no target")
        resolved = config.chord_for(cfg, window["class"], action)
        if resolved is None:
            raise Refused(
                exits.NOT_MAPPED,
                f"no binding for {action!r} in {window['class']}; "
                f"add keys.\"{window['class']}\".{action} rather than guessing a chord",
            )
    if resolved is None:
        raise Refused(exits.USAGE, "give either --action or --chord")

    uinput.parse_chord(resolved)  # fail before the device exists, not after
    if not dry_run:
        send_chords(cfg, [resolved] * repeat, backend)

    result = {
        "action": "key",
        "semantic": action,
        "chord": resolved,
        "repeat": repeat,
        "backend": backend,
        "window": _describe(window),
        "dry_run": dry_run,
    }
    if record:
        safety.record(cfg, result)
    return result


def _relative_to(
    window: dict[str, Any] | None, x: float, y: float
) -> dict[str, float] | None:
    if window is None:
        return None
    rect = window["logical_rect"]
    if rect["w"] <= 0 or rect["h"] <= 0:
        return None
    return {"x": (x - rect["x"]) / rect["w"], "y": (y - rect["y"]) / rect["h"]}


def _live_pointer_target(
    cfg: dict[str, Any],
    frame: dict[str, Any] | None,
    expected: dict[str, float] | None,
    monitors: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, dict[str, float]]:
    """Reassert pointer position and target immediately before an event."""
    cursor = observe.cursor()
    if expected is not None:
        safety.confirm_pointer(cfg, (expected["x"], expected["y"]), cursor)
    under = observe.window_at(cursor["x"], cursor["y"], observe.windows(monitors))
    safety.preflight_pointer(cfg, under, frame)
    return under, cursor


def _describe(window: dict[str, Any] | None) -> dict[str, Any] | None:
    if window is None:
        return None
    described = {
        "address": window["address"],
        "class": window["class"],
        "title": window["title"],
    }
    if "logical_rect" in window:
        described["logical_rect"] = window["logical_rect"]
    return described
