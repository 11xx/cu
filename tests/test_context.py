"""Invocation correlation stays explicit, bounded, and resettable."""

from __future__ import annotations

import json

import pytest

from cu import cli, config, context, frames, metrics, png, safety


def test_explicit_session_wins_and_context_resets(monkeypatch):
    monkeypatch.setenv("CU_SESSION", "environment-session")
    with context.active("cli-session") as invocation:
        fields = context.metadata()
        assert fields["invocation_id"] == invocation.invocation_id
        assert fields["cu_session"] == "cli-session"
    assert context.metadata() == {}


def test_harness_fields_are_copied_only_when_supplied(monkeypatch):
    monkeypatch.setenv("CU_HARNESS", "codex")
    monkeypatch.setenv("CU_HARNESS_SESSION", "harness-session")
    with context.active():
        assert context.metadata()["harness"] == "codex"
        assert context.metadata()["harness_session"] == "harness-session"


def test_batch_step_keeps_parent_invocation_and_restores_parent():
    with context.active("session"):
        parent = context.metadata()
        with context.batch_step(3):
            child = context.metadata()
            assert child["invocation_id"] == parent["invocation_id"]
            assert child["parent_invocation_id"] == parent["invocation_id"]
            assert child["batch_step"] == 3
        assert context.metadata() == parent


def test_action_log_and_metrics_carry_correlation_without_content(tmp_path, monkeypatch):
    path = tmp_path / "actions.jsonl"
    monkeypatch.setattr(config, "log_path", lambda _cfg: path)
    with context.active("session"):
        safety.record(config.DEFAULTS, {"action": "probe"})
        report = metrics.Measurement().report(0)
    entry = json.loads(path.read_text())
    assert entry["invocation_id"] == report["invocation_id"]
    assert entry["cu_session"] == report["cu_session"] == "session"
    assert "title" not in entry and "url" not in entry
    assert report["schema"] == "cu-metrics/2"


def test_fresh_frame_carries_the_acquisition_invocation(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    with context.active("session") as invocation, frames.capture_scratch() as scratch:
        scratch.write_bytes(png.encode(1, 1, bytes(4)))
        frame = frames.new(
            source={"type": "output", "name": "OUT"},
            logical_rect={"x": 0, "y": 0, "w": 1, "h": 1},
            image_size={"w": 1, "h": 1},
            scratch=scratch,
        )
    assert frame["invocation_id"] == invocation.invocation_id
    assert frame["cu_session"] == "session"


def test_repeated_invocations_get_distinct_ids_and_cleanup():
    with context.active() as first:
        first_id = first.invocation_id
    with context.active() as second:
        second_id = second.invocation_id
    assert first_id != second_id
    assert context.metadata() == {}


def test_parser_failure_does_not_leave_an_invocation_context():
    with pytest.raises(SystemExit):
        cli.main(["not-a-command"])
    assert context.metadata() == {}


def test_batch_receipt_carries_parent_invocation_and_step(tmp_path, monkeypatch, capsys):
    batch = tmp_path / "batch.json"
    batch.write_text(json.dumps([["key", "--chord", "a"]]))
    monkeypatch.setattr(
        cli.observe,
        "snapshot",
        lambda: {"windows": [], "focused": None, "monitors": []},
    )
    monkeypatch.setattr(
        cli.actions,
        "key",
        lambda *_args, **kwargs: {
            "action": "key",
            "chord": "a",
            "semantic": None,
            "repeat": 1,
            "dry_run": kwargs["dry_run"],
        },
    )
    assert cli.main(["batch", str(batch), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    child = payload["completed"][0]["result"]
    assert child["invocation_id"] == payload["invocation_id"]
    assert child["parent_invocation_id"] == payload["invocation_id"]
    assert child["batch_step"] == 1
