"""Bounded read-only evidence export from validated CU frames."""

from __future__ import annotations

import hashlib
import math
import numbers
import re
from pathlib import Path
from typing import Any

from . import frames, png

EXPORT_SCHEMA = "cu-evidence/1"
FRAME_SPACE = frames.FRAME_IMAGE_SPACE
LOGICAL_SPACE = frames.LOGICAL_SPACE
MAX_RETAINED_BYTES = 4 * 1024 * 1024
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ExportError(ValueError):
    """A frame cannot satisfy the evidence export contract."""


def _identity(frame: dict[str, Any]) -> dict[str, Any]:
    return {
        key: frame.get(key)
        for key in ("id", "source", "captured_at", "window", "target_window", "monitor")
    }


def _coordinates() -> dict[str, str]:
    return {"image": FRAME_SPACE, "logical": LOGICAL_SPACE}


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        raise ExportError(f"frame {label} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ExportError(f"frame {label} must be a finite number")
    return result


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ExportError(f"frame {label} must be a positive integer")
    return value


def _descriptor(frame: dict[str, Any]) -> Path:
    if not isinstance(frame, dict):
        raise ExportError("frame descriptor must be an object")
    try:
        frames.validate_id(frame["id"])
    except (KeyError, TypeError, frames.FrameError) as exc:
        raise ExportError("frame descriptor has an invalid id") from exc

    source = frame.get("source")
    if (
        not isinstance(source, dict)
        or not isinstance(source.get("type"), str)
        or not source["type"].strip()
        or not isinstance(source.get("name"), str)
        or not source["name"].strip()
    ):
        raise ExportError("frame source identity must have non-empty type and name")

    _number(frame.get("captured_at"), "captured_at")
    image_size = frame.get("image_size")
    if not isinstance(image_size, dict):
        raise ExportError("frame image dimensions must be an object")
    width = _positive_int(image_size.get("w"), "image width")
    height = _positive_int(image_size.get("h"), "image height")
    if width * height > png.MAX_PIXELS:
        raise ExportError("frame image dimensions exceed the PNG pixel limit")

    logical_rect = frame.get("logical_rect")
    if not isinstance(logical_rect, dict):
        raise ExportError("frame logical coverage must be an object")
    for axis in ("x", "y", "w", "h"):
        _number(logical_rect.get(axis), f"logical {axis}")
    if logical_rect["w"] <= 0 or logical_rect["h"] <= 0:
        raise ExportError("frame logical coverage must have positive dimensions")

    if frame.get("coordinate_spaces") != _coordinates():
        raise ExportError("frame descriptor has unknown coordinate spaces")

    encoded_bytes = _positive_int(frame.get("encoded_bytes"), "encoded byte count")
    if encoded_bytes > MAX_RETAINED_BYTES:
        raise ExportError("frame exceeds the bounded export byte limit")
    digest = frame.get("digest_native")
    if not isinstance(digest, str) or not SHA256.fullmatch(digest):
        raise ExportError("frame descriptor has an invalid PNG digest")

    raw_path = frame.get("path")
    if not isinstance(raw_path, (str, Path)) or not str(raw_path):
        raise ExportError("frame descriptor has an invalid PNG path")
    return Path(raw_path)


def _retention() -> dict[str, Any]:
    return {
        "max_age_seconds": frames.GC_MAX_AGE,
        "max_frames": frames.GC_MAX_FRAMES,
        "expiry": "not-recorded",
    }


def _unavailable(frame: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "schema": EXPORT_SCHEMA,
        "status": "unavailable",
        "producer": _producer(frame),
        "frame": _identity(frame),
        "coverage": {
            "requested": {
                "logical_rect": frame.get("logical_rect"),
                "image_size": frame.get("image_size"),
            },
            "returned": None,
            "coordinate_spaces": _coordinates(),
        },
        "source": {
            "digest": frame.get("digest_native"),
            "encoded_bytes": None,
            "reference": {"kind": "cu-runtime-frame", "frame_id": frame["id"]},
        },
        "availability": {"runtime": "unavailable", "retained": None, "reason": reason},
        "retention": _retention(),
        "derived": [],
    }


def _producer(frame: dict[str, Any]) -> dict[str, Any]:
    configuration = {
        key: frame[key] for key in ("profile", "capture_scale") if key in frame
    }
    return {
        "name": "cu",
        "version": "0.1.0",
        "profile": frame.get("profile"),
        "configuration": configuration,
    }


def _read(frame: dict[str, Any]) -> tuple[bytes, int, int, bytes, str]:
    path = _descriptor(frame)
    try:
        with path.open(mode="rb", buffering=0) as handle:
            data = handle.read(MAX_RETAINED_BYTES + 1)
    except (OSError, ValueError) as exc:
        raise ExportError(f"frame {frame.get('id', '<unknown>')} is unavailable: {exc}") from exc
    if len(data) > MAX_RETAINED_BYTES:
        raise ExportError("frame exceeds the bounded export byte limit")
    if frame["encoded_bytes"] != len(data):
        raise ExportError("frame encoded byte count does not match its PNG")
    try:
        width, height, pixels = png.decode(data)
    except (OSError, ValueError) as exc:
        raise ExportError(
            f"frame {frame.get('id', '<unknown>')} has an invalid PNG: {exc}"
        ) from exc
    if {"w": width, "h": height} != frame["image_size"]:
        raise ExportError("frame image dimensions do not match its descriptor")
    digest = hashlib.sha256(data).hexdigest()
    if digest != frame["digest_native"]:
        raise ExportError("frame PNG digest does not match its descriptor")
    return data, width, height, pixels, digest


def _same_coverage(first: dict[str, Any], second: dict[str, Any]) -> None:
    for key in ("source", "window", "target_window", "monitor"):
        if first.get(key) != second.get(key):
            raise ExportError("pixel-change inputs do not share source identity")
    if (
        first["image_size"] != second["image_size"]
        or first["logical_rect"] != second["logical_rect"]
    ):
        raise ExportError("pixel-change inputs do not share image size and logical coverage")


def _retain(data: bytes, digest: str, retain: Path) -> dict[str, Any]:
    created = False
    try:
        with retain.open("xb") as handle:
            created = True
            handle.write(data)
            handle.flush()
    except FileExistsError as exc:
        raise ExportError(f"retention path already exists: {retain}") from exc
    except OSError as exc:
        if created:
            retain.unlink(missing_ok=True)
        raise ExportError(f"cannot retain frame bytes at {retain}: {exc}") from exc
    return {"path": str(retain), "encoded_bytes": len(data), "sha256": digest}


def export_frame(
    frame: dict[str, Any], *, compare: dict[str, Any] | None = None, retain: Path | None = None
) -> dict[str, Any]:
    """Export one frame and an optional same-shape pixel-change observation."""
    path = _descriptor(frame)
    if compare is not None:
        _descriptor(compare)
        _same_coverage(frame, compare)
    if not path.is_file():
        envelope = _unavailable(frame, "runtime PNG is absent; collection time is unknown")
        if compare is not None:
            envelope["derived"].append(
                {
                    "kind": "pixel-change",
                    "status": "unavailable",
                    "input_frames": [frame["id"], compare["id"]],
                }
            )
        return envelope
    data, width, height, pixels, digest = _read(frame)
    derived: list[dict[str, Any]] = []
    if compare is not None:
        compare_path = Path(compare["path"])
        if not compare_path.is_file():
            derived.append(
                {
                    "kind": "pixel-change",
                    "status": "unavailable",
                    "input_frames": [frame["id"], compare["id"]],
                }
            )
        else:
            _, _, _, other_pixels, other_digest = _read(compare)
            pixels_equal = pixels == other_pixels
            derived.append(
                {
                    "kind": "pixel-change",
                    "status": "unchanged" if pixels_equal else "changed",
                    "input_frames": [frame["id"], compare["id"]],
                    "score": "decoded-pixel-equality",
                    "decoded_pixel_equal": pixels_equal,
                    "byte_hash_equal": digest == other_digest,
                }
            )
    retained = _retain(data, digest, retain) if retain is not None else None
    envelope = {
        "schema": EXPORT_SCHEMA,
        "status": "ok",
        "producer": _producer(frame),
        "frame": _identity(frame),
        "coverage": {
            "requested": {
                "logical_rect": frame.get("logical_rect"),
                "image_size": frame.get("image_size"),
            },
            "returned": {
                "logical_rect": frame.get("logical_rect"),
                "image_size": {"w": width, "h": height},
            },
            "coordinate_spaces": _coordinates(),
        },
        "source": {
            "digest": digest,
            "encoded_bytes": len(data),
            "reference": {"kind": "cu-runtime-frame", "frame_id": frame["id"]},
        },
        "availability": {
            "runtime": "available",
            "retained": retained,
        },
        "retention": _retention(),
        "derived": derived,
    }
    return envelope
