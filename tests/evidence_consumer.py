"""A local bounded consumer for the CU evidence export envelope."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

SCHEMA = "cu-evidence/1"
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ConsumerError(ValueError):
    """An evidence envelope cannot be consumed as declared."""


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConsumerError(f"{label} must be an object")
    return value


def _coverage(envelope: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    coverage = _object(envelope.get("coverage"), "coverage")
    requested = _object(coverage.get("requested"), "requested coverage")
    if not isinstance(requested.get("logical_rect"), dict) or not isinstance(
        requested.get("image_size"), dict
    ):
        raise ConsumerError("requested coverage is incomplete")
    spaces = _object(coverage.get("coordinate_spaces"), "coordinate spaces")
    if (
        spaces.get("image") != "cu-frame-image-pixels"
        or spaces.get("logical") != "hyprland-logical"
    ):
        raise ConsumerError("unknown coordinate space")
    returned = coverage.get("returned")
    if returned is not None:
        returned = _object(returned, "returned coverage")
        if not isinstance(returned.get("logical_rect"), dict) or not isinstance(
            returned.get("image_size"), dict
        ):
            raise ConsumerError("returned coverage is incomplete")
    return coverage, returned


def _derived(envelope: dict[str, Any], frame_id: str) -> list[dict[str, Any]]:
    derived = envelope.get("derived")
    if not isinstance(derived, list):
        raise ConsumerError("derived observations must be a list")
    for item in derived:
        observation = _object(item, "derived observation")
        if observation.get("kind") != "pixel-change":
            raise ConsumerError("unsupported derived observation")
        inputs = observation.get("input_frames")
        if not isinstance(inputs, list) or len(inputs) != 2 or inputs[0] != frame_id:
            raise ConsumerError("derived observation has no exact input reference")
        if observation.get("status") not in {"changed", "unchanged", "unavailable"}:
            raise ConsumerError("invalid derived observation status")
        if observation["status"] != "unavailable":
            if observation.get("score") != "decoded-pixel-equality":
                raise ConsumerError("derived observation has an unknown score")
            if not isinstance(observation.get("decoded_pixel_equal"), bool) or not isinstance(
                observation.get("byte_hash_equal"), bool
            ):
                raise ConsumerError("derived observation has incomplete scores")
    return derived


def consume(envelope: dict[str, Any]) -> dict[str, Any]:
    if envelope.get("schema") != SCHEMA:
        raise ConsumerError("unsupported evidence schema")
    producer = _object(envelope.get("producer"), "producer")
    if not isinstance(producer.get("name"), str) or not isinstance(
        producer.get("version"), str
    ):
        raise ConsumerError("producer identity is incomplete")
    _object(producer.get("configuration"), "producer configuration")
    frame = _object(envelope.get("frame"), "frame")
    frame_id = frame.get("id")
    if not isinstance(frame_id, str) or not frame_id:
        raise ConsumerError("frame identity is incomplete")
    coverage, returned = _coverage(envelope)
    source = _object(envelope.get("source"), "source")
    reference = _object(source.get("reference"), "source reference")
    if reference.get("kind") != "cu-runtime-frame" or reference.get("frame_id") != frame_id:
        raise ConsumerError("source reference does not identify the frame")
    if not isinstance(source.get("digest"), str) or not SHA256.fullmatch(source["digest"]):
        raise ConsumerError("source digest is missing")
    availability = _object(envelope.get("availability"), "availability")
    derived = _derived(envelope, frame_id)
    if envelope.get("status") == "unavailable":
        if availability.get("runtime") != "unavailable" or availability.get("retained") is not None:
            raise ConsumerError("unavailable source has an invalid availability state")
        if returned is not None:
            raise ConsumerError("unavailable source has returned coverage")
        return {"status": "unavailable", "derived": derived}
    if envelope.get("status") != "ok":
        raise ConsumerError("unsupported evidence status")
    if availability.get("runtime") != "available" or returned is None:
        raise ConsumerError("available source has incomplete coverage")
    if not isinstance(source.get("encoded_bytes"), int) or source["encoded_bytes"] <= 0:
        raise ConsumerError("available source byte count is missing")
    retained = availability.get("retained")
    if retained is not None:
        retained = _object(retained, "retained source")
        if (
            not isinstance(retained.get("path"), str)
            or not isinstance(retained.get("encoded_bytes"), int)
            or retained["encoded_bytes"] <= 0
            or not isinstance(retained.get("sha256"), str)
            or not SHA256.fullmatch(retained["sha256"])
            or retained["encoded_bytes"] != source["encoded_bytes"]
        ):
            raise ConsumerError("retained source descriptor is incomplete")
        path = Path(retained["path"])
        try:
            with path.open("rb") as handle:
                data = handle.read(retained["encoded_bytes"] + 1)
        except OSError as exc:
            raise ConsumerError(f"cannot read retained bytes: {exc}") from exc
        if (
            len(data) != retained["encoded_bytes"]
            or hashlib.sha256(data).hexdigest() != retained["sha256"]
        ):
            raise ConsumerError("retained bytes do not match their digest")
        if retained["sha256"] != source.get("digest"):
            raise ConsumerError("retained bytes disagree with source digest")
    return {
        "status": "available",
        "digest": source.get("digest"),
        "coverage": coverage,
        "producer": producer,
        "derived": derived,
    }
