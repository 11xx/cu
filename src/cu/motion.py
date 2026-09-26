"""Pure bounded paths for native pointer movement."""

from __future__ import annotations

import math
from collections.abc import Iterable

MAX_PATH_POINTS = 4096


class PathError(ValueError):
    """A path cannot be represented within the native movement bounds."""


def _point(value: Iterable[float]) -> dict[str, float]:
    values = tuple(value)
    if len(values) != 2 or not all(math.isfinite(number) for number in values):
        raise PathError("path points must contain two finite coordinates")
    return {"x": float(values[0]), "y": float(values[1])}


def line(
    start: Iterable[float], end: Iterable[float], samples: int
) -> list[dict[str, float]]:
    """Return an inclusive straight path with a bounded sample count."""
    if not isinstance(samples, int) or isinstance(samples, bool):
        raise PathError("path samples must be an integer")
    if not 1 <= samples <= MAX_PATH_POINTS:
        raise PathError(f"path samples must be between 1 and {MAX_PATH_POINTS}")
    first, last = _point(start), _point(end)
    return [
        {
            "x": first["x"] + (last["x"] - first["x"]) * index / samples,
            "y": first["y"] + (last["y"] - first["y"]) * index / samples,
        }
        for index in range(samples + 1)
    ]


def eased_line(
    start: Iterable[float], end: Iterable[float], samples: int
) -> list[dict[str, float]]:
    """A straight path whose samples follow a minimum-jerk speed profile.

    The pointer leaves from rest and settles into the endpoint instead of
    starting and stopping at full speed, which reads as a hand rather than a
    stepped jump.
    """
    straight = line(start, end, samples)
    first, last = straight[0], straight[-1]

    def progress(t: float) -> float:
        return t * t * t * (10 - 15 * t + 6 * t * t)

    return [
        {
            "x": first["x"] + (last["x"] - first["x"]) * progress(index / samples),
            "y": first["y"] + (last["y"] - first["y"]) * progress(index / samples),
        }
        for index in range(samples + 1)
    ]


def circle(
    center: Iterable[float], radius: float, turns: float, samples: int
) -> list[dict[str, float]]:
    """Return a closed circular path with finite radius, turns, and samples."""
    center_point = _point(center)
    if not math.isfinite(radius) or radius <= 0:
        raise PathError("circle radius must be a positive finite number")
    if not math.isfinite(turns) or turns <= 0:
        raise PathError("circle turns must be a positive finite number")
    if not isinstance(samples, int) or isinstance(samples, bool):
        raise PathError("path samples must be an integer")
    if 3 <= samples <= MAX_PATH_POINTS:
        return [
            {
                "x": center_point["x"] + radius * math.cos(math.tau * turns * index / samples),
                "y": center_point["y"] + radius * math.sin(math.tau * turns * index / samples),
            }
            for index in range(samples + 1)
        ]
    raise PathError(f"circle samples must be between 3 and {MAX_PATH_POINTS}")


def validate(points: list[dict[str, float]], bounds: dict[str, float]) -> None:
    """Require every sampled point inside a safe rectangle.

    A rectangle is convex, so the segment between two points inside it lies
    inside it too; checking the points checks the whole path.
    """
    if not points:
        raise PathError("a path must contain at least one point")
    for point in points:
        if not all(math.isfinite(point.get(axis, math.nan)) for axis in ("x", "y")):
            raise PathError("path points must be finite")
        if not (
            bounds["x"] <= point["x"] <= bounds["x"] + bounds["w"]
            and bounds["y"] <= point["y"] <= bounds["y"] + bounds["h"]
        ):
            raise PathError("path leaves the inspected safe region")
