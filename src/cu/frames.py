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
import time
from pathlib import Path
from typing import Any

#: A frame older than this no longer describes the screen with any confidence.
DEFAULT_MAX_AGE = 60.0

#: Retention for the store as a whole, independent of usability.
GC_MAX_AGE = 900.0
GC_MAX_FRAMES = 60


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


def path_for(frame_id: str, suffix: str) -> Path:
    return store_dir() / f"{frame_id}{suffix}"


def save(descriptor: dict[str, Any]) -> dict[str, Any]:
    path_for(descriptor["id"], ".json").write_text(json.dumps(descriptor, indent=2))
    return descriptor


def load(frame_id: str) -> dict[str, Any]:
    meta = path_for(frame_id, ".json")
    if not meta.exists():
        raise FrameError(f"no such frame: {frame_id}")
    return json.loads(meta.read_text())


def new(
    source: dict[str, Any],
    logical_rect: dict[str, float],
    image_size: dict[str, int],
    scratch: Path,
    **extra: Any,
) -> dict[str, Any]:
    """Name a freshly captured image and record what it depicts.

    The image is captured to a scratch path because its id does not exist
    until here, and moving it is part of allocating that id rather than
    something each caller repeats — a descriptor whose `path` disagrees with
    where the PNG landed would resolve to some other capture entirely.
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


def staleness(
    frame: dict[str, Any], windows: list[dict[str, Any]], max_age: float = DEFAULT_MAX_AGE
) -> str | None:
    """Why this frame can no longer be acted on, or None if it still can.

    Age alone is a weak signal, so a frame that depicts a window is also
    checked against that window still existing at the same geometry. A moved
    window makes every coordinate in the frame wrong while the image still
    looks perfectly plausible.
    """
    if age(frame) > max_age:
        return f"frame is {age(frame):.0f}s old (limit {max_age:.0f}s)"
    window = frame.get("window")
    if window is None:
        return None
    live = next((w for w in windows if w["address"] == window["address"]), None)
    if live is None:
        return f"window {window['class']} ({window['address']}) is gone"
    if live["logical_rect"] != window["logical_rect"]:
        return f"window {window['class']} moved or resized since capture"
    return None


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
