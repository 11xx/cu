"""Deterministic 16-item task fixture and completion verifier.

The fixture models application outputs only. It does not contact a service or
claim that a simulated chooser is a native desktop interaction.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

FIXTURE_VERSION = "cu-task-fixture/1"
PRESERVED_RELATIVE = Path("pre-existing") / "README.md"
PRESERVED_CONTENT = "pre-existing fixture data; keep this file unchanged\n"
FORMATS = ("md", "txt", "js", "json", "c")
ROUND_SHAPES = ("full-start", "prepared-page", "warm-follow-up", "cold-resume")
NAMES = (
    "README.md",
    "README.md",
    "notes.txt",
    "notes.txt",
    "player.js",
    "settings.json",
    "scanner.c",
    "guide.md",
    "summary.txt",
    "worker.js",
    "data.json",
    "parser.c",
    "manual.md",
    "report.txt",
    "controls.js",
    "state.json",
)


def expected_items(seed: int = 1) -> list[dict[str, Any]]:
    """Return stable identities, formats, names, and content digests."""
    items = []
    for index, name in enumerate(NAMES, 1):
        extension = name.rsplit(".", 1)[-1]
        content = f"fixture-seed={seed}; artifact=item-{index:02d}; format={extension}\n"
        if index == 2:
            content += "different-content-collision\n"
        encoded = content.encode()
        items.append(
            {
                "id": f"item-{index:02d}",
                "basename": name,
                "format": extension,
                "size": len(encoded),
                "sha256": hashlib.sha256(encoded).hexdigest(),
                "content": content,
            }
        )
    return items


def _write_item(root: Path, item: dict[str, Any]) -> Path:
    path = root / item["id"] / item["basename"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(item["content"])
    return path


def _relative_artifact(item: dict[str, Any]) -> Path:
    """Return the fixture path, rejecting names that escape their item scope."""
    basename = Path(item["basename"])
    if basename.name != item["basename"] or basename.suffix.lstrip(".") != item["format"]:
        raise ValueError(f"invalid artifact name/format for {item['id']!r}")
    item_id = Path(item["id"])
    if item_id.name != item["id"] or item_id in {Path("."), Path("..")}:
        raise ValueError(f"invalid artifact id {item['id']!r}")
    return item_id / basename


def _preservation_receipt(root: Path, items: list[dict[str, Any]]) -> dict[str, Any]:
    """Create a differing pre-existing file outside the produced artifact tree."""
    path = root / PRESERVED_RELATIVE
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(PRESERVED_CONTENT)
    before = path.read_bytes()
    expected = {item["sha256"] for item in items}
    return {
        "path": str(PRESERVED_RELATIVE),
        "before_sha256": hashlib.sha256(before).hexdigest(),
        "before_size": len(before),
        "different_from_expected": hashlib.sha256(before).hexdigest() not in expected,
        "_path": path,
    }


def _finish_preservation(receipt: dict[str, Any]) -> dict[str, Any]:
    path = receipt.pop("_path")
    try:
        after = path.read_bytes()
    except OSError:
        receipt.update({"status": "missing", "ok": False})
        return receipt
    after_sha256 = hashlib.sha256(after).hexdigest()
    receipt.update(
        {
            "after_sha256": after_sha256,
            "after_size": len(after),
            "status": "unchanged" if after_sha256 == receipt["before_sha256"] else "changed",
        }
    )
    receipt["ok"] = (
        receipt["status"] == "unchanged" and receipt["different_from_expected"]
    )
    return receipt


def verify(root: Path, items: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Verify exact paths, formats, content, count, and stability of artifacts."""
    items = items or expected_items()
    expected = Counter(item["sha256"] for item in items)
    failures: list[dict[str, Any]] = []
    expected_by_path: dict[Path, dict[str, Any]] = {}
    for item in items:
        try:
            relative = _relative_artifact(item)
        except (KeyError, TypeError, ValueError) as exc:
            failures.append({"kind": "invalid-expected-artifact", "detail": str(exc)})
            continue
        if relative in expected_by_path:
            failures.append({"kind": "duplicate-expected-path", "path": str(relative)})
        expected_by_path[relative] = item
    files = sorted(path for path in root.rglob("*") if path.is_file())
    actual: Counter[str] = Counter()
    for path in files:
        relative = path.relative_to(root)
        item = expected_by_path.get(relative)
        if item is None:
            failures.append({"kind": "unexpected-artifact", "path": str(relative)})
        elif path.suffix.lstrip(".") != item["format"]:
            failures.append(
                {
                    "kind": "format-mismatch",
                    "path": str(relative),
                    "expected": item["format"],
                    "actual": path.suffix.lstrip("."),
                }
            )
        before = path.stat()
        data = path.read_bytes()
        after = path.stat()
        if before.st_size == 0:
            failures.append({"kind": "zero-byte", "path": str(path.relative_to(root))})
            continue
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            failures.append({"kind": "changing", "path": str(path.relative_to(root))})
            continue
        digest = hashlib.sha256(data).hexdigest()
        if item is not None and (
            before.st_size != item["size"] or digest != item["sha256"]
        ):
            failures.append(
                {
                    "kind": "artifact-mismatch",
                    "path": str(relative),
                    "id": item["id"],
                    "expected_size": item["size"],
                    "actual_size": before.st_size,
                    "expected_sha256": item["sha256"],
                    "actual_sha256": digest,
                }
            )
        actual[digest] += 1
    for relative in sorted(set(expected_by_path) - {path.relative_to(root) for path in files}):
        failures.append(
            {
                "kind": "missing-artifact",
                "path": str(relative),
                "id": expected_by_path[relative]["id"],
            }
        )
    missing = list((expected - actual).elements())
    unexpected = list((actual - expected).elements())
    for digest in missing:
        failures.append({"kind": "missing-or-wrong-content", "sha256": digest})
    for digest in unexpected:
        failures.append({"kind": "wrong-or-duplicate-content", "sha256": digest})
    complete = not failures and actual == expected
    return {
        "ok": complete,
        "expected_count": sum(expected.values()),
        "actual_count": sum(actual.values()),
        "expected_unique_digests": len(expected),
        "actual_unique_digests": len(actual),
        "failures": failures,
    }


def run_trial(
    root: Path,
    *,
    mode: str = "individual",
    seed: int = 1,
    warm: bool = False,
    shape: str | None = None,
    delay_seconds: float = 0.0,
    fail_ids: set[str] | None = None,
    recover: bool = True,
    unexpected_dialog: bool = True,
    interrupted: bool = False,
) -> dict[str, Any]:
    """Run one local fixture trial and return a bounded benchmark envelope.

    The fixture models an initial attempt and, when recovery is enabled, a
    separate retry attempt.  The first failure remains in ``attempts``; it is
    never rewritten into the later successful result.
    """
    if mode not in {"individual", "batch", "combined"}:
        raise ValueError("mode must be individual, batch, or combined")
    if shape is None:
        shape = "warm-follow-up" if warm else "full-start"
    if shape not in ROUND_SHAPES:
        raise ValueError(f"shape must be one of {', '.join(ROUND_SHAPES)}")
    if warm and shape != "warm-follow-up":
        raise ValueError("warm=True requires the warm-follow-up round shape")
    if delay_seconds < 0 or delay_seconds > 10:
        raise ValueError("delay_seconds must be between 0 and 10")
    items = expected_items(seed)
    by_id = {item["id"]: item for item in items}
    fail_ids = set(fail_ids) if fail_ids is not None else {"item-03"}
    unknown = fail_ids - by_id.keys()
    if unknown:
        raise ValueError(f"unknown failure ids: {', '.join(sorted(unknown))}")
    started = time.perf_counter()
    trial_id = uuid.uuid4().hex
    task_version = f"{FIXTURE_VERSION}:{seed}"
    context_id = f"{trial_id}:context"
    receipts: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    recovery: list[str] = []
    attempts: list[dict[str, Any]] = []
    output = root / "artifacts"
    output.mkdir(parents=True, exist_ok=True)
    preservation = _preservation_receipt(root, items)
    application_started = time.perf_counter()
    initial_started = time.perf_counter()
    batches = [[item] for item in items] if mode == "individual" else [items]
    processed = 0
    interrupted_at: str | None = None
    for batch in batches:
        for item in batch:
            if interrupted and processed >= max(1, len(items) // 2):
                interrupted_at = item["id"]
                break
            if item["id"] in fail_ids:
                failures.append(
                    {"id": item["id"], "stage": "bulk" if mode != "individual" else "item"}
                )
                receipts.append(
                    {
                        "id": item["id"],
                        "status": "failed",
                        "attempt_id": f"{trial_id}:attempt-1",
                    }
                )
                processed += 1
                continue
            if delay_seconds and item["id"] == "item-07":
                time.sleep(delay_seconds)
            _write_item(output, item)
            receipts.append(
                {
                    "id": item["id"],
                    "status": "completed",
                    "attempt_id": f"{trial_id}:attempt-1",
                }
            )
            processed += 1
        if interrupted_at is not None:
            break
    initial_application_seconds = time.perf_counter() - application_started
    initial_verification = verify(output, items)
    initial_status = "aborted" if interrupted_at is not None else (
        "failed" if failures else "completed"
    )
    initial_outcome = "aborted" if interrupted_at is not None else (
        "completed" if initial_verification["ok"] else "incomplete"
    )
    attempts.append(
        {
            "job_id": trial_id,
            "round_id": f"{trial_id}:round-1",
            "attempt_id": f"{trial_id}:attempt-1",
            "task_version": task_version,
            "context_id": context_id,
            "shape": shape,
            "status": initial_status,
            "is_retry": False,
            "retry_of": None,
            "paid_assistance": False,
            "operator_intent_satisfied": initial_outcome == "completed",
            "explicit_brief_executed": True,
            "fixture_outcome": initial_outcome,
            "action_count": sum(receipt["status"] == "completed" for receipt in receipts),
            "verification": initial_verification,
            "interrupted_at": interrupted_at,
            "timing": {
                "end_to_end_wall_seconds": time.perf_counter() - initial_started,
                "active_worker_seconds": initial_application_seconds,
                "application_seconds": initial_application_seconds,
                "setup_seconds": None,
                "lead_gap_seconds": None,
                "cu_seconds": None,
                "transport_seconds": None,
                "model_seconds": None,
            },
        }
    )
    if recover and failures and interrupted_at is None:
        retry_started = time.perf_counter()
        retry_application_started = time.perf_counter()
        for failure in failures:
            item = by_id[failure["id"]]
            _write_item(output, item)
            recovery.append(item["id"])
            receipts.append(
                {
                    "id": item["id"],
                    "status": "recovered",
                    "attempt_id": f"{trial_id}:attempt-2",
                    "retry_of": f"{trial_id}:attempt-1",
                }
            )
        retry_application_seconds = time.perf_counter() - retry_application_started
        retry_verification = verify(output, items)
        attempts.append(
            {
                "job_id": trial_id,
                "round_id": f"{trial_id}:round-2",
                "attempt_id": f"{trial_id}:attempt-2",
                "task_version": task_version,
                "context_id": context_id,
                "shape": shape,
                "status": "completed" if retry_verification["ok"] else "failed",
                "is_retry": True,
                "retry_of": f"{trial_id}:attempt-1",
                "paid_assistance": False,
                "operator_intent_satisfied": retry_verification["ok"],
                "explicit_brief_executed": True,
                "fixture_outcome": "completed" if retry_verification["ok"] else "incomplete",
                "action_count": len(recovery),
                "verification": retry_verification,
                "interrupted_at": None,
                "timing": {
                    "end_to_end_wall_seconds": time.perf_counter() - retry_started,
                    "active_worker_seconds": retry_application_seconds,
                    "application_seconds": retry_application_seconds,
                    "setup_seconds": None,
                    "lead_gap_seconds": None,
                    "cu_seconds": None,
                    "transport_seconds": None,
                    "model_seconds": None,
                },
            }
        )
    application_seconds = time.perf_counter() - application_started
    verification = verify(output, items)
    preservation = _finish_preservation(preservation)
    if attempts[-1]["status"] == "completed" and not preservation["ok"]:
        attempts[-1]["fixture_outcome"] = "incomplete"
        attempts[-1]["operator_intent_satisfied"] = False
    envelope = {
        "schema": "cu-task-benchmark/1",
        "fixture": {
            "version": FIXTURE_VERSION,
            "seed": seed,
            "items": len(items),
            "formats": sorted({item["format"] for item in items}),
            "duplicate_basenames": sorted(
                name for name, count in Counter(item["basename"] for item in items).items()
                if count > 1
            ),
            "warm": warm,
            "shape": shape,
        },
        "trial": {"id": trial_id, "mode": mode},
        "controller": {
            "runtime": os.environ.get("CU_CONTROLLER", "unknown"),
            "model": os.environ.get("CU_MODEL"),
            "effort": os.environ.get("CU_EFFORT"),
        },
        "outcome": (
            "aborted"
            if interrupted_at is not None
            else "completed" if verification["ok"] and preservation["ok"] else "incomplete"
        ),
        "verification": verification,
        "preservation": {"policy": "outside-artifacts-root", "receipts": [preservation]},
        "receipts": receipts,
        "failures": failures,
        "recovery": {
            "attempted": recover and interrupted_at is None,
            "ids": recovery,
        },
        "attempts": attempts,
        "unexpected_dialog": (
            {"status": "observed", "kind": "simulated-native-dialog"}
            if unexpected_dialog
            else {"status": "not-present"}
        ),
        "timing": {
            "trial_wall_seconds": time.perf_counter() - started,
            "application_seconds": application_seconds,
            "cu_seconds": None,
            "harness_transport_seconds": None,
            "model_seconds": None,
        },
        "cost": {"cu_visual_tokens": None, "monetary_billing": None, "quota_consumption": None},
        "correlation": {
            "ground_truth": f"{FIXTURE_VERSION}:{seed}",
            "cu_invocation_id": os.environ.get("CU_INVOCATION_ID"),
            "harness_session": os.environ.get("CU_HARNESS_SESSION"),
        },
    }
    return envelope


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("individual", "batch", "combined"), default="individual")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--warm", action="store_true")
    parser.add_argument("--shape", choices=ROUND_SHAPES)
    parser.add_argument("--delay", type=float, default=0.0)
    parser.add_argument("--fail", action="append", default=[])
    parser.add_argument("--no-recovery", action="store_true")
    parser.add_argument("--no-dialog", action="store_true")
    parser.add_argument("--interrupted", action="store_true")
    args = parser.parse_args(argv)
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    fixture_root = Path(
        tempfile.mkdtemp(
            prefix="cu-task-",
            dir=runtime_dir if runtime_dir and os.access(runtime_dir, os.W_OK) else None,
        )
    )
    try:
        envelope = run_trial(
            fixture_root,
            mode=args.mode,
            seed=args.seed,
            warm=args.warm,
            shape=args.shape,
            delay_seconds=args.delay,
            fail_ids=set(args.fail) or None,
            recover=not args.no_recovery,
            unexpected_dialog=not args.no_dialog,
            interrupted=args.interrupted,
        )
        print(json.dumps(envelope, indent=2))
    finally:
        shutil.rmtree(fixture_root)
    return 0 if envelope["outcome"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
