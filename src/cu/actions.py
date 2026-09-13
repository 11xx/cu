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
from collections.abc import Callable
from typing import Any

from . import compositor, config, exits, frames, hypr, observe, safety, uinput
from .safety import Refused


def send_chords(
    cfg: dict[str, Any],
    chords: list[str],
    backend: str,
    *,
    before_send: Callable[[], None] | None = None,
) -> None:
    """Emit each chord through the selected backend.

    The uinput device is built once for the whole sequence: constructing it
    costs about 100 ms, which is most of the price of an input command, and
    paying that per keystroke is what makes a batched command worth having.
    """
    if backend == "compositor":
        for chord in chords:
            if before_send:
                before_send()
            compositor.send_chord(chord)
        return
    with uinput.Device(settle_ms=cfg["input"]["settle_ms"]) as device:
        for index, chord in enumerate(chords):
            if index:
                time.sleep(cfg["input"]["gap_ms"] / 1000)
            if before_send:
                before_send()
            device.chord(chord, cfg["input"]["press_ms"])


def aim(frame: dict[str, Any], x: float, y: float, snap: dict[str, Any]) -> dict[str, Any]:
    """Resolve an image coordinate to a logical point and the geometry covering it.

    The window matters even when the caller named none: the denylist has to
    apply to the thing actually being clicked, which on a full-output frame is
    whatever happens to be at that point.
    """
    lx, ly = frames.to_logical(frame, x, y)
    declared = frame.get("window")
    covering = observe.windows_at(lx, ly, snap["windows"])
    under = min(covering, key=lambda window: window["focus_order"], default=None)
    layer_surface = observe.layer_surface_at(lx, ly, snap.get("layer_surfaces", []))
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
        "covering_windows": covering,
        "layer_surface": layer_surface,
        "target_window": frame.get("target_window"),
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
    safety.check_frame(cfg, frame, snap["windows"], snap["monitors"])
    return {"snapshot": snap, "frame": frame, "aimed": aim(frame, at[0], at[1], snap)}


def move(
    cfg: dict[str, Any], frame_id: str, at: tuple[float, float], *, dry_run: bool
) -> dict[str, Any]:
    target = resolve_target(frame_id, at, cfg)
    aimed = target["aimed"]
    safety.preflight_pointer(
        cfg,
        aimed["window_under_point"],
        target["frame"],
        covering=aimed["covering_windows"],
        layer_surface=aimed["layer_surface"],
    )
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
        "target_window": _describe(aimed["target_window"]),
        "target_assertion": "bound" if aimed["target_window"] is not None else "unbound",
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
    result = _aimed_click(cfg, frame_id, at, button=button, count=count, dry_run=dry_run)
    safety.record(cfg, _loggable(result))
    return _loggable(result)


def _loggable(result: dict[str, Any]) -> dict[str, Any]:
    """The parts of a result that belong to the caller and the log.

    An aimed action carries the frame descriptor it resolved so a later step
    can re-assert it without reloading a frame that may since have been
    collected. That is machinery, not evidence, and the log is evidence.
    """
    return {key: value for key, value in result.items() if not key.startswith("_")}


def _aimed_click(
    cfg: dict[str, Any],
    frame_id: str,
    at: tuple[float, float],
    *,
    button: str = "left",
    count: int = 1,
    dry_run: bool,
) -> dict[str, Any]:
    """The click itself, unrecorded, so a larger action can own the log entry.

    A click that only exists to select the widget a following keystroke is
    aimed at is not a separate thing that happened, and logging it as one
    would promote into a recipe that replays the gap this exists to close.
    """
    if count < 1:
        raise ValueError("click count must be at least 1")
    target = resolve_target(frame_id, at, cfg)
    aimed = target["aimed"]
    under = aimed["window_under_point"]
    layer_surface = aimed["layer_surface"]
    safety.preflight_pointer(
        cfg,
        under,
        target["frame"],
        covering=aimed["covering_windows"],
        layer_surface=aimed["layer_surface"],
    )
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
        layer_surface = (
            observe.layer_surface_at(landed["x"], landed["y"], observe.layer_surfaces())
            if under is None
            else None
        )

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
        "target_window": _describe(aimed["target_window"]),
        "target_assertion": "bound" if aimed["target_window"] is not None else "unbound",
        "window_size": (
            {axis: under["logical_rect"][axis] for axis in ("w", "h")}
            if under and under.get("logical_rect")
            else None
        ),
        "layer_surface": layer_surface,
        "dry_run": dry_run,
        "_frame": target["frame"],
    }
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
    safety.preflight_pointer(
        cfg,
        under,
        target["frame"],
        covering=aimed["covering_windows"] if aimed else None,
        layer_surface=aimed["layer_surface"] if aimed else None,
    )
    landed = target["snapshot"]["cursor_logical"]
    if aimed:
        landed = warp(cfg, aimed["logical"]["x"], aimed["logical"]["y"], dry_run=dry_run)
    if not dry_run:
        with uinput.Device(settle_ms=cfg["input"]["settle_ms"]) as device:
            point = aimed["logical"] if aimed else target["snapshot"]["cursor_logical"]
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
        "target_window": _describe(aimed["target_window"]) if aimed else None,
        "target_assertion": (
            "bound" if aimed and aimed["target_window"] is not None else "unbound"
        ),
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


def _mark_injected(injected: list[bool] | None) -> None:
    """Say that keystrokes have gone out, whatever happens after."""
    if injected is not None:
        injected.append(True)


def _record_orphaned_click(cfg: dict[str, Any], pointer: dict[str, Any]) -> None:
    """Log a click that happened while the action it belonged to did not.

    An aimed type owns its click so the pair promotes as one step, which means
    nothing else writes that click down. When the typing half refuses, the
    click still moved the pointer and pressed a button on someone's desktop,
    and the log is the only record of what an agent did while nobody watched.
    """
    if pointer.get("dry_run"):
        return
    safety.record(cfg, {**_loggable(pointer), "action": "click", "typed": False})


def type_text(
    cfg: dict[str, Any],
    text: str,
    *,
    method: str = "clipboard",
    restore_clipboard: bool = True,
    backend: str | None = None,
    verify: bool = False,
    replace: bool = False,
    frame_id: str | None = None,
    at: tuple[float, float] | None = None,
    button: str = "left",
    count: int = 1,
    dry_run: bool,
) -> dict[str, Any]:
    """Enter text into whatever holds focus, or into a target it aims at first.

    Clipboard is the default because it is layout-independent and pays the
    device cost once regardless of length. Its two failure modes are known —
    applications that reject synthetic paste, and a paste chord this desktop
    has rebound — so `--method keys` exists, and the resolved chord comes from
    configuration rather than from a universal assumption.

    Given a frame and a point, the aim belongs to this command rather than to
    a preceding one. Two commands hold the desktop lock twice, and between
    them focus can move inside the same window, or the page can replace the
    control, while the window's address and title stand still — after which
    typing validates the window it was always going to validate and puts the
    text somewhere else. Owning the click makes the target, the focus
    transition, the injection, and the read-back one transaction.
    """
    backend = backend or cfg["input"]["key_backend"]
    if replace and not text:
        raise ValueError("--replace requires nonempty text; empty fields cannot be copied reliably")
    if (frame_id is None) != (at is None):
        raise ValueError("aimed typing needs both a frame and a point")

    if frame_id is None or at is None:
        return _type_into_focus(
            cfg, text, method=method, restore_clipboard=restore_clipboard,
            backend=backend, verify=verify, replace=replace, pointer=None, dry_run=dry_run,
        )

    pointer = _aimed_click(cfg, frame_id, at, button=button, count=count, dry_run=dry_run)
    # Set the moment the text is sent, not when it is written down. What the
    # click needs its own record for is being the only thing that happened;
    # once keystrokes are on their way the action is the typing, and every
    # later failure — a read-back that did not match, a clipboard that would
    # not restore — belongs to it.
    injected: list[bool] = []
    try:
        return _type_into_focus(
            cfg, text, method=method, restore_clipboard=restore_clipboard,
            backend=backend, verify=verify, replace=replace, pointer=pointer, injected=injected,
            dry_run=dry_run,
        )
    except BaseException:
        # The click already happened on someone's desktop. If the text never
        # got as far as being written down, the log is otherwise silent about
        # a pointer that moved and a button that went down.
        if not injected:
            _record_orphaned_click(cfg, pointer)
        raise


def _type_into_focus(
    cfg: dict[str, Any],
    text: str,
    *,
    method: str,
    restore_clipboard: bool,
    backend: str | None,
    verify: bool,
    replace: bool,
    pointer: dict[str, Any] | None,
    dry_run: bool,
    injected: list[bool] | None = None,
) -> dict[str, Any]:
    """Enter text into whatever holds focus, asserting an aim when there was one."""
    backend = backend or cfg["input"]["key_backend"]
    if pointer is not None and pointer["window"] is None:
        # An aim that landed on no window cannot say which field was selected,
        # and the focus assertion below would have nothing to compare against
        # — so the text would go wherever focus already was, which is the
        # failure this command exists to prevent.
        raise Refused(
            exits.TARGET_NOT_FOUND,
            "the aimed point is on no window, so nothing there can receive "
            "the text; re-observe and aim inside the field's window",
        )

    snap = observe.snapshot()
    focus = snap["focused"]
    window = next((w for w in snap["windows"] if focus and w["address"] == focus["address"]), None)
    safety.preflight_keyboard(cfg, window, focus)
    if window is None:
        raise Refused(
            exits.TARGET_NOT_FOUND, "no window holds focus; nothing would receive the text"
        )
    if pointer is not None and not dry_run:
        # Clicking a window is how it takes focus, so a click that did not
        # produce it means the text would land somewhere nobody aimed at.
        aimed_at = pointer["window"]["address"]
        if window["address"] != aimed_at:
            raise Refused(
                exits.FOCUS_CHANGED,
                f"the click landed on {pointer['window']['class']} but "
                f"{window['class']} holds keyboard focus; nothing was typed",
            )
        # A click can navigate as well as select. Asking again here is what
        # keeps the text out of a document that replaced the one aimed at
        # between the plan and the keystroke.
        safety.check_content(cfg, pointer["_frame"], snap["windows"])

    chord = None
    if method == "clipboard":
        chord = config.chord_for(cfg, window["class"], "paste")
        if chord is None:
            raise Refused(
                exits.NOT_MAPPED,
                f"no paste binding known for {window['class']}; "
                f"set keys.\"{window['class']}\".paste or use --method keys",
            )

    select = config.chord_for(cfg, window["class"], "select-all") if replace else None
    if replace and select is None:
        raise Refused(exits.NOT_MAPPED, f"no select-all binding known for {window['class']}")
    if select:
        uinput.parse_chord(select)

    result = {
        "action": "type",
        "replace": replace,
        "method": method,
        "length": len(text),
        "chord": chord,
        "backend": backend,
        "window": _describe(window),
        "target_window": pointer.get("target_window") if pointer is not None else None,
        "target_assertion": (
            pointer.get("target_assertion", "unbound") if pointer is not None else "unbound"
        ),
        "dry_run": dry_run,
    }
    if pointer is not None:
        # Both halves of the transaction, so the log and the JSON say where
        # the text was aimed as well as what happened to it.
        result["pointer"] = {
            "frame": pointer["frame"],
            "at": pointer["at"],
            "logical": pointer["logical"],
            "relative": pointer["relative"],
            "button": pointer["button"],
            "count": pointer["count"],
            "window": pointer["window"],
            "window_size": pointer["window_size"],
            "target_window": pointer.get("target_window"),
            "target_assertion": pointer.get("target_assertion", "unbound"),
        }

    if dry_run:
        safety.record(cfg, result)
        if injected is not None:
            injected.append(True)  # nothing was sent, and nothing is unrecorded
        return result

    if method == "clipboard":
        can_restore = restore_clipboard and shutil.which("wl-paste") is not None
        previous = _clipboard_read() if can_restore else None
        _clipboard_write(text.encode())
        try:
            send_chords(
                cfg,
                [select, chord] if select else [chord],
                backend,
                before_send=lambda: _recheck_keyboard(cfg, window),
            )
            _mark_injected(injected)
            # The paste has to be consumed before the clipboard reverts;
            # restoring too eagerly pastes the *previous* contents.
            time.sleep(0.12)
            if verify:
                result["verified"] = (
                    read_back(cfg, window, text, backend, exact=True) if replace
                    else read_back(cfg, window, text, backend)
                )
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
            ([select] if select else []) + [
                f"shift+{name}" if shifted else name
                for name, shifted in (uinput.char_to_key(c) for c in text)
            ],
            backend,
            before_send=lambda: _recheck_keyboard(cfg, window),
        )
        _mark_injected(injected)
        if verify:
            previous = _clipboard_read() if restore_clipboard else None
            try:
                result["verified"] = (
                    read_back(cfg, window, text, backend, exact=True) if replace
                    else read_back(cfg, window, text, backend)
                )
            finally:
                if restore_clipboard:
                    _clipboard_write(previous) if previous is not None else _clipboard_clear()

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
    *,
    exact: bool = False,
) -> dict[str, Any]:
    """Read a field's contents back and compare them to what was sent.

    Silent partial input is the failure both text methods share: a paste an
    application rejects, or a synthesized run whose leading characters were
    dropped, look exactly like success at the call site. Reading the field back
    is the only thing that distinguishes them.

    Insertion uses containment; explicit replacement requires equality.
    Select-all returns whatever else the field already held. And a field that cannot be
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
    send_chords(
        cfg,
        [select, copy],
        backend,
        before_send=lambda: _recheck_keyboard(cfg, window),
    )
    time.sleep(0.12)
    got = (_clipboard_read() or b"").decode(errors="replace")
    return compare_text(expected, got, exact=exact)


def compare_text(expected: str, got: str, *, exact: bool = False) -> dict[str, Any]:
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
    matches = expected == got if exact else expected in got
    if matches:
        verb = "equals" if exact else "contains all"
        return {"status": "ok", "detail": f"field {verb} {len(expected)} characters"}
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

    snap = observe.snapshot()
    focus = snap["focused"]
    current = browser_module.for_window_class(focus["class"]) if focus else None
    if current is None or current["id"] != target["id"]:
        got = focus["class"] if focus else "nothing"
        raise Refused(
            exits.FOCUS_CHANGED,
            f"expected {target['name']} to hold focus, but {got} does",
        )
    window = next((w for w in snap["windows"] if w["address"] == focus["address"]), None)
    if window is None:
        raise Refused(exits.FOCUS_CHANGED, f"{target['name']} closed before the action")
    chord, source = browser_module.resolve(target, action, cfg)
    if chord is None:
        raise Refused(
            exits.NOT_MAPPED,
            f"{target['name']} has no binding for {action!r}; "
            "add one under [keys] rather than guessing a chord",
        )
    result = key(
        cfg,
        action=None,
        chord=chord,
        backend=backend,
        expected_window=window,
        dry_run=dry_run,
        record=False,
    )
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
    expected_window: dict[str, Any] | None = None,
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
    if expected_window is not None:
        safety.confirm_focus(expected_window, focus)
    safety.preflight_keyboard(cfg, window, focus)
    if window is None:
        raise Refused(exits.TARGET_NOT_FOUND, "no window holds focus; nothing would receive keys")

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
        send_chords(
            cfg,
            [resolved] * repeat,
            backend,
            before_send=lambda: _recheck_keyboard(cfg, window),
        )

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
    if frame is not None and frame.get("window") is None:
        monitors = observe.monitors()
    windows = observe.windows(monitors)
    covering = observe.windows_at(cursor["x"], cursor["y"], windows)
    under = min(covering, key=lambda window: window["focus_order"], default=None)
    layer_surface = None
    if frame is not None and frame.get("target_window") is not None:
        layer_surface = observe.layer_surface_at(
            cursor["x"], cursor["y"], observe.layer_surfaces()
        )
    safety.preflight_pointer(
        cfg,
        under,
        frame,
        covering=covering,
        layer_surface=layer_surface,
    )
    if frame is not None:
        safety.check_frame(cfg, frame, windows, monitors)
    return under, cursor


def _recheck_keyboard(cfg: dict[str, Any], window: dict[str, Any] | None) -> None:
    """Reassert focus after device settlement, immediately before a chord."""
    live = observe.focused()
    safety.preflight_keyboard(cfg, live, live)
    safety.check_focus(cfg, window, live)


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
