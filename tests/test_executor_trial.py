"""Conditional executor evaluation stays negative while measurements are unknown."""

from __future__ import annotations

from benchmark_task import run_trial, verify
from comparison_runner import run_comparison
from executor_trial import evaluate_entry, future_contract
from result_collector import (
    AccountingError,
    ResultCollector,
    compare_results,
    derive_counter_delta,
    derive_uncached_input,
)


def test_unknown_baseline_produces_no_trial_decision():
    result = evaluate_entry({"residual_controller_seconds": None})
    assert result["decision"] == "no-trial"
    assert "unknown" in result["reason"]


def test_measured_residual_makes_a_trial_eligible():
    assert evaluate_entry({"residual_controller_seconds": 1.5})["decision"] == "eligible"


def test_incomplete_and_no_action_results_cannot_be_trial_eligible(tmp_path):
    incomplete = run_trial(tmp_path / "incomplete", recover=False)
    decision = evaluate_entry(
        {
            "residual_controller_seconds": 1.0,
            "outcome": incomplete["outcome"],
            "verification": incomplete["verification"],
        }
    )
    assert decision["decision"] == "no-trial"
    assert "incomplete" in decision["reason"]
    zero = evaluate_entry(
        {
            "residual_controller_seconds": 0.0,
            "outcome": "success",
            "verification": {"ok": True, "expected_count": 0, "actual_count": 0},
        }
    )
    assert zero["decision"] == "no-trial"


def test_cumulative_and_cached_counters_require_compatible_semantics():
    def cumulative(value, *, coverage="complete", coverage_id=None):
        result = {
            "value": value,
            "semantics": "cumulative",
            "scope": "session",
            "coverage": coverage,
            "session_id": "session-1",
        }
        if coverage_id is not None:
            result["coverage_id"] = coverage_id
        return result

    assert derive_counter_delta(cumulative(100), cumulative(40))["value"] == 60
    assert derive_counter_delta(
        cumulative(100, coverage="complete"),
        cumulative(40, coverage="bounded", coverage_id="read-1"),
    ) is None
    total = {
        "value": 100,
        "semantics": "total_including_cached",
        "scope": "round",
        "coverage": "complete",
    }
    cached = {
        "value": 30,
        "semantics": "cached_input",
        "scope": "round",
        "coverage": "complete",
    }
    assert derive_uncached_input(total, cached)["value"] == 70
    assert derive_uncached_input(total, {**cached, "coverage": "unknown"}) is None


def test_collector_keeps_all_attempts_and_separates_billing_from_quota():
    collector = ResultCollector(
        fixture={"version": "fixture/1", "seed": 3},
        run={"run_id": "run-1", "route": "fake"},
        verification_policy="sha256-artifact-v1",
    )

    def add_attempt(attempt_id, status, *, retry_of=None, paid=False, verified=False):
        verification = {
            "ok": verified,
            "expected_count": 1,
            "actual_count": 1 if verified else 0,
            "failures": [] if verified else [{"kind": "missing-artifact"}],
        }
        collector.add(
            round_receipt={
                "job_id": "job-1",
                "round_id": f"round-{attempt_id}",
                "attempt_id": attempt_id,
                "task_version": "task-1",
                "context_id": "context-1",
                "shape": "prepared-page",
                "timing": {
                    "end_to_end_wall_seconds": 2.0,
                    "active_worker_seconds": 1.0,
                    "setup_seconds": 0.5,
                    "lead_gap_seconds": 0.5,
                },
            },
            task_receipt={
                "attempt_id": attempt_id,
                "status": status,
                "retry_of": retry_of,
                "is_retry": retry_of is not None,
                "paid_assistance": paid,
                "operator_intent_satisfied": verified,
                "explicit_brief_executed": True,
                "fixture_outcome": "completed" if verified else "incomplete",
                "action_count": 1 if verified else 0,
            },
            verification=verification,
            usage={
                "counters": {
                    "input_tokens": None,
                    "output_tokens": None,
                    "reasoning_tokens": None,
                    "cached_input_tokens": None,
                },
                "reported_billing": (
                    {"amount": 2.5, "currency": "USD", "scope": "attempt"}
                    if verified
                    else None
                ),
                "quota_consumption": {"window": "fake", "units": 3},
            },
        )

    add_attempt("attempt-1", "failed")
    add_attempt("attempt-2", "aborted", retry_of="attempt-1")
    add_attempt("attempt-3", "completed", retry_of="attempt-2", paid=True, verified=True)
    report = collector.report()
    assert report["summary"]["attempt_count"] == 3
    assert report["summary"]["status_counts"] == {
        "failed": 1,
        "aborted": 1,
        "completed": 1,
    }
    assert report["summary"]["retry_count"] == 2
    assert report["summary"]["paid_assistance_count"] == 1
    assert report["attempts"][0]["verification"]["verified_complete"] is False
    assert report["attempts"][2]["billing"]["cost_per_completed_task"]["amount"] == 2.5
    assert report["attempts"][2]["usage"]["quota_consumption"] == {
        "window": "fake",
        "units": 3,
    }


def test_collector_rejects_duplicate_joins_and_unversioned_estimates():
    collector = ResultCollector(
        fixture={"version": "fixture/1", "seed": 1},
        run={"run_id": "run-1"},
        verification_policy="sha256-artifact-v1",
    )
    kwargs = {
        "round_receipt": {
            "job_id": "job-1",
            "round_id": "round-1",
            "attempt_id": "attempt-1",
            "shape": "full-start",
        },
        "task_receipt": {
            "attempt_id": "attempt-1",
            "status": "completed",
            "fixture_outcome": "completed",
            "action_count": 1,
        },
        "verification": {"ok": True, "expected_count": 1, "actual_count": 1, "failures": []},
        "usage": {
            "monetary_estimate": {"amount": 1.0},
        },
    }
    try:
        collector.add(**kwargs)
    except AccountingError as exc:
        assert "price_source" in str(exc)
    else:
        raise AssertionError("unversioned monetary estimate was accepted")
    kwargs["usage"] = {}
    collector.add(**kwargs)
    try:
        collector.add(**kwargs)
    except AccountingError as exc:
        assert "duplicate" in str(exc)
    else:
        raise AssertionError("duplicate receipt join was accepted")


def test_comparison_runner_requires_matching_verified_fixture(tmp_path):
    report = run_comparison(
        fixture_version="cu-task-fixture/1",
        seed=4,
        route="local-fixture",
        verification_policy="sha256-artifact-v1",
        root=tmp_path / "run",
    )
    assert report["selected_route"] == "local-fixture"
    assert report["accounting"]["summary"]["retry_count"] == 1
    assert report["accounting"]["summary"]["verified_completed_attempts"] == 1
    assert report["accounting"]["run"]["timing"]["end_to_end_wall_seconds"] >= 0
    assert report["routine"]["resume"]["context_id"] == report["routine"]["context"]["context_id"]
    assert report["routine"]["eventual"]["eventual_outcome"] == "completed"
    assert compare_results(report["accounting"], report["accounting"])["comparable"] is True
    incomplete = run_comparison(
        fixture_version="cu-task-fixture/1",
        seed=4,
        route="local-fixture",
        verification_policy="sha256-artifact-v1",
        recover=False,
        root=tmp_path / "incomplete",
    )
    assert compare_results(incomplete["accounting"], report["accounting"])["comparable"] is False
    warm = run_comparison(
        fixture_version="cu-task-fixture/1",
        seed=4,
        route="local-fixture",
        verification_policy="sha256-artifact-v1",
        shape="warm-follow-up",
        root=tmp_path / "warm",
    )
    assert compare_results(report["accounting"], warm["accounting"])["comparable"] is False


def test_pre_repair_swapped_contents_are_still_rejected(tmp_path):
    run_trial(tmp_path, recover=True)
    output = tmp_path / "artifacts"
    first = output / "item-01" / "README.md"
    second = output / "item-02" / "README.md"
    first_bytes, second_bytes = first.read_bytes(), second.read_bytes()
    first.write_bytes(second_bytes)
    second.write_bytes(first_bytes)
    result = verify(output)
    assert result["ok"] is False
    assert {failure["kind"] for failure in result["failures"]} >= {"artifact-mismatch"}


def test_contract_requires_bounds_and_preserves_abort():
    contract = future_contract()
    assert "deadline and cancellation" in contract["required"]
    assert contract["refusals"]["abort"] == 40
    assert "autonomous pixel aiming" in contract["excluded"]
