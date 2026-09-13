"""Small, fail-closed accounting for task and worker round receipts.

The collector accepts the public identity fields emitted by a worker round and
the independent fixture verifier.  It keeps raw backend counter semantics and
only derives values when scope and coverage prove that the arithmetic is
meaningful.  It stores one result per attempt, including failed and retried
attempts, instead of collapsing a run to its last status.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from copy import deepcopy
from math import isfinite
from typing import Any

COLLECTOR_SCHEMA = "cu-result-collector/1"
COMPARISON_SCHEMA = "cu-comparison/1"
ROUND_SHAPES = frozenset(
    {"full-start", "prepared-page", "warm-follow-up", "cold-resume"}
)
ATTEMPT_STATUSES = frozenset({"completed", "failed", "aborted", "interrupted"})
EXPECTED_COUNTERS = (
    "input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "cached_input_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
)
EXPECTED_ACTIVITY = ("tool_calls", "images", "encoded_bytes", "visual_tokens")


class AccountingError(ValueError):
    """Raised when a receipt cannot support an honest accounting result."""


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise AccountingError(f"{label} must be an object")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AccountingError(f"{label} must be a non-empty string")
    return value.strip()


def _optional_text(value: Any, label: str) -> str | None:
    if value is None:
        return None
    return _text(value, label)


def _number(value: Any, label: str, *, allow_none: bool = True) -> int | float | None:
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AccountingError(f"{label} must be a non-negative number or null")
    if isinstance(value, float) and not isfinite(value):
        raise AccountingError(f"{label} must be finite")
    if value < 0:
        raise AccountingError(f"{label} must be non-negative")
    return value


def _integer(value: Any, label: str, *, allow_none: bool = False) -> int | None:
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AccountingError(f"{label} must be a non-negative integer")
    return value


def _optional_bool(value: Any, label: str) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise AccountingError(f"{label} must be true, false, or null")
    return value


def _copy(value: Any) -> Any:
    return deepcopy(value)


def _counter(value: Any, label: str) -> dict[str, Any]:
    """Normalize one counter without inventing its backend meaning."""
    if value is None:
        return {
            "value": None,
            "semantics": "unknown",
            "scope": "unknown",
            "coverage": "unknown",
        }
    if isinstance(value, Mapping):
        result = {
            "value": _number(value.get("value"), f"{label}.value"),
            "semantics": value.get("semantics", "unknown"),
            "scope": value.get("scope", "unknown"),
            "coverage": value.get("coverage", "unknown"),
        }
        for key in ("coverage_id", "session_id", "includes_cached", "subset_of"):
            if key in value:
                result[key] = _copy(value[key])
        for key in ("semantics", "scope", "coverage"):
            if not isinstance(result[key], str) or not result[key].strip():
                raise AccountingError(f"{label}.{key} must be a non-empty string")
            result[key] = result[key].strip()
        return result
    return {
        "value": _number(value, label),
        "semantics": "unknown",
        "scope": "unknown",
        "coverage": "unknown",
    }


def _coverage_compatible(current: Mapping[str, Any], previous: Mapping[str, Any]) -> bool:
    current_coverage = current.get("coverage")
    previous_coverage = previous.get("coverage")
    if current_coverage in (None, "unknown") or previous_coverage in (None, "unknown"):
        return False
    if current_coverage != previous_coverage:
        return False
    if current_coverage != "complete":
        return (
            current.get("coverage_id") is not None
            and current.get("coverage_id") == previous.get("coverage_id")
        )
    return True


def _counter_delta(
    current: Mapping[str, Any], previous: Mapping[str, Any]
) -> tuple[dict[str, Any] | None, str | None]:
    if current.get("semantics") != "cumulative" or previous.get("semantics") != "cumulative":
        return None, "both counters must be cumulative"
    if current.get("scope") != previous.get("scope"):
        return None, "counter scopes differ"
    if current.get("scope") == "unknown":
        return None, "counter scope is unknown"
    if not _coverage_compatible(current, previous):
        return None, "counter coverage is incompatible"
    if current.get("scope") == "session":
        current_session = current.get("session_id")
        previous_session = previous.get("session_id")
        if not current_session or current_session != previous_session:
            return None, "session identities differ or are missing"
    current_value = current.get("value")
    previous_value = previous.get("value")
    if current_value is None or previous_value is None:
        return None, "counter value is unknown"
    if current_value < previous_value:
        return None, "cumulative counter decreased"
    return {
        "value": current_value - previous_value,
        "semantics": "delta",
        "scope": current["scope"],
        "coverage": current["coverage"],
        "derived_from": {"current": _copy(current), "previous": _copy(previous)},
    }, None


def derive_counter_delta(
    current: Mapping[str, Any], previous: Mapping[str, Any]
) -> dict[str, Any] | None:
    """Derive a delta only for compatible cumulative counters."""
    delta, _reason = _counter_delta(
        _mapping(current, "current counter"), _mapping(previous, "previous counter")
    )
    return delta


def _uncached_input(
    total: Mapping[str, Any], cached: Mapping[str, Any]
) -> tuple[dict[str, Any] | None, str | None]:
    total_semantics = total.get("semantics")
    cached_semantics = cached.get("semantics")
    includes_cached = total.get("includes_cached") is True or total_semantics in {
        "input_total_including_cached",
        "total_including_cached",
    }
    cached_subset = cached.get("subset_of") == "input_tokens" or cached_semantics in {
        "cached_input",
        "cache_read",
        "cached_input_tokens",
    }
    if not includes_cached or not cached_subset:
        return None, "backend cache semantics do not prove a subset of total input"
    if total.get("scope") != cached.get("scope"):
        return None, "input and cached-input scopes differ"
    if not _coverage_compatible(total, cached):
        return None, "input and cached-input coverage is incompatible"
    total_value = total.get("value")
    cached_value = cached.get("value")
    if total_value is None or cached_value is None:
        return None, "input or cached-input value is unknown"
    if total_value < cached_value:
        return None, "cached input exceeds total input"
    return {
        "value": total_value - cached_value,
        "semantics": "derived_uncached",
        "scope": total["scope"],
        "coverage": total["coverage"],
        "derived_from": {"total": _copy(total), "cached": _copy(cached)},
    }, None


def derive_uncached_input(
    total: Mapping[str, Any], cached: Mapping[str, Any]
) -> dict[str, Any] | None:
    """Derive uncached input only from explicit backend cache semantics."""
    result, _reason = _uncached_input(
        _mapping(total, "input counter"), _mapping(cached, "cached counter")
    )
    return result


def _activity(usage: Mapping[str, Any]) -> dict[str, Any]:
    raw = usage.get("activity")
    raw = raw if isinstance(raw, Mapping) else {}
    result = {
        name: _number(raw.get(name), f"activity.{name}") for name in EXPECTED_ACTIVITY
    }
    metrics = usage.get("cu_metrics")
    if not isinstance(metrics, Mapping):
        return result
    frames = metrics.get("frames")
    if not isinstance(frames, list):
        return result
    # The CU metrics frame list is intentionally bounded.  Preserve that fact
    # instead of presenting observed frames as a complete transcript count.
    if result["images"] is None:
        result["images"] = len(frames)
    if result["encoded_bytes"] is None:
        encoded = [frame.get("encoded_bytes") for frame in frames if isinstance(frame, Mapping)]
        if encoded and all(isinstance(item, int) and item >= 0 for item in encoded):
            result["encoded_bytes"] = sum(encoded)
    if result["visual_tokens"] is None:
        tokens = [frame.get("visual_tokens") for frame in frames if isinstance(frame, Mapping)]
        if tokens and all(isinstance(item, (int, float)) and item >= 0 for item in tokens):
            result["visual_tokens"] = sum(tokens)
    result["observed_coverage"] = "bounded"
    return result


def _usage(usage: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = _mapping(usage or {}, "usage receipt")
    raw_counters = raw.get("counters")
    raw_counters = raw_counters if isinstance(raw_counters, Mapping) else {}
    counters = {
        name: _counter(raw_counters.get(name), f"counters.{name}")
        for name in EXPECTED_COUNTERS
    }
    for name, value in raw_counters.items():
        if name not in counters:
            counters[_text(name, "counter name")] = _counter(value, f"counters.{name}")

    previous_raw = raw.get("previous_counters")
    previous = None
    deltas: dict[str, dict[str, Any] | None] = {}
    delta_reasons: dict[str, str] = {}
    if isinstance(previous_raw, Mapping):
        previous = {
            name: _counter(value, f"previous_counters.{name}")
            for name, value in previous_raw.items()
        }
        for name, current in counters.items():
            if name not in previous:
                continue
            delta, reason = _counter_delta(current, previous[name])
            deltas[name] = delta
            if reason is not None:
                delta_reasons[name] = reason

    total = deltas.get("input_tokens") or counters.get("input_tokens")
    cached = deltas.get("cached_input_tokens") or counters.get("cached_input_tokens")
    uncached, uncached_reason = _uncached_input(total, cached)
    timing = raw.get("timing")
    timing = _mapping(timing, "usage timing") if timing is not None else {}
    normalized_timing = {
        name: _number(timing.get(name), f"usage timing.{name}")
        for name in (
            "end_to_end_wall_seconds",
            "active_worker_seconds",
            "setup_seconds",
            "lead_gap_seconds",
            "cu_seconds",
            "application_seconds",
            "transport_seconds",
            "model_seconds",
        )
    }
    return {
        "coverage": raw.get("coverage", "unknown"),
        "activity": _activity(raw),
        "counters": counters,
        "previous_counters": previous,
        "derived": {
            "deltas": deltas,
            "delta_reasons": delta_reasons,
            "uncached_input_tokens": uncached,
            "uncached_input_reason": uncached_reason,
        },
        "timing": normalized_timing,
        "reported_billing": _copy(raw.get("reported_billing")),
        "quota_consumption": _copy(raw.get("quota_consumption")),
        "monetary_estimate": _estimate(raw.get("monetary_estimate")),
    }


def _estimate(value: Any) -> Any:
    if value is None:
        return None
    estimate = _mapping(value, "monetary estimate")
    _text(estimate.get("price_source"), "monetary estimate.price_source")
    _text(estimate.get("price_version"), "monetary estimate.price_version")
    return _copy(estimate)


def _timing(round_receipt: Mapping[str, Any], usage: Mapping[str, Any]) -> dict[str, Any]:
    raw = round_receipt.get("timing")
    raw = _mapping(raw, "round timing") if raw is not None else {}
    usage_timing = usage.get("timing", {})
    return {
        name: _number(
            raw.get(name) if name in raw else usage_timing.get(name),
            f"timing.{name}",
        )
        for name in (
            "end_to_end_wall_seconds",
            "active_worker_seconds",
            "setup_seconds",
            "lead_gap_seconds",
            "cu_seconds",
            "application_seconds",
            "transport_seconds",
            "model_seconds",
        )
    }


def _verification(
    verification: Mapping[str, Any] | None,
    task_receipt: Mapping[str, Any],
) -> tuple[dict[str, Any], bool, str]:
    raw = _mapping(verification or {}, "verification")
    expected = _integer(raw.get("expected_count"), "verification.expected_count", allow_none=True)
    actual = _integer(raw.get("actual_count"), "verification.actual_count", allow_none=True)
    failures = raw.get("failures")
    if failures is not None and not isinstance(failures, list):
        raise AccountingError("verification.failures must be a list or null")
    summary = {
        "ok": raw.get("ok") is True,
        "expected_count": expected,
        "actual_count": actual,
        "failure_count": len(failures) if failures is not None else None,
    }
    if raw.get("ok") is not True:
        return summary, False, "independent verifier did not return ok"
    if expected is None or actual is None:
        return summary, False, "independent verifier counts are incomplete"
    if expected == 0:
        return summary, False, "zero expected artifacts cannot prove a completed task"
    if actual != expected:
        return summary, False, "independent verifier counts do not match"
    if failures:
        return summary, False, "independent verifier reported failures"
    action_count = task_receipt.get("action_count")
    if action_count is not None and _integer(action_count, "task_receipt.action_count") == 0:
        return summary, False, "task receipt reports no action"
    return summary, True, "verified complete"


def _billing_result(
    usage: Mapping[str, Any],
    verified_complete: bool,
    operator_intent_satisfied: bool | None,
    explicit_brief_executed: bool | None,
) -> dict[str, Any]:
    reported = usage.get("reported_billing")
    estimate = usage.get("monetary_estimate")
    cost_per_task = None
    reason = "reported billing is unavailable"
    if not verified_complete:
        reason = "task is not independently verified complete"
    elif operator_intent_satisfied is not True:
        reason = "operator intent satisfaction is unknown or false"
    elif explicit_brief_executed is not True:
        reason = "explicit brief execution is unknown or false"
    elif isinstance(reported, Mapping):
        amount = reported.get("amount")
        scope = reported.get("scope")
        if (
            isinstance(amount, (int, float))
            and not isinstance(amount, bool)
            and isfinite(amount)
            and amount >= 0
            and isinstance(reported.get("currency"), str)
            and bool(reported["currency"].strip())
            and scope in {"attempt", "round", "run"}
        ):
            cost_per_task = {
                "amount": amount,
                "currency": reported.get("currency"),
                "source": "reported",
            }
            reason = "reported billing is attached to a verified complete attempt"
        else:
            reason = "reported billing has no compatible amount and scope"
    return {
        "reported_billing": _copy(reported),
        "monetary_estimate": _copy(estimate),
        "cost_per_completed_task": cost_per_task,
        "reason": reason,
    }


def collect_result(
    *,
    fixture: Mapping[str, Any],
    run: Mapping[str, Any],
    round_receipt: Mapping[str, Any],
    task_receipt: Mapping[str, Any],
    verification: Mapping[str, Any] | None = None,
    usage: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Collect one attempt without collapsing controller and application facts."""
    fixture = _mapping(fixture, "fixture")
    run = _mapping(run, "run")
    round_receipt = _mapping(round_receipt, "round receipt")
    task_receipt = _mapping(task_receipt, "task receipt")
    fixture_version = _text(fixture.get("version"), "fixture.version")
    seed = _integer(fixture.get("seed"), "fixture.seed")
    run_id = _text(run.get("run_id"), "run.run_id")
    job_id = _text(round_receipt.get("job_id"), "round receipt.job_id")
    round_id = _text(round_receipt.get("round_id"), "round receipt.round_id")
    attempt_id = _text(round_receipt.get("attempt_id"), "round receipt.attempt_id")
    task_attempt_id = _text(task_receipt.get("attempt_id"), "task receipt.attempt_id")
    if attempt_id != task_attempt_id:
        raise AccountingError("round and task receipts name different attempts")
    shape = _text(round_receipt.get("shape"), "round receipt.shape")
    if shape not in ROUND_SHAPES:
        raise AccountingError(f"unsupported round shape: {shape}")
    status = _text(task_receipt.get("status"), "task receipt.status")
    if status not in ATTEMPT_STATUSES:
        raise AccountingError(f"unsupported task receipt status: {status}")
    retry_of = task_receipt.get("retry_of")
    if retry_of is not None:
        retry_of = _text(retry_of, "task receipt.retry_of")
        if retry_of == attempt_id:
            raise AccountingError("an attempt cannot retry itself")
    is_retry = task_receipt.get("is_retry")
    if is_retry is None:
        is_retry = retry_of is not None
    if not isinstance(is_retry, bool):
        raise AccountingError("task receipt.is_retry must be true, false, or null")
    paid_assistance = _optional_bool(
        task_receipt.get("paid_assistance"), "task receipt.paid_assistance"
    )
    usage = _usage(usage)
    verification_summary, verified_complete, verification_reason = _verification(
        verification or task_receipt.get("verification"), task_receipt
    )
    fixture_outcome = task_receipt.get("fixture_outcome", "unknown")
    fixture_outcome = _text(fixture_outcome, "task receipt.fixture_outcome")
    operator_intent_satisfied = _optional_bool(
        task_receipt.get("operator_intent_satisfied"),
        "task receipt.operator_intent_satisfied",
    )
    explicit_brief_executed = _optional_bool(
        task_receipt.get("explicit_brief_executed"),
        "task receipt.explicit_brief_executed",
    )
    if status != "completed":
        verified_complete = False
        verification_reason = f"attempt status is {status}"
    if fixture_outcome != "completed":
        verified_complete = False
        if verification_reason == "verified complete":
            verification_reason = f"fixture outcome is {fixture_outcome}"
    return {
        "schema": COLLECTOR_SCHEMA,
        "join": {
            "fixture_version": fixture_version,
            "fixture_seed": seed,
            "run_id": run_id,
            "job_id": job_id,
            "round_id": round_id,
            "attempt_id": attempt_id,
            "task_version": _optional_text(
                round_receipt.get("task_version"), "round receipt.task_version"
            ),
            "context_id": _optional_text(
                round_receipt.get("context_id"), "round receipt.context_id"
            ),
        },
        "shape": shape,
        "attempt": {
            "status": status,
            "is_retry": is_retry,
            "retry_of": retry_of,
            "paid_assistance": paid_assistance,
        },
        "layers": {
            "operator_intent_satisfied": operator_intent_satisfied,
            "explicit_brief_executed": explicit_brief_executed,
            "fixture_application_outcome": fixture_outcome,
        },
        "verification": {
            **verification_summary,
            "verified_complete": verified_complete,
            "reason": verification_reason,
        },
        "timing": _timing(round_receipt, usage),
        "usage": usage,
        "billing": _billing_result(
            usage,
            verified_complete,
            operator_intent_satisfied,
            explicit_brief_executed,
        ),
    }


class ResultCollector:
    """Collect all attempts for one fixture/run while retaining their joins."""

    def __init__(
        self,
        *,
        fixture: Mapping[str, Any],
        run: Mapping[str, Any],
        verification_policy: str,
    ) -> None:
        self.fixture = {
            "version": _text(fixture.get("version"), "fixture.version"),
            "seed": _integer(fixture.get("seed"), "fixture.seed"),
        }
        self.run = {"run_id": _text(run.get("run_id"), "run.run_id")}
        for key in ("route", "controller", "model", "effort", "timing"):
            if key in run:
                self.run[key] = _copy(run[key])
        self.verification_policy = _text(verification_policy, "verification_policy")
        self._attempts: list[dict[str, Any]] = []
        self._keys: set[tuple[str, str, str, str, str]] = set()

    def add(
        self,
        *,
        round_receipt: Mapping[str, Any],
        task_receipt: Mapping[str, Any],
        verification: Mapping[str, Any] | None = None,
        usage: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        result = collect_result(
            fixture=self.fixture,
            run=self.run,
            round_receipt=round_receipt,
            task_receipt=task_receipt,
            verification=verification,
            usage=usage,
        )
        join = result["join"]
        key = (
            join["fixture_version"],
            str(join["fixture_seed"]),
            join["run_id"],
            join["round_id"],
            join["attempt_id"],
        )
        if key in self._keys:
            raise AccountingError("duplicate fixture/run/round/attempt receipt")
        self._keys.add(key)
        self._attempts.append(result)
        return result

    def report(self) -> dict[str, Any]:
        statuses = Counter(item["attempt"]["status"] for item in self._attempts)
        retries = sum(item["attempt"]["is_retry"] is True for item in self._attempts)
        paid = sum(item["attempt"]["paid_assistance"] is True for item in self._attempts)
        verified = sum(item["verification"]["verified_complete"] is True for item in self._attempts)
        economic = _report_economic(self._attempts)
        return {
            "schema": COLLECTOR_SCHEMA,
            "fixture": _copy(self.fixture),
            "run": _copy(self.run),
            "verification_policy": self.verification_policy,
            "attempts": _copy(self._attempts),
            "summary": {
                "attempt_count": len(self._attempts),
                "status_counts": dict(statuses),
                "retry_count": retries,
                "paid_assistance_count": paid,
                "verified_completed_attempts": verified,
            },
            "economic": economic,
        }


def _report_economic(attempts: list[Mapping[str, Any]]) -> dict[str, Any]:
    completed = [
        item for item in attempts if item.get("verification", {}).get("verified_complete") is True
    ]
    if len(completed) != 1:
        return {
            "cost_per_completed_task": None,
            "reason": "exactly one comparable verified completion is required",
        }
    billed = []
    for item in attempts:
        reported = item.get("billing", {}).get("reported_billing")
        if not isinstance(reported, Mapping):
            return {
                "cost_per_completed_task": None,
                "reason": "every attempt needs actual reported billing before aggregation",
            }
        amount = reported.get("amount")
        scope = reported.get("scope")
        currency = reported.get("currency")
        if (
            not isinstance(amount, (int, float))
            or isinstance(amount, bool)
            or not isfinite(amount)
            or amount < 0
            or not isinstance(currency, str)
            or not currency.strip()
            or scope not in {"attempt", "round", "run"}
        ):
            return {
                "cost_per_completed_task": None,
                "reason": "reported billing has incompatible amount or scope",
            }
        billed.append((amount, currency))
    currencies = {currency for _amount, currency in billed}
    if len(currencies) != 1:
        return {
            "cost_per_completed_task": None,
            "reason": "reported billing currencies are incompatible",
        }
    return {
        "cost_per_completed_task": {
            "amount": sum(amount for amount, _currency in billed),
            "currency": billed[0][1],
            "source": "reported",
            "attempt_count": len(attempts),
        },
        "reason": "all attempts have compatible reported billing and one verified completion",
    }


def compare_results(left: Mapping[str, Any], right: Mapping[str, Any]) -> dict[str, Any]:
    """Compare two reports only when fixture and verification contracts match."""
    left = _mapping(left, "left comparison")
    right = _mapping(right, "right comparison")
    if left.get("fixture") != right.get("fixture"):
        return {
            "schema": COMPARISON_SCHEMA,
            "comparable": False,
            "reason": "fixture version or seed differs",
        }
    if left.get("verification_policy") != right.get("verification_policy"):
        return {
            "schema": COMPARISON_SCHEMA,
            "comparable": False,
            "reason": "verification policy differs",
        }
    left_attempts = left.get("attempts")
    right_attempts = right.get("attempts")
    if not isinstance(left_attempts, list) or not isinstance(right_attempts, list):
        return {
            "schema": COMPARISON_SCHEMA,
            "comparable": False,
            "reason": "attempt receipts are incomplete",
        }
    left_shapes = {item.get("shape") for item in left_attempts}
    right_shapes = {item.get("shape") for item in right_attempts}
    if left_shapes != right_shapes:
        return {
            "schema": COMPARISON_SCHEMA,
            "comparable": False,
            "reason": "round shapes differ",
        }
    left_complete = any(
        item.get("verification", {}).get("verified_complete") is True
        for item in left_attempts
    )
    right_complete = any(
        item.get("verification", {}).get("verified_complete") is True
        for item in right_attempts
    )
    if not left_complete or not right_complete:
        return {
            "schema": COMPARISON_SCHEMA,
            "comparable": False,
            "reason": "both sides need independently verified complete outcomes",
        }
    return {
        "schema": COMPARISON_SCHEMA,
        "comparable": True,
        "fixture": _copy(left["fixture"]),
        "verification_policy": left["verification_policy"],
        "economic": {
            "cost_per_completed_task": None,
            "reason": "actual reported billing is unavailable or must be supplied per attempt",
        },
    }
