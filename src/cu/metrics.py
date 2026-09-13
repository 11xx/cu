"""Opt-in per-invocation timing, without desktop content or persistent telemetry."""

from __future__ import annotations

import time
from collections import defaultdict
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from typing import Any

from . import context

_current: ContextVar[Measurement | None] = ContextVar("cu_metrics", default=None)
COMPONENTS = (
    "acquisition", "reference_decode", "probe_decode", "crop_compare",
    "intentional_wait", "frame_publication", "output_publication",
    "native_preflight", "native_device", "native_delivery", "native_convergence",
)


class Measurement:
    def __init__(self):
        self.started = time.monotonic()
        self.seconds: dict[str, float] = dict.fromkeys(COMPONENTS, 0.0)
        self.operations: dict[str, int] = defaultdict(int)
        self.frames: list[dict[str, Any]] = []

    def report(self, code: int) -> dict[str, Any]:
        total = time.monotonic() - self.started
        return {
            "schema": "cu-metrics/2", **context.metadata(), "code": code,
            "total_seconds": total,
            "seconds": self.seconds, "operations": dict(self.operations),
            "other_seconds": max(0, total - sum(self.seconds.values())),
            "frames": self.frames,
            "excluded": ["process_startup", "metrics_emission", "transport", "model"],
        }


@contextmanager
def collecting(measurement: Measurement | None):
    token = _current.set(measurement)
    try:
        yield
    finally:
        _current.reset(token)


@contextmanager
def span(name: str):
    measurement = _current.get()
    if measurement is None:
        yield
        return
    started = time.monotonic()
    try:
        yield
    finally:
        measurement.seconds[name] += time.monotonic() - started
        measurement.operations[name] += 1


def count(name: str, amount: int = 1) -> None:
    """Add bounded operation counts to an opt-in invocation measurement."""
    measurement = _current.get()
    if measurement is not None:
        measurement.operations[name] += amount


def timed(name: str):
    def decorate(function):
        @wraps(function)
        def measured(*args, **kwargs):
            with span(name):
                return function(*args, **kwargs)
        return measured
    return decorate


def frame(value: Any) -> None:
    measurement = _current.get()
    if measurement is None or not isinstance(value, dict) or "image_size" not in value:
        return
    if len(measurement.frames) < 32:
        measurement.frames.append({key: value.get(key) for key in (
            "id", "image_size", "encoded_bytes", "visual_tokens", "estimator",
        )})
