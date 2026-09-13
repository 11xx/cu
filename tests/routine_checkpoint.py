"""Bounded, caller-managed task context for local recovery.

The context records decisions and verified effect receipts for one task. It is
not a CU store: the caller supplies the current task and artifact root, while
CU remains responsible for reacquiring live windows, frames, and leases.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

CHECKPOINT_VERSION = "cu-routine-context/1"

# A context is a small handoff, not a transcript or an artifact cache. The
# collection limits keep every serialized field reviewable before a caller
# gives it to another round.
MAX_SERIALIZED_BYTES = 64 * 1024
MAX_STRING_LENGTH = 512
MAX_COLLECTION_ITEMS = 128
MAX_COMPLETED_ITEMS = 256
MAX_PENDING_ITEMS = 128
MAX_FRAME_BINDINGS = 128
MAX_FAILURES = 128
MAX_NESTING = 4
MAX_ARTIFACT_BYTES = 16 * 1024 * 1024

ROUND_SHAPES = frozenset(
    {"full-start", "prepared-page", "warm-follow-up", "cold-resume"}
)
RUN_STATUSES = frozenset({"in-progress", "completed", "failed", "aborted", "interrupted"})
RUN_OUTCOMES = frozenset({"completed", "incomplete", "aborted", "unknown"})
PENDING_STATUSES = frozenset({"pending", "unresolved", "failed", "uncertain", "deferred"})
EVIDENCE_FRESHNESS = frozenset({"fresh", "stale", "unknown"})

FORBIDDEN_FIELDS = {"body", "content", "url", "signed_url"}
HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
PATH_SEPARATORS = {"", ".", ".."}

TOP_LEVEL_FIELDS = {
    "schema",
    "context_id",
    "task",
    "parameters",
    "affordances",
    "completed",
    "pending",
    "evidence",
    "frame_bindings",
    "run",
}
TASK_FIELDS = {"task_id", "intent", "destination", "revision", "document", "scope"}
RUN_FIELDS = {
    "shape",
    "attempt_id",
    "attempt_number",
    "first_attempt",
    "status",
    "outcome",
    "failures",
    "recovery",
}
RECOVERY_FIELDS = {"attempted", "ids", "reconsidered", "skipped"}
EVIDENCE_FIELDS = {"freshness", "sources"}
COMPLETED_FIELDS = {
    "artifact_id",
    "path",
    "sha256",
    "size",
    "status",
    "source_name",
    "attempt_id",
}


class CheckpointError(ValueError):
    """A task context cannot be trusted for the current task."""


def _bounded_text(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise CheckpointError(f"{name} must be a string")
    if len(value) > MAX_STRING_LENGTH:
        raise CheckpointError(f"{name} exceeds {MAX_STRING_LENGTH} characters")
    if "\n" in value or "\r" in value:
        raise CheckpointError(f"{name} must be one line")
    return value


def _required_text(value: Any, name: str) -> str:
    result = _bounded_text(value, name)
    if not result.strip():
        raise CheckpointError(f"{name} must be a nonempty string")
    return result


def _private_key(key: str) -> bool:
    words = re.split(r"[^a-z0-9]+", key.lower())
    return any(word in FORBIDDEN_FIELDS or word == "signed" for word in words)


def _private_value(value: str) -> bool:
    lowered = value.strip().lower()
    return lowered.startswith(("http://", "https://", "data:"))


def _bounded_value(value: Any, name: str, *, depth: int = 0) -> None:
    """Validate metadata recursively without accepting transcript-shaped data."""
    if depth > MAX_NESTING:
        raise CheckpointError(f"{name} exceeds {MAX_NESTING} nested levels")
    if isinstance(value, str):
        if len(value) > MAX_STRING_LENGTH:
            raise CheckpointError(f"{name} exceeds {MAX_STRING_LENGTH} characters")
        if _private_value(value):
            raise CheckpointError(f"{name} contains private URL data")
        return
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CheckpointError(f"{name} must contain finite numbers")
        return
    if isinstance(value, Mapping):
        if len(value) > MAX_COLLECTION_ITEMS:
            raise CheckpointError(f"{name} exceeds {MAX_COLLECTION_ITEMS} fields")
        for key, child in value.items():
            if not isinstance(key, str) or not key.strip():
                raise CheckpointError(f"{name} has an invalid field name")
            if len(key) > MAX_STRING_LENGTH:
                raise CheckpointError(f"{name}.{key} has an oversized field name")
            if _private_key(key):
                raise CheckpointError(f"{name}.{key} is private source data")
            _bounded_value(child, f"{name}.{key}", depth=depth + 1)
        return
    if isinstance(value, list):
        if len(value) > MAX_COLLECTION_ITEMS:
            raise CheckpointError(f"{name} exceeds {MAX_COLLECTION_ITEMS} items")
        for index, child in enumerate(value):
            _bounded_value(child, f"{name}[{index}]", depth=depth + 1)
        return
    raise CheckpointError(f"{name} contains unsupported data")


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8") + b"\n"
    except (TypeError, UnicodeEncodeError, ValueError) as exc:
        raise CheckpointError(f"checkpoint is not JSON-serializable: {exc}") from exc
    if len(encoded) > MAX_SERIALIZED_BYTES:
        raise CheckpointError(f"checkpoint exceeds {MAX_SERIALIZED_BYTES} bytes")
    return encoded


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CheckpointError(f"{name} must be an object")
    return value


def _bounded_identifier(value: Any, name: str) -> str:
    result = _required_text(value, name)
    if any(character.isspace() for character in result):
        raise CheckpointError(f"{name} must not contain whitespace")
    return result


def _relative_path(value: Any, name: str) -> str:
    result = _required_text(value, name)
    path = Path(result)
    if path.is_absolute() or any(part in PATH_SEPARATORS for part in path.parts):
        raise CheckpointError(f"{name} must be a relative path inside the artifact root")
    return path.as_posix()


def _parameters(value: Any, name: str = "parameters") -> dict[str, str]:
    raw = _mapping(value, name)
    if len(raw) > MAX_COLLECTION_ITEMS:
        raise CheckpointError(f"{name} exceeds {MAX_COLLECTION_ITEMS} fields")
    result: dict[str, str] = {}
    for raw_key, child in raw.items():
        key = _bounded_identifier(raw_key, f"{name} key")
        if _private_key(key):
            raise CheckpointError(f"{name}.{key} is private source data")
        result[key] = _bounded_text(child, f"{name}.{key}")
        if _private_value(result[key]):
            raise CheckpointError(f"{name}.{key} contains private URL data")
    return result


def _task_values(
    *,
    intent: Any,
    destination: Any,
    revision: Any,
    task_id: Any = None,
    document: Any = None,
    scope: Any = None,
) -> dict[str, Any]:
    identity = {
        "intent": _required_text(intent, "intent"),
        "destination": _required_text(destination, "destination"),
        "revision": _required_text(revision, "revision"),
        "document": None,
        "scope": None,
    }
    for field, value in (("document", document), ("scope", scope)):
        if value is not None:
            identity[field] = _required_text(value, field)
    if task_id is None:
        seed = json.dumps(identity, sort_keys=True, separators=(",", ":"))
        task_id = f"task-{hashlib.sha256(seed.encode()).hexdigest()[:24]}"
    identity["task_id"] = _bounded_identifier(task_id, "task_id")
    return {
        "task_id": identity["task_id"],
        "intent": identity["intent"],
        "destination": identity["destination"],
        "revision": identity["revision"],
        "document": identity["document"],
        "scope": identity["scope"],
    }


def _task_from_current(current: Mapping[str, Any]) -> dict[str, Any]:
    raw = current.get("task")
    raw = _mapping(raw, "current task") if raw is not None else current
    return _task_values(
        task_id=raw.get("task_id"),
        intent=raw.get("intent"),
        destination=raw.get("destination"),
        revision=raw.get("revision"),
        document=raw.get("document"),
        scope=raw.get("scope"),
    )


def _task_for_checkpoint(checkpoint: Mapping[str, Any]) -> dict[str, Any]:
    task = _mapping(checkpoint.get("task"), "checkpoint task")
    return _task_values(
        task_id=task.get("task_id"),
        intent=task.get("intent"),
        destination=task.get("destination"),
        revision=task.get("revision"),
        document=task.get("document"),
        scope=task.get("scope"),
    )


def _records(value: Any, name: str, limit: int) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise CheckpointError(f"{name} must be a list")
    if len(value) > limit:
        raise CheckpointError(f"{name} exceeds {limit} items")
    records = []
    for index, item in enumerate(value):
        record = dict(_mapping(item, f"{name}[{index}]"))
        _bounded_value(record, f"{name}[{index}]")
        records.append(record)
    return records


def _completed_item(value: Any, index: int) -> dict[str, Any]:
    item = dict(_mapping(value, f"completed[{index}]"))
    _bounded_value(item, f"completed[{index}]")
    unknown = set(item) - COMPLETED_FIELDS
    if unknown:
        raise CheckpointError(
            f"completed[{index}] has unsupported fields: {', '.join(sorted(unknown))}"
        )
    item["artifact_id"] = _bounded_identifier(
        item.get("artifact_id"), f"completed[{index}].artifact_id"
    )
    item["path"] = _relative_path(item.get("path"), f"completed[{index}].path")
    if item.get("status") != "verified":
        raise CheckpointError(f"completed[{index}].status must be verified")
    item["sha256"] = item.get("sha256")
    if not isinstance(item["sha256"], str) or not HASH_PATTERN.fullmatch(item["sha256"]):
        raise CheckpointError(f"completed[{index}].sha256 must be a lowercase SHA-256 digest")
    size = item.get("size")
    if (
        not isinstance(size, int)
        or isinstance(size, bool)
        or not 0 <= size <= MAX_ARTIFACT_BYTES
    ):
        raise CheckpointError(
            f"completed[{index}].size must be an integer between 0 and {MAX_ARTIFACT_BYTES}"
        )
    if "source_name" in item:
        item["source_name"] = _required_text(
            item["source_name"], f"completed[{index}].source_name"
        )
    if "attempt_id" in item:
        item["attempt_id"] = _bounded_identifier(
            item["attempt_id"], f"completed[{index}].attempt_id"
        )
    return item


def _completed(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise CheckpointError("completed must be a list")
    if len(value) > MAX_COMPLETED_ITEMS:
        raise CheckpointError(f"completed exceeds {MAX_COMPLETED_ITEMS} items")
    result = [_completed_item(item, index) for index, item in enumerate(value)]
    ids = [item["artifact_id"] for item in result]
    if len(ids) != len(set(ids)):
        raise CheckpointError("completed contains duplicate artifact IDs")
    paths = [item["path"] for item in result]
    if len(paths) != len(set(paths)):
        raise CheckpointError("completed contains duplicate artifact paths")
    return result


def _pending(value: Any) -> list[dict[str, Any]]:
    result = _records(value, "pending", MAX_PENDING_ITEMS)
    for index, item in enumerate(result):
        if "artifact_id" in item:
            item["artifact_id"] = _bounded_identifier(
                item["artifact_id"], f"pending[{index}].artifact_id"
            )
        item["kind"] = _required_text(item.get("kind"), f"pending[{index}].kind")
        status = item.get("status")
        if status not in PENDING_STATUSES:
            raise CheckpointError(
                f"pending[{index}].status must be one of {', '.join(sorted(PENDING_STATUSES))}"
            )
    return result


def _frames(value: Any) -> list[dict[str, Any]]:
    result = _records(value, "frame_bindings", MAX_FRAME_BINDINGS)
    for index, item in enumerate(result):
        item["frame"] = _bounded_identifier(
            item.get("frame"), f"frame_bindings[{index}].frame"
        )
        if "lease" in item:
            item["lease"] = _bounded_identifier(
                item["lease"], f"frame_bindings[{index}].lease"
            )
        # A binding is evidence about a prior runtime, never a live target.
        item["status"] = "historical"
    return result


def _evidence(value: Any) -> dict[str, Any]:
    raw = dict(_mapping(value, "evidence"))
    unknown = set(raw) - EVIDENCE_FIELDS
    if unknown:
        raise CheckpointError(
            f"evidence has unsupported fields: {', '.join(sorted(unknown))}"
        )
    freshness = raw.get("freshness", "unknown")
    if freshness not in EVIDENCE_FRESHNESS:
        raise CheckpointError(
            f"evidence.freshness must be one of {', '.join(sorted(EVIDENCE_FRESHNESS))}"
        )
    sources = raw.get("sources", [])
    if not isinstance(sources, list) or len(sources) > MAX_COLLECTION_ITEMS:
        raise CheckpointError("evidence.sources must be a bounded list")
    sources = [_required_text(source, "evidence.sources entry") for source in sources]
    return {"freshness": freshness, "sources": sources}


def _recovery(value: Any) -> dict[str, Any]:
    raw = dict(_mapping(value, "run.recovery"))
    unknown = set(raw) - RECOVERY_FIELDS
    if unknown:
        raise CheckpointError(
            f"run.recovery has unsupported fields: {', '.join(sorted(unknown))}"
        )
    attempted = raw.get("attempted", False)
    if not isinstance(attempted, bool):
        raise CheckpointError("run.recovery.attempted must be boolean")
    result = {"attempted": attempted}
    for field in ("ids", "reconsidered", "skipped"):
        values = raw.get(field, [])
        if not isinstance(values, list) or len(values) > MAX_COLLECTION_ITEMS:
            raise CheckpointError(f"run.recovery.{field} must be a bounded list")
        result[field] = [
            _bounded_identifier(item, f"run.recovery.{field} entry") for item in values
        ]
    return result


def _run(value: Any) -> dict[str, Any]:
    raw = dict(_mapping(value, "run"))
    unknown = set(raw) - RUN_FIELDS
    if unknown:
        raise CheckpointError(f"run has unsupported fields: {', '.join(sorted(unknown))}")
    shape = raw.get("shape")
    if shape not in ROUND_SHAPES:
        raise CheckpointError(f"run.shape must be one of {', '.join(sorted(ROUND_SHAPES))}")
    attempt_id = _bounded_identifier(raw.get("attempt_id"), "run.attempt_id")
    attempt_number = raw.get("attempt_number")
    if (
        not isinstance(attempt_number, int)
        or isinstance(attempt_number, bool)
        or attempt_number < 1
    ):
        raise CheckpointError("run.attempt_number must be a positive integer")
    first_attempt = raw.get("first_attempt")
    if not isinstance(first_attempt, bool):
        raise CheckpointError("run.first_attempt must be boolean")
    status = raw.get("status")
    if status not in RUN_STATUSES:
        raise CheckpointError(f"run.status must be one of {', '.join(sorted(RUN_STATUSES))}")
    outcome = raw.get("outcome")
    if outcome not in RUN_OUTCOMES:
        raise CheckpointError(f"run.outcome must be one of {', '.join(sorted(RUN_OUTCOMES))}")
    if status == "completed" and outcome != "completed":
        raise CheckpointError("a completed run must have a completed outcome")
    if status == "aborted" and outcome != "aborted":
        raise CheckpointError("an aborted run must have an aborted outcome")
    if outcome == "completed" and status != "completed":
        raise CheckpointError("a completed outcome requires a completed run")
    failures = _records(raw.get("failures", []), "run.failures", MAX_FAILURES)
    return {
        "shape": shape,
        "attempt_id": attempt_id,
        "attempt_number": attempt_number,
        "first_attempt": first_attempt,
        "status": status,
        "outcome": outcome,
        "failures": failures,
        "recovery": _recovery(raw.get("recovery", {})),
    }


def _validate_shape(checkpoint: Mapping[str, Any]) -> dict[str, Any]:
    if set(checkpoint) != TOP_LEVEL_FIELDS:
        missing = TOP_LEVEL_FIELDS - set(checkpoint)
        unknown = set(checkpoint) - TOP_LEVEL_FIELDS
        details = []
        if missing:
            details.append(f"missing {', '.join(sorted(missing))}")
        if unknown:
            details.append(f"unsupported {', '.join(sorted(unknown))}")
        raise CheckpointError("checkpoint fields are invalid: " + "; ".join(details))
    if checkpoint.get("schema") != CHECKPOINT_VERSION:
        raise CheckpointError("unsupported checkpoint schema")
    context_id = _bounded_identifier(checkpoint.get("context_id"), "context_id")
    task = _task_for_checkpoint(checkpoint)
    if set(_mapping(checkpoint.get("task"), "checkpoint task")) != TASK_FIELDS:
        raise CheckpointError("checkpoint task fields are invalid")
    parameters = _parameters(checkpoint.get("parameters"))
    affordances = _records(
        checkpoint.get("affordances"), "affordances", MAX_COLLECTION_ITEMS
    )
    completed = _completed(checkpoint.get("completed"))
    pending = _pending(checkpoint.get("pending"))
    evidence = _evidence(checkpoint.get("evidence"))
    frames = _frames(checkpoint.get("frame_bindings"))
    run = _run(checkpoint.get("run"))
    if run["outcome"] == "completed" and pending:
        raise CheckpointError("a completed outcome cannot have pending exceptions")
    for index, item in enumerate(completed):
        if "attempt_id" in item and item["attempt_id"] != run["attempt_id"]:
            raise CheckpointError(
                f"completed[{index}].attempt_id does not match run.attempt_id"
            )
    normalized = {
        "schema": CHECKPOINT_VERSION,
        "context_id": context_id,
        "task": task,
        "parameters": parameters,
        "affordances": affordances,
        "completed": completed,
        "pending": pending,
        "evidence": evidence,
        "frame_bindings": frames,
        "run": run,
    }
    _bounded_value(normalized, "checkpoint")
    _json_bytes(normalized)
    return normalized


def _context_id(task: Mapping[str, Any], parameters: Mapping[str, str]) -> str:
    seed = json.dumps(
        {"task": task, "parameters": parameters}, sort_keys=True, separators=(",", ":")
    )
    return f"context-{hashlib.sha256(seed.encode()).hexdigest()[:24]}"


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
    task_id: str | None = None,
    document: str | None = None,
    scope: str | None = None,
    context_id: str | None = None,
    shape: str = "cold-resume",
    attempt_id: str = "attempt-1",
    attempt_number: int = 1,
    first_attempt: bool = True,
    status: str = "in-progress",
    outcome: str = "incomplete",
    failures: list[dict[str, Any]] | None = None,
    recovery: dict[str, Any] | None = None,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one bounded context without source bodies or live target authority."""
    task = _task_values(
        task_id=task_id,
        intent=intent,
        destination=destination,
        revision=revision,
        document=document,
        scope=scope,
    )
    normalized_parameters = _parameters(parameters)
    value = {
        "schema": CHECKPOINT_VERSION,
        "context_id": (
            _bounded_identifier(context_id, "context_id")
            if context_id is not None
            else _context_id(task, normalized_parameters)
        ),
        "task": task,
        "parameters": normalized_parameters,
        "affordances": affordances,
        "completed": completed,
        "pending": pending,
        "evidence": (
            evidence
            if evidence is not None
            else {"freshness": "unknown", "sources": []}
        ),
        "frame_bindings": frame_bindings,
        "run": {
            "shape": shape,
            "attempt_id": attempt_id,
            "attempt_number": attempt_number,
            "first_attempt": first_attempt,
            "status": status,
            "outcome": outcome,
            "failures": failures if failures is not None else [],
            "recovery": recovery if recovery is not None else {},
        },
    }
    return _validate_shape(value)


def validate(checkpoint: dict[str, Any], current: dict[str, Any]) -> None:
    """Validate shape, exact task parameters, and current scope before reuse."""
    normalized = _validate_shape(_mapping(checkpoint, "checkpoint"))
    current_mapping = _mapping(current, "current task")
    current_task = _task_from_current(current_mapping)
    for field in ("intent", "destination", "revision", "document", "scope", "task_id"):
        if normalized["task"][field] != current_task[field]:
            raise CheckpointError(f"checkpoint {field} does not match the current task")
    if "parameters" not in current_mapping:
        if normalized["parameters"]:
            raise CheckpointError("current task parameters are missing")
    elif normalized["parameters"] != _parameters(
        current_mapping["parameters"], "current parameters"
    ):
        raise CheckpointError("checkpoint parameters do not match the current task")


def _artifact_location(root: Path, relative: str) -> Path:
    root = root.resolve()
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise CheckpointError("artifact receipt escapes its caller-owned root") from exc
    return candidate


def verify_artifact(root: Path, receipt: dict[str, Any]) -> dict[str, Any]:
    """Verify one receipt's relative path, size, digest, and stable read."""
    item = _completed_item(receipt, 0)
    try:
        path = _artifact_location(Path(root), item["path"])
    except (OSError, RuntimeError, CheckpointError) as exc:
        return {
            "artifact_id": item["artifact_id"],
            "status": "reconsider",
            "reason": str(exc),
        }
    try:
        before = path.stat()
    except OSError:
        return {
            "artifact_id": item["artifact_id"],
            "status": "reconsider",
            "reason": "missing-artifact",
        }
    if not path.is_file():
        return {
            "artifact_id": item["artifact_id"],
            "status": "reconsider",
            "reason": "artifact-is-not-a-file",
        }
    if before.st_size > MAX_ARTIFACT_BYTES:
        return {
            "artifact_id": item["artifact_id"],
            "status": "reconsider",
            "reason": "artifact-exceeds-verification-bound",
        }
    digest = hashlib.sha256()
    total = 0
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_ARTIFACT_BYTES:
                    return {
                        "artifact_id": item["artifact_id"],
                        "status": "reconsider",
                        "reason": "artifact-exceeds-verification-bound",
                    }
                digest.update(chunk)
    except OSError:
        return {
            "artifact_id": item["artifact_id"],
            "status": "reconsider",
            "reason": "artifact-read-failed",
        }
    try:
        after = path.stat()
    except OSError:
        return {
            "artifact_id": item["artifact_id"],
            "status": "reconsider",
            "reason": "artifact-disappeared",
        }
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        return {
            "artifact_id": item["artifact_id"],
            "status": "reconsider",
            "reason": "artifact-changed-during-verification",
        }
    if before.st_size != item["size"] or digest.hexdigest() != item["sha256"]:
        return {
            "artifact_id": item["artifact_id"],
            "status": "reconsider",
            "reason": "artifact-mismatch",
        }
    return {"artifact_id": item["artifact_id"], "status": "verified"}


def resume(
    checkpoint: dict[str, Any],
    current: dict[str, Any],
    *,
    artifact_root: Path | None = None,
    aborted: bool = False,
) -> dict[str, Any]:
    """Return reusable effects and exceptions without replaying or rebinding."""
    if aborted:
        raise CheckpointError("the task is aborted; resume requires a new user decision")
    validate(checkpoint, current)
    normalized = _validate_shape(checkpoint)
    pending = normalized["pending"]
    pending_ids = {
        item["artifact_id"] for item in pending if item.get("artifact_id") is not None
    }
    skip_completed: list[str] = []
    reconsider: list[dict[str, str]] = []
    for item in normalized["completed"]:
        artifact_id = item["artifact_id"]
        if artifact_id in pending_ids:
            reconsider.append({"artifact_id": artifact_id, "reason": "pending-exception"})
            continue
        if artifact_root is None:
            result = {
                "artifact_id": artifact_id,
                "status": "reconsider",
                "reason": "artifact-verification-unavailable",
            }
        else:
            result = verify_artifact(Path(artifact_root), item)
        if result["status"] == "verified":
            skip_completed.append(artifact_id)
        else:
            reconsider.append({"artifact_id": artifact_id, "reason": result["reason"]})
    freshness = normalized["evidence"]["freshness"]
    requires_reobserve = bool(normalized["frame_bindings"]) or freshness != "fresh"
    return {
        "context_id": normalized["context_id"],
        "task_id": normalized["task"]["task_id"],
        "shape": normalized["run"]["shape"],
        "first_attempt": normalized["run"]["first_attempt"],
        "attempt_id": normalized["run"]["attempt_id"],
        "status": normalized["run"]["status"],
        "outcome": normalized["run"]["outcome"],
        "skip_completed": skip_completed,
        "reconsider": reconsider,
        "pending_exceptions": list(pending),
        "evidence_freshness": freshness,
        "requires_reobserve": requires_reobserve,
        "rebound_frames": [],
        "rebound_leases": [],
    }


def _current_for_checkpoint(checkpoint: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "task": dict(checkpoint["task"]),
        "parameters": dict(checkpoint["parameters"]),
    }


def write(path: Path, checkpoint: dict[str, Any]) -> None:
    """Write one bounded context to a caller-selected path."""
    normalized = _validate_shape(_mapping(checkpoint, "checkpoint"))
    validate(normalized, _current_for_checkpoint(normalized))
    try:
        Path(path).write_bytes(_json_bytes(normalized))
    except OSError as exc:
        raise CheckpointError(f"cannot write checkpoint: {exc}") from exc


def read(path: Path) -> dict[str, Any]:
    """Read one bounded context and reject oversized or malformed JSON."""
    path = Path(path)
    try:
        if path.stat().st_size > MAX_SERIALIZED_BYTES:
            raise CheckpointError(f"checkpoint exceeds {MAX_SERIALIZED_BYTES} bytes")
        with path.open("rb") as stream:
            encoded = stream.read(MAX_SERIALIZED_BYTES + 1)
    except CheckpointError:
        raise
    except OSError as exc:
        raise CheckpointError(f"cannot read checkpoint: {exc}") from exc
    if len(encoded) > MAX_SERIALIZED_BYTES:
        raise CheckpointError(f"checkpoint exceeds {MAX_SERIALIZED_BYTES} bytes")
    try:
        value = json.loads(encoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CheckpointError(f"cannot read checkpoint: {exc}") from exc
    if not isinstance(value, dict):
        raise CheckpointError("checkpoint must be an object")
    return _validate_shape(value)
