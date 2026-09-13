"""Pure bounded paths for native pointer movement."""

from __future__ import annotations

import math
from collections.abc import Iterable
from itertools import pairwise

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
    """Require every sampled point and segment endpoint inside a safe rectangle."""
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
    # A rectangle is convex, so a segment whose endpoints are inside it is also
    # inside it. Checking every segment keeps that invariant explicit for paths
    # generated outside this module.
    for first, second in pairwise(points):
        if not (
            bounds["x"] <= first["x"] <= bounds["x"] + bounds["w"]
            and bounds["y"] <= first["y"] <= bounds["y"] + bounds["h"]
            and bounds["x"] <= second["x"] <= bounds["x"] + bounds["w"]
            and bounds["y"] <= second["y"] <= bounds["y"] + bounds["h"]
        ):
            raise PathError("path segment leaves the inspected safe region")
