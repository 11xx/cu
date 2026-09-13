"""File-based task-local checkpoint prototype; no production storage owner."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CHECKPOINT_VERSION = "cu-routine-checkpoint/1"
FORBIDDEN_FIELDS = {"body", "content", "url", "signed_url"}


class CheckpointError(ValueError):
    """A checkpoint cannot be trusted for the current task."""


def _required_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CheckpointError(f"{name} must be a nonempty string")
    return value


def create(
    *,
    intent: str,
    destination: str,
    revision: str,
    parameters: dict[str, str],
    affordances: list[dict[str, Any]],
    completed: list[dict[str, Any]],
    pending: list[dict[str, Any]],
    frame_bindings: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a checkpoint containing decisions and receipts, never source bodies."""
    _required_text(intent, "intent")
    _required_text(destination, "destination")
    _required_text(revision, "revision")
    if any(field in json.dumps(parameters) for field in FORBIDDEN_FIELDS):
        raise CheckpointError("checkpoint parameters contain forbidden source data")
    return {
        "schema": CHECKPOINT_VERSION,
        "task": {"intent": intent, "destination": destination, "revision": revision},
        "parameters": dict(parameters),
        "affordances": list(affordances),
        "completed": list(completed),
        "pending": list(pending),
        "frame_bindings": [
            {**binding, "status": "historical"} for binding in frame_bindings
        ],
    }


def validate(checkpoint: dict[str, Any], current: dict[str, Any]) -> None:
    """Check task identity and require fresh evidence for every old frame."""
    if checkpoint.get("schema") != CHECKPOINT_VERSION:
        raise CheckpointError("unsupported checkpoint schema")
    task = checkpoint.get("task")
    if not isinstance(task, dict):
        raise CheckpointError("checkpoint has no task identity")
    for field in ("intent", "destination", "revision"):
        if task.get(field) != current.get(field):
            raise CheckpointError(f"checkpoint {field} does not match the current task")
    if any(field in json.dumps(checkpoint) for field in FORBIDDEN_FIELDS):
        raise CheckpointError("checkpoint contains forbidden source data")


def resume(
    checkpoint: dict[str, Any],
    current: dict[str, Any],
    *,
    aborted: bool = False,
) -> dict[str, Any]:
    """Return the decided prefix to skip and exceptions that still need judgment."""
    if aborted:
        raise CheckpointError("the task is aborted; resume requires a new user decision")
    validate(checkpoint, current)
    completed = checkpoint.get("completed", [])
    pending = checkpoint.get("pending", [])
    if not isinstance(completed, list) or not isinstance(pending, list):
        raise CheckpointError("checkpoint completed and pending fields must be lists")
    return {
        "skip_completed": [item.get("artifact_id") for item in completed],
        "pending_exceptions": list(pending),
        "requires_reobserve": bool(checkpoint.get("frame_bindings")),
        "rebound_frames": [],
    }


def write(path: Path, checkpoint: dict[str, Any]) -> None:
    """Write one explicit checkpoint file for a caller-managed prototype."""
    validate(checkpoint, checkpoint["task"])
    path.write_text(json.dumps(checkpoint, indent=2) + "\n")


def read(path: Path) -> dict[str, Any]:
    """Read and validate a checkpoint document without interpreting coordinates."""
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise CheckpointError(f"cannot read checkpoint: {exc}") from exc
    if not isinstance(value, dict):
        raise CheckpointError("checkpoint must be an object")
    return value
