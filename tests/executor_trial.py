"""Conditional bounded-executor evaluation contract."""

from __future__ import annotations

import argparse
import json
from typing import Any

TRIAL_SCHEMA = "cu-executor-trial/1"


def evaluate_entry(baseline: dict[str, Any]) -> dict[str, Any]:
    """Return no-trial until a measured residual controller cost exists."""
    residual = baseline.get("residual_controller_seconds")
    if not isinstance(residual, (int, float)) or residual < 0:
        return {
            "schema": TRIAL_SCHEMA,
            "decision": "no-trial",
            "reason": "residual controller cost is unknown",
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
            "explicit target and destination",
            "deadline and cancellation",
            "exclusive reservation ownership",
            "child-death cleanup or lifecycle expiry",
            "partial-action receipts without replay",
            "complete-task verifier result",
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
