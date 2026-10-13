"""Pure layout quantization and refresh-aligned absolute stream plans.

These plans carry no device or input authority. The compositor's normalization,
device interplay and hover delivery require carrier-specific live probes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .motion import MAX_PATH_POINTS, PathError

AXIS_STEPS = 65536


@dataclass(frozen=True)
class Output:
    """One active output's normalized logical rectangle."""

    x: float
    y: float
    width: float
    height: float

    def __post_init__(self) -> None:
        if not all(math.isfinite(value) for value in (self.x, self.y, self.width, self.height)):
            raise PathError("output geometry must be finite")
        if self.width <= 0 or self.height <= 0:
            raise PathError("output dimensions must be positive")
        if not all(math.isfinite(value) for value in (self.x + self.width, self.y + self.height)):
            raise PathError("output extent must be finite")

    def contains(self, x: float, y: float) -> bool:
        return self.x <= x < self.x + self.width and self.y <= y < self.y + self.height


@dataclass(frozen=True)
class Layout:
    """An immutable output union and its absolute-axis bounding rectangle."""

    outputs: tuple[Output, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.outputs, tuple) or not 1 <= len(self.outputs) <= 64:
            raise PathError("layout requires one through 64 immutable output rectangles")
        if not all(isinstance(output, Output) for output in self.outputs):
            raise PathError("layout entries must be normalized outputs")
        x, y, width, height = self.bounds
        if not all(math.isfinite(value) for value in (x, y, width, height)):
            raise PathError("layout extent must be finite")

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        x = min(output.x for output in self.outputs)
        y = min(output.y for output in self.outputs)
        right = max(output.x + output.width for output in self.outputs)
        bottom = max(output.y + output.height for output in self.outputs)
        return x, y, right - x, bottom - y

    def encode(self, x: float, y: float) -> tuple[int, int]:
        """Quantize logical pixel centers; refuse points in gaps or outside outputs."""
        if not all(math.isfinite(value) for value in (x, y)):
            raise PathError("absolute points must be finite")
        if not any(output.contains(x, y) for output in self.outputs):
            raise PathError("absolute point is outside active outputs")
        left, top, width, height = self.bounds

        def axis(value: float, origin: float, extent: float) -> int:
            return min(AXIS_STEPS - 1, math.floor((value - origin + 0.5) / extent * AXIS_STEPS))

        return axis(x, left, width), axis(y, top, height)

    def require_segment(self, start: tuple[float, float], end: tuple[float, float]) -> None:
        """Require the entire straight route to lie in the active output union."""
        self.encode(*start)
        self.encode(*end)
        intervals = []
        for output in self.outputs:
            low, high = 0.0, 1.0
            for first, last, origin, extent in (
                (start[0], end[0], output.x, output.width),
                (start[1], end[1], output.y, output.height),
            ):
                delta = last - first
                if delta == 0:
                    if not origin <= first < origin + extent:
                        high = -1.0
                        break
                else:
                    edges = ((origin - first) / delta, (origin + extent - first) / delta)
                    low, high = max(low, min(edges)), min(high, max(edges))
            if low <= high:
                intervals.append((low, high))
        covered = 0.0
        for low, high in sorted(intervals):
            if low > covered:
                break
            covered = max(covered, high)
        if covered < 1.0:
            raise PathError("absolute route crosses an output gap")


@dataclass(frozen=True)
class Sample:
    """A planned deadline relative to stream start and its logical/axis coordinates."""

    at: float
    x: float
    y: float
    abs_x: int
    abs_y: int


def plan(
    layout: Layout, start: tuple[float, float], end: tuple[float, float], *,
    duration: float, refresh_hz: float, multiple: int = 3, eased: bool = True,
) -> tuple[Sample, ...]:
    """Sample a gap-free line at a refresh multiple, retaining every deadline."""
    if not math.isfinite(duration) or not 0 < duration <= 10:
        raise PathError("stream duration must be positive and at most ten seconds")
    if not math.isfinite(refresh_hz) or not 0 < refresh_hz <= 1000:
        raise PathError("refresh rate must be positive and at most 1000 Hz")
    if type(multiple) is not int or not 1 <= multiple <= 8:
        raise PathError("refresh multiple must be an integer from one through eight")
    if type(eased) is not bool:
        raise PathError("eased must be a boolean")
    count = math.ceil(duration * refresh_hz * multiple)
    if count > MAX_PATH_POINTS:
        raise PathError("stream exceeds the sample budget")
    layout.require_segment(start, end)
    result = []
    for index in range(count + 1):
        at = duration if index == count else index / (refresh_hz * multiple)
        t = at / duration
        progress = t * t * t * (10 - 15 * t + 6 * t * t) if eased else t
        x = start[0] + (end[0] - start[0]) * progress
        y = start[1] + (end[1] - start[1]) * progress
        if index == count:
            x, y = end
        abs_x, abs_y = layout.encode(x, y)
        result.append(Sample(at, x, y, abs_x, abs_y))
    return tuple(result)
