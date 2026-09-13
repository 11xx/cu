"""Bounded read-only evidence export from validated CU frames."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from . import frames, png

EXPORT_SCHEMA = "cu-evidence/1"
FRAME_SPACE = "cu-frame-image-pixels"
LOGICAL_SPACE = "hyprland-logical"
MAX_RETAINED_BYTES = 4 * 1024 * 1024


class ExportError(ValueError):
    """A frame cannot satisfy the evidence export contract."""


def _identity(frame: dict[str, Any]) -> dict[str, Any]:
    return {
        key: frame.get(key)
        for key in ("id", "source", "captured_at", "window", "target_window", "monitor")
    }


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
        "producer": {"name": "cu", "version": "0.1.0"},
        "frame": _identity(frame),
        "coverage": {
            "requested": {
                "logical_rect": frame.get("logical_rect"),
                "image_size": frame.get("image_size"),
            },
            "returned": None,
            "coordinate_spaces": {"image": FRAME_SPACE, "logical": LOGICAL_SPACE},
        },
        "source": {"digest": frame.get("digest_native"), "encoded_bytes": None},
        "availability": {"runtime": "unavailable", "reason": reason},
        "retention": _retention(),
        "derived": [],
    }


def _read(frame: dict[str, Any]) -> tuple[bytes, int, int, str]:
    path = Path(frame.get("path", ""))
    try:
        data = path.read_bytes()
    except (OSError, ValueError) as exc:
        raise ExportError(f"frame {frame.get('id', '<unknown>')} is unavailable: {exc}") from exc
    if len(data) > MAX_RETAINED_BYTES:
        raise ExportError("frame exceeds the bounded export byte limit")
    encoded = frame.get("encoded_bytes")
    if not isinstance(encoded, int) or encoded != len(data):
        raise ExportError("frame encoded byte count does not match its PNG")
    try:
        width, height, _ = png.decode(data)
    except (OSError, ValueError) as exc:
        raise ExportError(
            f"frame {frame.get('id', '<unknown>')} has an invalid PNG: {exc}"
        ) from exc
    if {"w": width, "h": height} != frame.get("image_size"):
        raise ExportError("frame image dimensions do not match its descriptor")
    digest = hashlib.sha256(data).hexdigest()
    if digest != frame.get("digest_native"):
        raise ExportError("frame PNG digest does not match its descriptor")
    return data, width, height, digest


def export_frame(
    frame: dict[str, Any], *, compare: dict[str, Any] | None = None, retain: Path | None = None
) -> dict[str, Any]:
    """Export one frame and an optional same-shape pixel-change observation."""
    path = Path(frame.get("path", ""))
    if not path.is_file():
        envelope = _unavailable(frame, "runtime PNG is absent; collection time is unknown")
        if compare is not None:
            envelope["derived"].append({"kind": "pixel-change", "status": "unavailable"})
        return envelope
    data, width, height, digest = _read(frame)
    if retain is not None:
        if retain.exists():
            raise ExportError(f"retention path already exists: {retain}")
        try:
            with retain.open("xb") as handle:
                handle.write(data)
        except OSError as exc:
            raise ExportError(f"cannot retain frame bytes at {retain}: {exc}") from exc
        retained = {"path": str(retain), "encoded_bytes": len(data), "sha256": digest}
    else:
        retained = None
    envelope = {
        "schema": EXPORT_SCHEMA,
        "status": "ok",
        "producer": {"name": "cu", "version": "0.1.0", "profile": frame.get("profile")},
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
            "coordinate_spaces": {"image": FRAME_SPACE, "logical": LOGICAL_SPACE},
        },
        "source": {"digest": digest, "encoded_bytes": len(data)},
        "availability": {
            "runtime": "available",
            "retained": retained,
        },
        "retention": _retention(),
        "derived": [],
    }
    if compare is not None:
        if (
            frame.get("image_size") != compare.get("image_size")
            or frame.get("logical_rect") != compare.get("logical_rect")
        ):
            raise ExportError("pixel-change inputs do not share image size and logical coverage")
        if not Path(compare.get("path", "")).is_file():
            envelope["derived"].append({
                "kind": "pixel-change",
                "status": "unavailable",
                "input_frames": [frame.get("id"), compare.get("id")],
            })
        else:
            _, _, _, other_digest = _read(compare)
            envelope["derived"].append({
                "kind": "pixel-change",
                "status": "changed" if digest != other_digest else "unchanged",
                "input_frames": [frame.get("id"), compare.get("id")],
                "score": "sha256-equality",
            })
    return envelope
