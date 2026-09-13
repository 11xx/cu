"""Task context preserves verified effects while invalidating stale evidence."""

from __future__ import annotations

import copy
import hashlib
import json

import pytest
from routine_checkpoint import (
    MAX_SERIALIZED_BYTES,
    CheckpointError,
    create,
    read,
    resume,
    write,
)

PARAMETERS = {"range": "April", "rename": "preserve-collision"}
CONTENT = {
    "item-01": "verified artifact one\n",
    "item-02": "verified artifact two\n",
    "item-03": "verified artifact three\n",
}


def current_task(**overrides):
    return {
        "task_id": "task-april-17",
        "intent": "download April artifacts",
        "destination": "fixture-output",
        "revision": "fixture-v1",
        "document": "fixture-document-17",
        "scope": "artifact-tree",
        "parameters": dict(PARAMETERS),
        **overrides,
    }


def receipt(root, artifact_id, *, content=None, actual=None):
    expected = CONTENT[artifact_id] if content is None else content
    path = root / artifact_id / "README.md"
    if actual is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(actual)
    return {
        "artifact_id": artifact_id,
        "path": f"{artifact_id}/README.md",
        "sha256": hashlib.sha256(expected.encode()).hexdigest(),
        "size": len(expected.encode()),
        "status": "verified",
        "source_name": "README.md",
        "attempt_id": "attempt-1",
    }


def checkpoint(root, *, completed=None, pending=None, **overrides):
    completed = completed if completed is not None else [
        receipt(root, "item-01", actual=CONTENT["item-01"]),
        receipt(root, "item-02", actual=CONTENT["item-02"]),
    ]
    pending = pending if pending is not None else [
        {"artifact_id": "item-03", "kind": "collision", "status": "unresolved"}
    ]
    values = {
        "shape": "full-start",
        "attempt_id": "attempt-1",
        "status": "failed",
        "outcome": "incomplete",
        "failures": [{"artifact_id": "item-03", "stage": "bulk"}],
        "evidence": {"freshness": "stale", "sources": ["cu-task-fixture/1:17"]},
        "recovery": {},
    }
    values.update(overrides)
    return create(
        task_id="task-april-17",
        intent="download April artifacts",
        destination="fixture-output",
        revision="fixture-v1",
        document="fixture-document-17",
        scope="artifact-tree",
        parameters=PARAMETERS,
        affordances=[{"kind": "bulk-select", "status": "observed"}],
        completed=completed,
        pending=pending,
        frame_bindings=[{"frame": "f-old", "window": "0x1", "lease": "lease-old"}],
        context_id="context-april-17",
        **values,
    )


def test_resume_skips_only_verified_effects_and_keeps_pending_exceptions(tmp_path):
    result = resume(checkpoint(tmp_path), current_task(), artifact_root=tmp_path)

    assert result["skip_completed"] == ["item-01", "item-02"]
    assert result["reconsider"] == []
    assert result["pending_exceptions"][0]["kind"] == "collision"
    assert result["shape"] == "full-start"
    assert result["first_attempt"] is True
    assert result["outcome"] == "incomplete"


@pytest.mark.parametrize(
    "field", ["intent", "destination", "revision", "document", "scope", "task_id"]
)
def test_changed_task_identity_invalidates_resume(tmp_path, field):
    current = current_task()
    current[field] = "different"
    with pytest.raises(CheckpointError, match=field):
        resume(checkpoint(tmp_path), current, artifact_root=tmp_path)


def test_changed_parameters_invalidate_resume(tmp_path):
    current = current_task(parameters={"range": "May", "rename": "preserve-collision"})
    with pytest.raises(CheckpointError, match="parameters"):
        resume(checkpoint(tmp_path), current, artifact_root=tmp_path)


def test_missing_and_changed_artifacts_are_reconsidered_without_replaying_valid_effects(tmp_path):
    completed = [
        receipt(tmp_path, "item-01", actual=CONTENT["item-01"]),
        receipt(tmp_path, "item-02"),
        receipt(tmp_path, "item-03", actual="different content\n"),
    ]
    result = resume(
        checkpoint(tmp_path, completed=completed, pending=[]),
        current_task(),
        artifact_root=tmp_path,
    )

    assert result["skip_completed"] == ["item-01"]
    assert result["reconsider"] == [
        {"artifact_id": "item-02", "reason": "missing-artifact"},
        {"artifact_id": "item-03", "reason": "artifact-mismatch"},
    ]
    assert not (tmp_path / "item-01" / "README.md").read_text().startswith("different")


def test_delayed_and_different_content_collision_remain_pending(tmp_path):
    completed = [
        receipt(tmp_path, "item-01", actual=CONTENT["item-01"]),
        receipt(tmp_path, "item-02", actual="different-content-collision\n"),
    ]
    result = resume(
        checkpoint(
            tmp_path,
            completed=completed,
            pending=[
                {"artifact_id": "item-03", "kind": "delayed", "status": "unresolved"},
                {"artifact_id": "item-04", "kind": "collision", "status": "unresolved"},
            ],
        ),
        current_task(),
        artifact_root=tmp_path,
    )

    assert result["skip_completed"] == ["item-01"]
    assert result["reconsider"] == [{"artifact_id": "item-02", "reason": "artifact-mismatch"}]
    assert [item["kind"] for item in result["pending_exceptions"]] == ["delayed", "collision"]


def test_pending_exception_wins_over_completed_membership(tmp_path):
    completed = [receipt(tmp_path, "item-01", actual=CONTENT["item-01"])]
    result = resume(
        checkpoint(
            tmp_path,
            completed=completed,
            pending=[{"artifact_id": "item-01", "kind": "collision", "status": "unresolved"}],
        ),
        current_task(),
        artifact_root=tmp_path,
    )
    assert result["skip_completed"] == []
    assert result["reconsider"] == [{"artifact_id": "item-01", "reason": "pending-exception"}]


def test_context_without_artifact_root_never_trusts_completed_membership(tmp_path):
    result = resume(checkpoint(tmp_path), current_task())
    assert result["skip_completed"] == []
    assert result["reconsider"] == [
        {"artifact_id": "item-01", "reason": "artifact-verification-unavailable"},
        {"artifact_id": "item-02", "reason": "artifact-verification-unavailable"},
    ]


def test_checkpoint_coordinates_and_leases_are_historical_until_reacquired(tmp_path):
    value = checkpoint(tmp_path)
    result = resume(value, current_task(), artifact_root=tmp_path)

    assert [binding["status"] for binding in value["frame_bindings"]] == ["historical"]
    assert result["requires_reobserve"] is True
    assert result["evidence_freshness"] == "stale"
    assert result["rebound_frames"] == []
    assert result["rebound_leases"] == []


def test_round_trip_preserves_context_without_source_body_or_url(tmp_path):
    value = checkpoint(tmp_path)
    path = tmp_path / "checkpoint.json"
    write(path, value)
    restored = read(path)

    assert restored == value
    encoded = json.dumps(restored)
    assert all(field not in encoded for field in ("body", "content", "url", "signed_url"))


def test_context_preserves_cold_resume_and_eventual_recovery_shape(tmp_path):
    completed = [
        {**receipt(tmp_path, "item-01", actual=CONTENT["item-01"]), "attempt_id": "attempt-2"},
        {**receipt(tmp_path, "item-02", actual=CONTENT["item-02"]), "attempt_id": "attempt-2"},
    ]
    value = checkpoint(
        tmp_path,
        completed=completed,
        pending=[],
        shape="cold-resume",
        first_attempt=False,
        attempt_id="attempt-2",
        attempt_number=2,
        status="completed",
        outcome="completed",
        recovery={"attempted": True, "ids": ["item-03"], "skipped": ["item-01"]},
    )
    result = resume(value, current_task(), artifact_root=tmp_path)

    assert result["shape"] == "cold-resume"
    assert result["first_attempt"] is False
    assert result["attempt_id"] == "attempt-2"
    assert result["outcome"] == "completed"


@pytest.mark.parametrize(
    ("field", "value"),
    [("body", "private"), ("signed_url", "https://private.example/file")],
)
def test_private_source_fields_are_rejected(field, value):
    parameters = {"range": "April", field: value}
    with pytest.raises(CheckpointError, match="private"):
        create(
            intent="download artifacts",
            destination="fixture-output",
            revision="fixture-v1",
            parameters=parameters,
            affordances=[],
            completed=[],
            pending=[],
            frame_bindings=[],
        )


def test_path_traversal_and_malformed_receipts_are_rejected(tmp_path):
    malformed = checkpoint(tmp_path)
    malformed["completed"] = [{"artifact_id": "item-01", "status": "verified"}]
    with pytest.raises(CheckpointError, match=r"completed\[0\]\.path"):
        resume(malformed, current_task(), artifact_root=tmp_path)

    with pytest.raises(CheckpointError, match="relative path"):
        create(
            intent="download artifacts",
            destination="fixture-output",
            revision="fixture-v1",
            parameters={},
            affordances=[],
            completed=[
                {
                    **receipt(tmp_path, "item-01", actual=CONTENT["item-01"]),
                    "path": "../outside",
                }
            ],
            pending=[],
            frame_bindings=[],
        )


def test_oversized_context_is_rejected():
    with pytest.raises(CheckpointError, match="bytes"):
        create(
            intent="download artifacts",
            destination="fixture-output",
            revision="fixture-v1",
            parameters={f"parameter-{index}": "x" * 512 for index in range(128)},
            affordances=[],
            completed=[],
            pending=[],
            frame_bindings=[],
            evidence={"freshness": "unknown", "sources": []},
        )

    assert MAX_SERIALIZED_BYTES == 64 * 1024


def test_abort_blocks_resume_before_any_reuse(tmp_path):
    with pytest.raises(CheckpointError, match="aborted"):
        resume(checkpoint(tmp_path), current_task(), artifact_root=tmp_path, aborted=True)


def test_resume_does_not_mutate_checkpoint(tmp_path):
    value = checkpoint(tmp_path)
    before = copy.deepcopy(value)

    resume(value, current_task(), artifact_root=tmp_path)

    assert value == before
