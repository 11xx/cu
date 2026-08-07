"""Preconditions, each one a fact the rest of `cu` depends on.

Everything here is re-checked rather than assumed, because most of these are
properties of a running system that can drift: a permission granted by an ACL,
a remapper's device filter, a compositor API that changed shape between
releases.
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Any

from . import hypr

OK, WARN, FAIL = "ok", "warn", "fail"


def _check(name: str, status: str, detail: str, remedy: str = "") -> dict[str, Any]:
    return {"name": name, "status": status, "detail": detail, "remedy": remedy}


def compositor() -> dict[str, Any]:
    if not shutil.which("hyprctl"):
        return _check("compositor", FAIL, "hyprctl not on PATH", "Install Hyprland.")
    if not hypr.signature():
        return _check(
            "compositor",
            FAIL,
            "HYPRLAND_INSTANCE_SIGNATURE unset",
            "Run inside a Hyprland session.",
        )
    try:
        return _check("compositor", OK, hypr.version())
    except hypr.HyprError as exc:
        return _check("compositor", FAIL, str(exc), "Is the compositor running?")


def lua_api() -> dict[str, Any]:
    """Hyprland 0.56 moved dispatchers to Lua; the old string form is a parse error.

    Both halves are checked because `hl.dsp.*` only builds a dispatcher — it is
    `hl.dispatch` that runs it, and a missing executor would leave every action
    silently doing nothing.
    """
    probe = 'return type(hl.dispatch) .. "," .. type(hl.dsp.cursor.move)'
    try:
        kinds = hypr.lua(probe)
    except hypr.HyprError as exc:
        return _check(
            "lua_api",
            FAIL,
            f"hyprctl repl unusable: {exc}",
            "Requires Hyprland 0.56+ with the Lua API.",
        )
    if kinds != "function,function":
        return _check(
            "lua_api",
            FAIL,
            f"expected hl.dispatch and hl.dsp.cursor.move to be functions, got {kinds!r}",
            "The compositor API changed shape; cu needs updating.",
        )
    return _check("lua_api", OK, "hl.dispatch and hl.dsp.cursor.move present")


def event_socket() -> dict[str, Any]:
    sock = hypr.event_socket()
    if sock is None:
        return _check("event_socket", WARN, "XDG_RUNTIME_DIR or signature unset")
    if not sock.is_socket():
        return _check(
            "event_socket", WARN, f"{sock} missing", "`cu wait` will be unavailable."
        )
    return _check("event_socket", OK, str(sock))


def uinput() -> dict[str, Any]:
    """Injection needs write access to /dev/uinput, normally via the input group."""
    path = "/dev/uinput"
    if not Path(path).exists():
        return _check(
            "uinput", FAIL, f"{path} missing", "Load the uinput module: modprobe uinput."
        )
    try:
        fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
    except OSError as exc:
        return _check(
            "uinput",
            FAIL,
            f"{path} not writable as uid {os.getuid()}: {exc.strerror}",
            "Add the user to the `input` group, or install a udev rule granting access.",
        )
    os.close(fd)
    return _check("uinput", OK, f"writable as uid {os.getuid()}, no root needed")


#: How long a /proc scan may be reused. Long enough that the remapper filter
#: and the lock-screen check share one scan within a single injection — they
#: are meant to describe the same instant, and scanning twice cost about half
#: the latency of a dry-run command. Short enough that a replay, which injects
#: repeatedly over minutes, re-reads before each action rather than deciding
#: the whole run against the moment it started.
PROCESS_SCAN_TTL = 1.0

_process_scan: tuple[float, list[tuple[int, list[str]]]] | None = None


def _processes() -> list[tuple[int, list[str]]]:
    """Every readable process as (pid, argv). Ignores races against exiting pids.

    A lock screen or an unfiltered remapper appearing mid-run has to be seen:
    both guards exist for the case where the world changes under an agent, so
    the answer is dated and expires rather than being fixed at process start.
    """
    global _process_scan
    now = time.monotonic()
    if _process_scan is not None and now - _process_scan[0] < PROCESS_SCAN_TTL:
        return _process_scan[1]
    found = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            argv = (entry / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        args = [a.decode(errors="replace") for a in argv if a]
        if args:
            found.append((int(entry.name), args))
    _process_scan = (now, found)
    return found


def xremap_filter() -> dict[str, Any]:
    """An unfiltered xremap grabs virtual keyboards and silently rewrites injection.

    The filter is a property of the running process, not of the config, so the
    live argv is what gets read.
    """
    procs = [(pid, args) for pid, args in _processes() if Path(args[0]).name == "xremap"]
    if not procs:
        return _check("xremap_filter", OK, "xremap not running; no remapping applies")
    unfiltered = [pid for pid, args in procs if "--device" not in args]
    if unfiltered:
        return _check(
            "xremap_filter",
            FAIL,
            f"xremap pid {unfiltered[0]} runs without --device; injected keys would be remapped",
            "Restart xremap bound to the physical keyboard, or stop it before injecting.",
        )
    devices = [args[args.index("--device") + 1] for _, args in procs]
    return _check("xremap_filter", OK, f"filtered to {', '.join(devices)}")


LOCKERS = {"hyprlock", "swaylock", "gtklock", "waylock"}


def session_unlocked() -> dict[str, Any]:
    for _, args in _processes():
        locker = Path(args[0]).name
        if locker in LOCKERS:
            return _check(
                "session_unlocked",
                FAIL,
                f"{locker} is running",
                "Refusing to act on a locked session.",
            )
    return _check("session_unlocked", OK, "no lock screen running")


def abort_flag() -> dict[str, Any]:
    """A set abort flag refuses every injection, and is otherwise invisible.

    Without this check the symptom is every input command failing with the same
    code and no indication why — which is exactly the state a user leaves
    behind after aborting an agent and walking away.
    """
    from . import config

    try:
        path = config.abort_path()
    except config.ConfigError as exc:
        return _check("abort_flag", FAIL, str(exc))
    if path.exists():
        return _check(
            "abort_flag",
            WARN,
            f"set at {path}; every input command will refuse",
            "The user clears it; cu never does.",
        )
    return _check("abort_flag", OK, "clear")


def key_dispatcher() -> dict[str, Any]:
    """The compositor keyboard backend, which drives a sleeping desktop.

    Optional rather than required: without it uinput still works, and only the
    quiet path is unavailable.
    """
    try:
        kind = hypr.lua("return type(hl.dsp.send_key_state)")
    except hypr.HyprError as exc:
        return _check("key_dispatcher", WARN, str(exc), "`--backend compositor` unavailable.")
    if kind != "function":
        return _check(
            "key_dispatcher",
            WARN,
            f"hl.dsp.send_key_state is {kind!r}",
            "`--backend compositor` unavailable; uinput still works.",
        )
    return _check("key_dispatcher", OK, "hl.dsp.send_key_state present")


def binaries() -> list[dict[str, Any]]:
    required = {"grim": "Screen capture is impossible without it."}
    optional = {
        "wl-copy": "`cu type` falls back to keycode synthesis.",
        "wl-paste": "Clipboard restore after `cu type` is unavailable.",
        "slurp": "Interactive region selection is unavailable.",
    }
    out = []
    for name, why in required.items():
        path = shutil.which(name)
        out.append(
            _check(f"bin:{name}", OK if path else FAIL, path or "not on PATH", why)
        )
    for name, why in optional.items():
        path = shutil.which(name)
        out.append(
            _check(f"bin:{name}", OK if path else WARN, path or "not on PATH", why)
        )
    return out


def runtime_dir() -> dict[str, Any]:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime:
        return _check(
            "runtime_dir",
            FAIL,
            "XDG_RUNTIME_DIR unset",
            "Frames have nowhere private and tmpfs-backed to live.",
        )
    return _check("runtime_dir", OK, runtime)


def run_all() -> list[dict[str, Any]]:
    results = [compositor()]
    if results[0]["status"] != FAIL:
        results += [lua_api(), key_dispatcher(), event_socket()]
    results += [runtime_dir(), uinput(), xremap_filter(), session_unlocked(), abort_flag()]
    results += binaries()
    return results


def worst(results: list[dict[str, Any]]) -> str:
    statuses = {r["status"] for r in results}
    return FAIL if FAIL in statuses else WARN if WARN in statuses else OK
