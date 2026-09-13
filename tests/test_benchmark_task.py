"""Completion verifier and deterministic task-envelope coverage."""

from __future__ import annotations

from pathlib import Path

from benchmark_task import expected_items, run_trial, verify


def test_verifier_accepts_exact_recovered_fixture(tmp_path):
    result = run_trial(tmp_path, mode="batch", seed=7, delay_seconds=0.001)
    assert result["outcome"] == "completed"
    assert result["verification"]["expected_count"] == 16
    assert result["recovery"]["ids"] == ["item-03"]
    assert result["unexpected_dialog"]["status"] == "observed"
    assert result["timing"]["model_seconds"] is None


def test_verifier_rejects_missing_zero_wrong_and_duplicate_count_only(tmp_path):
    items = expected_items(1)
    output = tmp_path / "artifacts"
    output.mkdir()
    (output / "one").write_text(items[0]["content"])
    (output / "zero").write_bytes(b"")
    (output / "wrong").write_text("wrong content\n")
    (output / "duplicate").write_text(items[0]["content"])
    result = verify(output, items)
    kinds = {failure["kind"] for failure in result["failures"]}
    assert result["ok"] is False
    assert "zero-byte" in kinds
    assert "wrong-or-duplicate-content" in kinds
    assert "missing-or-wrong-content" in kinds


def test_verifier_rejects_a_still_changing_file(tmp_path, monkeypatch):
    items = expected_items(1)
    output = tmp_path / "artifacts"
    output.mkdir()
    path = output / "item"
    path.write_text(items[0]["content"])
    original_read = Path.read_bytes

    def changing_read(self, *args, **kwargs):
        value = original_read(self, *args, **kwargs)
        if self == path:
            path.write_text(path.read_text() + "changed")
        return value

    monkeypatch.setattr(Path, "read_bytes", changing_read)
    result = verify(output, items)
    assert any(failure["kind"] == "changing" for failure in result["failures"])


def test_modes_emit_same_fixture_contract_and_unknown_external_costs(tmp_path):
    envelopes = [
        run_trial(tmp_path / mode, mode=mode, seed=9)
        for mode in ("individual", "batch", "combined")
    ]
    assert {envelope["fixture"]["version"] for envelope in envelopes} == {"cu-task-fixture/1"}
    assert {envelope["fixture"]["seed"] for envelope in envelopes} == {9}
    assert all(envelope["verification"]["ok"] for envelope in envelopes)
    assert all(envelope["cost"]["monetary_billing"] is None for envelope in envelopes)
