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
FORMATS = ("md", "txt", "js", "json", "c")
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


def verify(root: Path, items: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Verify exact content, count, and stability of every produced artifact."""
    items = items or expected_items()
    expected = Counter(item["sha256"] for item in items)
    files = sorted(path for path in root.rglob("*") if path.is_file())
    failures: list[dict[str, Any]] = []
    actual: Counter[str] = Counter()
    for path in files:
        before = path.stat()
        data = path.read_bytes()
        after = path.stat()
        if before.st_size == 0:
            failures.append({"kind": "zero-byte", "path": str(path.relative_to(root))})
            continue
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            failures.append({"kind": "changing", "path": str(path.relative_to(root))})
            continue
        actual[hashlib.sha256(data).hexdigest()] += 1
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
    delay_seconds: float = 0.0,
    fail_ids: set[str] | None = None,
    recover: bool = True,
    unexpected_dialog: bool = True,
) -> dict[str, Any]:
    """Run one local fixture trial and return a bounded benchmark envelope."""
    if mode not in {"individual", "batch", "combined"}:
        raise ValueError("mode must be individual, batch, or combined")
    if delay_seconds < 0 or delay_seconds > 10:
        raise ValueError("delay_seconds must be between 0 and 10")
    items = expected_items(seed)
    by_id = {item["id"]: item for item in items}
    fail_ids = set(fail_ids or {"item-03"})
    unknown = fail_ids - by_id.keys()
    if unknown:
        raise ValueError(f"unknown failure ids: {', '.join(sorted(unknown))}")
    started = time.perf_counter()
    receipts: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    recovery: list[str] = []
    output = root / "artifacts"
    output.mkdir(parents=True, exist_ok=True)
    application_started = time.perf_counter()
    batches = [[item] for item in items] if mode == "individual" else [items]
    for batch in batches:
        for item in batch:
            if item["id"] in fail_ids:
                failures.append(
                    {"id": item["id"], "stage": "bulk" if mode != "individual" else "item"}
                )
                receipts.append({"id": item["id"], "status": "failed"})
                continue
            if delay_seconds and item["id"] == "item-07":
                time.sleep(delay_seconds)
            _write_item(output, item)
            receipts.append({"id": item["id"], "status": "completed"})
    if recover:
        for failure in failures:
            item = by_id[failure["id"]]
            _write_item(output, item)
            recovery.append(item["id"])
            receipts.append({"id": item["id"], "status": "recovered"})
    application_seconds = time.perf_counter() - application_started
    verification = verify(output, items)
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
        },
        "trial": {"id": uuid.uuid4().hex, "mode": mode},
        "controller": {
            "runtime": os.environ.get("CU_CONTROLLER", "unknown"),
            "model": os.environ.get("CU_MODEL"),
            "effort": os.environ.get("CU_EFFORT"),
        },
        "outcome": "completed" if verification["ok"] else "incomplete",
        "verification": verification,
        "receipts": receipts,
        "failures": failures,
        "recovery": {"attempted": recover, "ids": recovery},
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
    parser.add_argument("--delay", type=float, default=0.0)
    parser.add_argument("--fail", action="append", default=[])
    parser.add_argument("--no-recovery", action="store_true")
    parser.add_argument("--no-dialog", action="store_true")
    args = parser.parse_args(argv)
    fixture_root = Path(tempfile.mkdtemp(prefix="cu-task-", dir=os.environ.get("XDG_RUNTIME_DIR")))
    try:
        envelope = run_trial(
            fixture_root,
            mode=args.mode,
            seed=args.seed,
            warm=args.warm,
            delay_seconds=args.delay,
            fail_ids=set(args.fail) or None,
            recover=not args.no_recovery,
            unexpected_dialog=not args.no_dialog,
        )
        print(json.dumps(envelope, indent=2))
    finally:
        shutil.rmtree(fixture_root)
    return 0 if envelope["outcome"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
