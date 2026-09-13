"""Reproducible local comparison envelope for the private task fixture.

This runner labels one selected route; it does not contact a model, provider,
desktop, or remote service.  It is useful for collecting exploratory receipts,
while economic comparisons still require two comparable verified reports.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from benchmark_task import FIXTURE_VERSION, ROUND_SHAPES, run_trial
from result_collector import EXPECTED_ACTIVITY, EXPECTED_COUNTERS, ResultCollector

VERIFICATION_POLICIES = ("sha256-artifact-v1",)


def run_comparison(
    *,
    fixture_version: str,
    seed: int,
    route: str,
    verification_policy: str,
    mode: str = "batch",
    shape: str = "full-start",
    warm: bool = False,
    delay_seconds: float = 0.0,
    fail_ids: set[str] | None = None,
    recover: bool = True,
    interrupted: bool = False,
    root: Path | None = None,
) -> dict[str, Any]:
    """Run one explicit fixture and collect every attempt receipt."""
    if fixture_version != FIXTURE_VERSION:
        raise ValueError(f"unsupported fixture version: {fixture_version}")
    if not route.strip():
        raise ValueError("route must be non-empty")
    if verification_policy not in VERIFICATION_POLICIES:
        raise ValueError(f"unsupported verification policy: {verification_policy}")
    if warm and shape == "full-start":
        shape = "warm-follow-up"
    trial_root = root or Path.cwd() / ".cu-comparison-fixture"
    trial = run_trial(
        trial_root,
        mode=mode,
        seed=seed,
        warm=warm,
        shape=shape,
        delay_seconds=delay_seconds,
        fail_ids=fail_ids,
        recover=recover,
        interrupted=interrupted,
    )
    collector = ResultCollector(
        fixture={"version": fixture_version, "seed": seed},
        run={
            "run_id": trial["trial"]["id"],
            "route": route,
            "controller": trial["controller"]["runtime"],
            "model": trial["controller"]["model"],
            "effort": trial["controller"]["effort"],
            "timing": {
                "end_to_end_wall_seconds": trial["timing"]["trial_wall_seconds"],
                "active_worker_seconds": trial["timing"]["application_seconds"],
                "cu_seconds": trial["timing"]["cu_seconds"],
                "transport_seconds": trial["timing"]["harness_transport_seconds"],
                "model_seconds": trial["timing"]["model_seconds"],
                "setup_seconds": None,
                "lead_gap_seconds": None,
            },
        },
        verification_policy=verification_policy,
    )
    for index, attempt in enumerate(trial["attempts"]):
        fixture_outcome = attempt["fixture_outcome"]
        if index == len(trial["attempts"]) - 1 and trial["outcome"] != "completed":
            fixture_outcome = trial["outcome"]
        task_receipt = {
            "attempt_id": attempt["attempt_id"],
            "status": attempt["status"],
            "retry_of": attempt["retry_of"],
            "is_retry": attempt["is_retry"],
            "paid_assistance": attempt["paid_assistance"],
            "operator_intent_satisfied": (
                attempt["operator_intent_satisfied"] if fixture_outcome == "completed" else False
            ),
            "explicit_brief_executed": attempt["explicit_brief_executed"],
            "fixture_outcome": fixture_outcome,
            "action_count": attempt["action_count"],
        }
        usage = {
            "coverage": "unknown",
            "activity": dict.fromkeys(EXPECTED_ACTIVITY),
            "counters": dict.fromkeys(EXPECTED_COUNTERS),
            "timing": attempt["timing"],
            "reported_billing": None,
            "quota_consumption": None,
        }
        collector.add(
            round_receipt={
                "job_id": attempt["job_id"],
                "round_id": attempt["round_id"],
                "attempt_id": attempt["attempt_id"],
                "task_version": attempt["task_version"],
                "context_id": attempt["context_id"],
                "shape": attempt["shape"],
                "timing": attempt["timing"],
            },
            task_receipt=task_receipt,
            verification=attempt["verification"],
            usage=usage,
        )
    return {
        "schema": "cu-comparison/1",
        "fixture": {"version": fixture_version, "seed": seed},
        "selected_route": route,
        "verification_policy": verification_policy,
        "trial": {
            "id": trial["trial"]["id"],
            "mode": trial["trial"]["mode"],
            "outcome": trial["outcome"],
            "recovery": trial["recovery"],
        },
        "accounting": collector.report(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-version", required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--route", required=True)
    parser.add_argument("--verification-policy", required=True)
    parser.add_argument("--mode", choices=("individual", "batch", "combined"), default="batch")
    parser.add_argument("--shape", choices=ROUND_SHAPES, default="full-start")
    parser.add_argument("--warm", action="store_true")
    parser.add_argument("--delay", type=float, default=0.0)
    parser.add_argument("--fail", action="append", default=[])
    parser.add_argument("--no-recovery", action="store_true")
    parser.add_argument("--interrupted", action="store_true")
    args = parser.parse_args(argv)
    import tempfile

    with tempfile.TemporaryDirectory(prefix="cu-comparison-") as directory:
        report = run_comparison(
            fixture_version=args.fixture_version,
            seed=args.seed,
            route=args.route,
            verification_policy=args.verification_policy,
            mode=args.mode,
            shape=args.shape,
            warm=args.warm,
            delay_seconds=args.delay,
            fail_ids=set(args.fail) if args.fail else None,
            recover=not args.no_recovery,
            interrupted=args.interrupted,
            root=Path(directory),
        )
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
