"""Unit-test actions stay outside configured desktop audit logs."""

from __future__ import annotations

import json

from cu import config, safety


def test_action_records_use_private_log_even_with_explicit_config(tmp_path):
    private_log = tmp_path / "state" / "actions.jsonl"
    configured_log = tmp_path / "configured-actions.jsonl"
    configured_log.write_text("existing audit record\n")
    cfg = {"log": {"path": str(configured_log)}}

    assert config.log_path(config.DEFAULTS) == private_log
    assert config.log_path(cfg) == private_log

    safety.record(config.DEFAULTS, {"action": "default-path-probe"})
    safety.record(cfg, {"action": "configured-path-probe"})

    entries = [json.loads(line) for line in private_log.read_text().splitlines()]
    assert [entry["action"] for entry in entries] == [
        "default-path-probe", "configured-path-probe",
    ]
    assert configured_log.read_text() == "existing audit record\n"
