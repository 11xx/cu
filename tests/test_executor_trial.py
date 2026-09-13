"""Conditional executor evaluation stays negative while measurements are unknown."""

from __future__ import annotations

from executor_trial import evaluate_entry, future_contract


def test_unknown_baseline_produces_no_trial_decision():
    result = evaluate_entry({"residual_controller_seconds": None})
    assert result["decision"] == "no-trial"
    assert "unknown" in result["reason"]


def test_measured_residual_makes_a_trial_eligible():
    assert evaluate_entry({"residual_controller_seconds": 1.5})["decision"] == "eligible"


def test_contract_requires_bounds_and_preserves_abort():
    contract = future_contract()
    assert "deadline and cancellation" in contract["required"]
    assert contract["refusals"]["abort"] == 40
    assert "autonomous pixel aiming" in contract["excluded"]
