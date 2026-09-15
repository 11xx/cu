"""Hyprland IPC.

Hyprland 0.56 replaced the classic `hyprctl dispatch <name> <args>` string form
with a Lua API, so anything that acts goes through `repl`. Read-only queries
still have stable JSON subcommands and are used directly.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
from pathlib import Path


class HyprError(RuntimeError):
    pass


def available() -> bool:
    return shutil.which("hyprctl") is not None and bool(signature())


def signature() -> str | None:
    return os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")


def socket_dir() -> Path | None:
    sig, runtime = signature(), os.environ.get("XDG_RUNTIME_DIR")
    if not sig or not runtime:
        return None
    return Path(runtime) / "hypr" / sig


def event_socket() -> Path | None:
    d = socket_dir()
    return d / ".socket2.sock" if d else None


def _run(args: list[str], timeout: float = 5.0) -> str:
    try:
        proc = subprocess.run(
            ["hyprctl", *args], capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HyprError(f"hyprctl {' '.join(args)}: {exc}") from exc
    if proc.returncode != 0:
        raise HyprError(f"hyprctl {' '.join(args)}: {proc.stderr.strip()}")
    return proc.stdout


def request_socket() -> Path | None:
    d = socket_dir()
    return d / ".socket.sock" if d else None


def _request(request: str, timeout: float = 5.0) -> str:
    """Send one request on the compositor's command socket; the reply ends at EOF.

    The socket answers in a fraction of a millisecond where spawning `hyprctl`
    costs several, and native movement reads the desktop between every event.
    """
    path = request_socket()
    if path is None or not path.is_socket():
        raise HyprError("the Hyprland request socket is unavailable")
    chunks = []
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(timeout)
            conn.connect(str(path))
            conn.sendall(request.encode())
            while chunk := conn.recv(65536):
                chunks.append(chunk)
    except OSError as exc:
        raise HyprError(f"hyprland request {request!r}: {exc}") from exc
    return b"".join(chunks).decode()


def query(what: str) -> object:
    """Run a read-only JSON query such as `monitors` or `clients`."""
    return batch([what])[0]


def batch(queries: list[str]) -> list[object]:
    """Run several read-only JSON queries in one compositor request, in order."""
    if len(queries) == 1:
        raw = _request(f"j/{queries[0]}")
    else:
        raw = _request("[[BATCH]]" + ";".join(f"j/{what}" for what in queries))
    decoder = json.JSONDecoder()
    results: list[object] = []
    index = 0
    while len(results) < len(queries):
        while index < len(raw) and raw[index].isspace():
            index += 1
        try:
            value, index = decoder.raw_decode(raw, index)
        except json.JSONDecodeError as exc:
            raise HyprError(
                f"hyprland {' '.join(queries)} returned non-JSON: {raw[:200]!r}"
            ) from exc
        results.append(value)
    return results


def lua(code: str) -> str:
    """Evaluate Lua in the compositor and return its printed result.

    `hyprctl repl` reports Lua errors on stdout with a zero exit status, so the
    result is inspected rather than trusted.
    """
    out = _run(["repl", code]).strip()
    if out.startswith("error:"):
        raise HyprError(out)
    return out


def move_cursor(x: float, y: float) -> None:
    """Warp the pointer to an absolute position in global logical coordinates.

    One spelling, because four plausible variants of this call return success
    and move nothing: positional arguments, the old `"x y"` string, its
    `"exact x y"` form, and constructing the dispatcher without dispatching it.
    Wrapping it here is the whole reason this layer exists — a caller gets the
    form that works rather than five that look alike at the call site.
    """
    lua(f"hl.dispatch(hl.dsp.cursor.move({{x = {x:.4f}, y = {y:.4f}}}))")


def version() -> str:
    """The release only. The full first line carries branch, commit, and date."""
    if not available():
        return ""
    first = _run(["version"]).splitlines()[0].strip()
    return first.split(" built from")[0].strip()
