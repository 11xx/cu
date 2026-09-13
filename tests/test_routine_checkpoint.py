"""Checkpoint prototype preserves decisions while invalidating stale evidence."""

from __future__ import annotations

import json

import pytest
from routine_checkpoint import CheckpointError, create, read, resume, write


def current_task(**overrides):
    return {
        "intent": "download April artifacts",
        "destination": "fixture-output",
        "revision": "fixture-v1",
        **overrides,
    }


def checkpoint():
    return create(
        intent="download April artifacts",
        destination="fixture-output",
        revision="fixture-v1",
        parameters={"range": "April", "rename": "preserve-collision"},
        affordances=[{"kind": "bulk-select", "status": "observed"}],
        completed=[
            {"artifact_id": "item-01", "source_name": "README.md", "status": "verified"},
            {"artifact_id": "item-02", "source_name": "README.md", "status": "verified"},
        ],
        pending=[{"artifact_id": "item-03", "kind": "collision", "status": "unresolved"}],
        frame_bindings=[{"frame": "f1", "window": "0x1"}],
    )


def test_resume_skips_completed_effects_and_keeps_pending_exceptions():
    result = resume(
        checkpoint(),
        current_task(),
    )
    assert result["skip_completed"] == ["item-01", "item-02"]
    assert result["pending_exceptions"][0]["kind"] == "collision"
    assert result["requires_reobserve"] is True
    assert result["rebound_frames"] == []


@pytest.mark.parametrize("field", ["intent", "destination", "revision"])
def test_changed_task_identity_invalidates_resume(field):
    current = current_task()
    current[field] = "different"
    with pytest.raises(CheckpointError, match=field):
        resume(checkpoint(), current)


def test_checkpoint_coordinates_are_historical_until_rebound():
    result = resume(
        checkpoint(),
        current_task(),
    )
    assert result["rebound_frames"] == []
    assert result["requires_reobserve"] is True


def test_checkpoint_round_trip_has_no_source_body_or_url(tmp_path):
    value = checkpoint()
    path = tmp_path / "checkpoint.json"
    write(path, value)
    restored = read(path)
    encoded = json.dumps(restored)
    assert restored == value
    assert "body" not in encoded and "content" not in encoded and "url" not in encoded


def test_abort_blocks_resume():
    with pytest.raises(CheckpointError, match="aborted"):
        resume(
            checkpoint(),
            current_task(),
            aborted=True,
        )
