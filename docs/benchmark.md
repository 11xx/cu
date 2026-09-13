# Complete-task benchmark fixture

`tests/benchmark_task.py` is a private-data-free task model for measuring
completion and recovery boundaries before a controller comparison. It creates
16 deterministic Markdown, text, JavaScript, JSON, and C artifacts. Two pairs
share basenames, item 2 carries a different-content collision, item 7 can be
delayed, item 3 fails the initial bulk/item pass, and an unexpected dialog is a
receipt rather than an artifact.

Run one bounded local trial from the repository root:

```sh
uv run python tests/benchmark_task.py --mode batch --seed 1 --delay 0.1
```

The JSON envelope records the fixture revision and seed, mode, warm/cold label,
runtime identity, receipts, failures, recovery IDs, verifier counts, and
separate application/CU/harness/model timing fields. Unknown external timing,
visual-token cost, quota use, and monetary billing remain `null`; no allowance
percentage is converted into a price or speed claim. `CU_INVOCATION_ID` and
`CU_HARNESS_SESSION` are copied only when a caller supplies them.

The independent verifier maps each artifact id to its exact
`<artifact-id>/<basename>` path and declared format, then checks file stability
and SHA-256 content. It rejects zero-byte, changing, missing, unexpected-name,
format-mismatch, wrong-content, and duplicate-count-only results. Duplicate
basenames are valid only in their separate artifact-id directories. Each trial
also records a receipt for differing pre-existing data kept outside the
artifact tree and requires that data to remain unchanged. A complete outcome
requires all 16 expected paths and digests. `--no-recovery` deliberately leaves
the failed item missing so the envelope reports an incomplete outcome.

`individual`, `batch`, and `combined` are fixture execution shapes with the same
expected output; they do not simulate CU subprocesses or a model. A comparative
controller run must attach real CU invocation/measurement evidence, use equal
verified outcomes, include failed trials and cold/warm states, and keep any
native chooser or paid-provider result outside this synthetic fixture.
