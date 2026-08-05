"""Screen capture, budgeted rather than literal.

Per-output capture is the primitive. Whole-desktop capture is never used:
Hyprland composites it at the largest scale in play, which upscales every other
output into an image that is both larger and blurrier than capturing each one
natively.

`grim -g` takes logical coordinates — the same space frames describe — so a
frame's rectangle is directly usable as its own capture argument.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path
from typing import Any

#: Downscale targets by consumer. The long-edge figure is what actually drives
#: image token cost; the ceiling is where a provider stops accepting detail.
PROFILES = {
    "claude": {"max_long_edge": 1920, "ceiling": 2576},
    "openai": {"max_long_edge": 1536, "ceiling": 2048},
    "generic": {"max_long_edge": 1280, "ceiling": 1568},
}


class CaptureError(RuntimeError):
    pass


def _run(args: list[str]) -> None:
    if not shutil.which(args[0]):
        raise CaptureError(f"{args[0]} is not installed")
    proc = subprocess.run(args, capture_output=True, text=True)
    if proc.returncode != 0:
        raise CaptureError(f"{' '.join(args[:2])}: {proc.stderr.strip()}")


def intersects(rect: dict[str, float], other: dict[str, float]) -> bool:
    return not (
        rect["x"] + rect["w"] <= other["x"]
        or other["x"] + other["w"] <= rect["x"]
        or rect["y"] + rect["h"] <= other["y"]
        or other["y"] + other["h"] <= rect["y"]
    )


def sole_monitor(
    rect: dict[str, float], monitors: list[dict[str, Any]]
) -> dict[str, Any]:
    """The one monitor a rectangle lies on.

    A region spanning outputs is refused rather than captured: Hyprland would
    composite it at the larger scale, silently upscaling the other half.
    """
    touched = [m for m in monitors if intersects(rect, m["logical_rect"])]
    if not touched:
        raise CaptureError("region falls outside every output")
    if len(touched) > 1:
        names = ", ".join(m["name"] for m in touched)
        raise CaptureError(
            f"region spans {names}; capture one output at a time to avoid upscaling"
        )
    return touched[0]


def clamp(rect: dict[str, float], bounds: dict[str, float]) -> dict[str, float]:
    x0 = max(rect["x"], bounds["x"])
    y0 = max(rect["y"], bounds["y"])
    x1 = min(rect["x"] + rect["w"], bounds["x"] + bounds["w"])
    y1 = min(rect["y"] + rect["h"], bounds["y"] + bounds["h"])
    if x1 <= x0 or y1 <= y0:
        raise CaptureError("region is empty after clamping to its output")
    return {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}


def scale_for(
    rect: dict[str, float], monitor_scale: float, max_long_edge: int
) -> float:
    """The grim scale factor that lands inside the budget.

    `grim -s` multiplies the *logical* size, so the factor is a ratio against
    the logical rectangle rather than against pixels. It is capped at the
    output's own scale: beyond that grim would upsample, spending tokens on
    pixels carrying no additional detail.
    """
    longest = max(rect["w"], rect["h"])
    if longest <= 0:
        raise CaptureError("cannot scale an empty region")
    return min(max_long_edge / longest, monitor_scale)


def grab_output(name: str, dest: Path, scale: float) -> None:
    _run(["grim", "-o", name, "-s", f"{scale:.6f}", str(dest)])


def grab_region(rect: dict[str, float], dest: Path, scale: float) -> None:
    geometry = f"{int(rect['x'])},{int(rect['y'])} {int(rect['w'])}x{int(rect['h'])}"
    _run(["grim", "-g", geometry, "-s", f"{scale:.6f}", str(dest)])


PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def size_of(path: Path) -> dict[str, int]:
    """Read the dimensions out of the PNG header.

    Spawning `magick identify` to learn two integers cost ~80 ms per call and
    was invoked three times per capture; the IHDR chunk is at a fixed offset.
    """
    header = path.read_bytes()[:24]
    if len(header) < 24 or not header.startswith(PNG_MAGIC):
        raise CaptureError(f"{path} is not a PNG")
    return {
        "w": int.from_bytes(header[16:20], "big"),
        "h": int.from_bytes(header[20:24], "big"),
    }


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
