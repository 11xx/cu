"""Conditional bounded-executor evaluation contract."""

from __future__ import annotations

import argparse
import json
import math
from typing import Any

TRIAL_SCHEMA = "cu-executor-trial/1"


def evaluate_entry(baseline: dict[str, Any]) -> dict[str, Any]:
    """Return no-trial for incomplete or non-action accounting evidence."""
    residual = baseline.get("residual_controller_seconds")
    if (
        not isinstance(residual, (int, float))
        or isinstance(residual, bool)
        or not math.isfinite(residual)
        or residual < 0
    ):
        return {
            "schema": TRIAL_SCHEMA,
            "decision": "no-trial",
            "reason": "residual controller cost is unknown",
            "baseline": baseline,
        }
    outcome = baseline.get("outcome")
    if outcome is not None and outcome != "completed":
        return {
            "schema": TRIAL_SCHEMA,
            "decision": "no-trial",
            "reason": f"fixture outcome is {outcome!r}, not independently complete",
            "baseline": baseline,
        }
    verification = baseline.get("verification")
    if isinstance(verification, dict):
        if verification.get("ok") is not True:
            return {
                "schema": TRIAL_SCHEMA,
                "decision": "no-trial",
                "reason": "independent verifier did not confirm completion",
                "baseline": baseline,
            }
        expected = verification.get("expected_count")
        actual = verification.get("actual_count")
        if not isinstance(expected, int) or expected <= 0 or actual != expected:
            return {
                "schema": TRIAL_SCHEMA,
                "decision": "no-trial",
                "reason": "completion counts are missing, zero, or mismatched",
                "baseline": baseline,
            }
        if verification.get("failures"):
            return {
                "schema": TRIAL_SCHEMA,
                "decision": "no-trial",
                "reason": "independent verifier reported failures",
                "baseline": baseline,
            }
    return {
        "schema": TRIAL_SCHEMA,
        "decision": "eligible",
        "reason": "measured residual controller cost is available",
        "baseline": baseline,
    }


def future_contract() -> dict[str, Any]:
    """Describe the bounded trial without authorizing a run."""
    return {
        "schema": TRIAL_SCHEMA,
        "intent": "caller-authorized semantic task with validated targets",
        "required": [
            "fixture version and seed",
            "run, job, round and attempt identity",
            "operator intent and explicit brief result",
            "full-start, prepared-page, warm-follow-up or cold-resume shape",
            "explicit target and destination",
            "deadline and cancellation",
            "exclusive reservation ownership",
            "child-death cleanup or lifecycle expiry",
            "partial-action receipts without replay",
            "complete-task verifier result",
            "per-round usage receipt with counter semantics and coverage",
            "reported billing separate from quota consumption and estimates",
        ],
        "refusals": {"abort": 40, "stale_or_missing_target": "retryable"},
        "excluded": ["autonomous pixel aiming", "permanent input device", "daemon", "queue"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--residual-seconds", type=float)
    args = parser.parse_args()
    baseline = {"residual_controller_seconds": args.residual_seconds}
    print(json.dumps({"entry": evaluate_entry(baseline), "contract": future_contract()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
