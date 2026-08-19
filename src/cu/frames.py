"""The frame store, and the transform every later coordinate depends on.

A frame records only the logical rectangle it depicts and the pixel size it
depicts it with. Output scale, downscale ratio, crop origin, and monitor
position collapse into that one ratio, so a window crop and a full output share
a single transform rather than three that can disagree.

`cu` has no daemon, so frames live on disk. XDG_RUNTIME_DIR is tmpfs, 0700, and
cleared on reboot — the right lifetime for data that is worthless the moment
the screen changes.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

#: A frame older than this no longer describes the screen with any confidence.
DEFAULT_MAX_AGE = 60.0

#: Retention for the store as a whole, independent of usability.
GC_MAX_AGE = 900.0
GC_MAX_FRAMES = 60

FRAME_ID_PATTERN = re.compile(r"^f[1-9][0-9]*$")


class FrameError(RuntimeError):
    pass


def store_dir() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime:
        raise FrameError("XDG_RUNTIME_DIR is unset; frames have nowhere to live")
    path = Path(runtime) / "cu" / "frames"
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def _next_id() -> str:
    """Allocate a short, monotonic id.

    Short because an agent types it back; monotonic because reuse would let a
    stale reference silently resolve to an unrelated frame. The lock makes
    concurrent `cu shot` calls safe.
    """
    counter = store_dir() / "counter"
    fd = os.open(counter, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        raw = os.read(fd, 32).decode().strip()
        value = int(raw) + 1 if raw.isdigit() else 1
        os.lseek(fd, 0, os.SEEK_SET)
        os.ftruncate(fd, 0)
        os.write(fd, str(value).encode())
        return f"f{value}"
    finally:
        os.close(fd)


def validate_id(frame_id: str) -> str:
    if not FRAME_ID_PATTERN.fullmatch(frame_id):
        raise FrameError(f"invalid frame id: {frame_id!r}")
    return frame_id


def path_for(frame_id: str, suffix: str) -> Path:
    validate_id(frame_id)
    return store_dir() / f"{frame_id}{suffix}"


def save(descriptor: dict[str, Any]) -> dict[str, Any]:
    path = path_for(descriptor["id"], ".json")
    fd, temporary = tempfile.mkstemp(prefix=f".{descriptor['id']}-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(descriptor, handle, indent=2)
        Path(temporary).replace(path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return descriptor


def load(frame_id: str) -> dict[str, Any]:
    meta = path_for(frame_id, ".json")
    if not meta.exists():
        raise FrameError(f"no such frame: {frame_id}")
    try:
        descriptor = json.loads(meta.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise FrameError(f"cannot read frame {frame_id}: {exc}") from exc
    if not isinstance(descriptor, dict) or descriptor.get("id") != frame_id:
        raise FrameError(f"frame {frame_id} has an invalid descriptor")
    return descriptor


@contextmanager
def capture_scratch() -> Iterator[Path]:
    """A private path for one capture, removed unless `new` consumes it."""
    fd, name = tempfile.mkstemp(prefix=".pending-", suffix=".png", dir=store_dir())
    os.close(fd)
    path = Path(name)
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)


def new(
    source: dict[str, Any],
    logical_rect: dict[str, float],
    image_size: dict[str, int],
    scratch: Path,
    **extra: Any,
) -> dict[str, Any]:
    """Name a new frame image and record what it depicts.

    The image is written to a scratch path because its id does not exist until
    here, and moving it is part of allocating that id rather than something
    each caller repeats — a descriptor whose `path` disagrees with where the
    PNG landed would resolve to some other frame entirely.
    """
    frame_id = _next_id()
    final = path_for(frame_id, ".png")
    scratch.replace(final)
    return save(
        {
            "id": frame_id,
            "source": source,
            "logical_rect": logical_rect,
            "image_size": image_size,
            "path": str(final),
            "captured_at": time.time(),
            **extra,
        }
    )


def to_logical(frame: dict[str, Any], x: float, y: float) -> tuple[float, float]:
    """Map a coordinate in the frame's image to global logical space.

    This is the whole contract. Anything that clicks goes through here.
    """
    img, rect = frame["image_size"], frame["logical_rect"]
    if img["w"] <= 0 or img["h"] <= 0:
        raise FrameError(f"frame {frame['id']} has a degenerate image size")
    if not (0 <= x <= img["w"] and 0 <= y <= img["h"]):
        raise FrameError(
            f"({x},{y}) is outside frame {frame['id']} ({img['w']}x{img['h']})"
        )
    return (
        rect["x"] + x * (rect["w"] / img["w"]),
        rect["y"] + y * (rect["h"] / img["h"]),
    )


def age(frame: dict[str, Any]) -> float:
    return time.time() - frame["captured_at"]


def content_identity(frame: dict[str, Any]) -> str | None:
    """What the frame recorded about *what* it depicted, not where.

    A window's address, class, and geometry all survive the document inside it
    being replaced, so they cannot answer whether the pixels still mean what
    they meant. The title is the identity every window has; an adapter with a
    better one — a tab URL — records it here instead.
    """
    window = frame.get("window") or {}
    return window.get("content_id") or window.get("title")


def staleness(
    frame: dict[str, Any],
    windows: list[dict[str, Any]],
    monitors: list[dict[str, Any]],
    max_age: float = DEFAULT_MAX_AGE,
) -> str | None:
    """Why this frame can no longer be acted on, or None if it still can.

    Age alone is a weak signal. A window frame is checked against that window's
    geometry, while an output or region frame is checked against its output's
    geometry. Either can move every coordinate while the image still looks
    perfectly plausible.

    What the window *shows* is a separate question with a separate answer;
    see `content_drift`.
    """
    if age(frame) > max_age:
        return f"frame is {age(frame):.0f}s old (limit {max_age:.0f}s)"
    window = frame.get("window")
    if window is None:
        monitor = frame.get("monitor") or {}
        captured = monitor.get("logical_rect")
        if captured is None:
            return "frame has no captured output geometry"
        live = next((m for m in monitors if m["name"] == monitor.get("name")), None)
        if live is None:
            return f"output {monitor.get('name', '<unknown>')} is gone"
        if live["logical_rect"] != captured:
            return f"output {monitor['name']} moved or changed size since capture"
        return None
    live = next((w for w in windows if w["address"] == window["address"]), None)
    if live is None:
        return f"window {window['class']} ({window['address']}) is gone"
    if live["logical_rect"] != window["logical_rect"]:
        return f"window {window['class']} moved or resized since capture"
    return None


def content_drift(
    frame: dict[str, Any],
    windows: list[dict[str, Any]],
    drift_classes: tuple[str, ...] | list[str] = (),
) -> str | None:
    """Why the window no longer shows what the frame depicts, or None.

    Geometry answers where a window is, never what is inside it. A browser tab
    that navigates leaves the address, the class, and the rectangle untouched
    while replacing everything a coordinate could mean, and a frame that passes
    every geometric check then aims a click at a document nobody looked at.

    A class listed in `drift_classes` is exempt: some titles carry a clock or
    an unread count and move on their own, and refusing those would train a
    caller to turn the whole interlock off.

    A title is what every window has, not what every change moves: a document
    swap that keeps the title passes this. It narrows the window in which a
    coordinate can be wrong rather than closing it, which is why a frame is
    still bound to a moment and why `content_id` exists for an adapter that
    can name the document itself.
    """
    window = frame.get("window")
    captured = content_identity(frame)
    if window is None or captured is None:
        return None
    live = next((w for w in windows if w["address"] == window["address"]), None)
    if live is None:
        return None  # the window is gone, which `staleness` already reports
    current = live.get("content_id") or live.get("title")
    # The exemption is about titles that move on their own. It says nothing
    # about a document identity, which changes only when the document does —
    # so an exempt class is still refused when that is what disagrees.
    by_title = not (window.get("content_id") and live.get("content_id"))
    if by_title and window.get("class", "").lower() in {c.lower() for c in drift_classes}:
        return None
    if current == captured:
        return None
    # Both identities, because the caller has to be able to tell a page that
    # navigated from a title that merely ticked over.
    return (
        f"window {window['class']} showed {captured!r} when frame {frame['id']} "
        f"was captured and shows {current!r} now"
    )


def collect() -> int:
    """Drop frames that are too old or too numerous. Returns how many went."""
    metas = sorted(store_dir().glob("f*.json"), key=lambda p: p.stat().st_mtime)
    now = time.time()
    doomed = [p for p in metas if now - p.stat().st_mtime > GC_MAX_AGE]
    surplus = len(metas) - len(doomed) - GC_MAX_FRAMES
    if surplus > 0:
        doomed += [p for p in metas if p not in doomed][:surplus]
    for meta in doomed:
        for suffix in (".json", ".png"):
            meta.with_suffix(suffix).unlink(missing_ok=True)
    return len(doomed)
