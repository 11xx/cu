"""A local bounded consumer for the CU evidence export envelope."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

SCHEMA = "cu-evidence/1"


class ConsumerError(ValueError):
    """An evidence envelope cannot be consumed as declared."""


def consume(envelope: dict[str, Any]) -> dict[str, Any]:
    if envelope.get("schema") != SCHEMA:
        raise ConsumerError("unsupported evidence schema")
    coverage = envelope.get("coverage") or {}
    spaces = coverage.get("coordinate_spaces") or {}
    if (
        spaces.get("image") != "cu-frame-image-pixels"
        or spaces.get("logical") != "hyprland-logical"
    ):
        raise ConsumerError("unknown coordinate space")
    if envelope.get("status") == "unavailable":
        return {"status": "unavailable", "derived": envelope.get("derived", [])}
    source = envelope.get("source") or {}
    retained = (envelope.get("availability") or {}).get("retained")
    if retained:
        path = Path(retained["path"])
        data = path.read_bytes()
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
        "derived": envelope.get("derived", []),
    }
