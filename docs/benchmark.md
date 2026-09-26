# Complete-task benchmark and accounting fixture

`tests/benchmark_task.py` is a private-data-free task model for measuring
completion and recovery boundaries for a controller comparison. It creates 16
deterministic Markdown, text, JavaScript, JSON, and C artifacts. Two pairs share
basenames, item 2 carries a different-content collision, item 7 can be delayed,
item 3 fails the initial bulk/item pass, and an unexpected dialog is a receipt
rather than an artifact.

Run one bounded local trial from the repository root:

```sh
uv run python tests/benchmark_task.py --mode batch --seed 1 --delay 0.1
```

The envelope records the fixture revision and seed, mode, round shape, runtime
identity, item receipts, every attempt, failures, recovery IDs, verifier
counts, and separate application/CU/harness/model timing fields. It also
contains the caller-managed routine context and the resume decision that joins
the verified prefix to the fixture's attempt receipts. The allowed round shapes are
`full-start`, `prepared-page`, `warm-follow-up`, and `cold-resume`. A failed
attempt remains present when recovery creates a new retry attempt.
`--interrupted` records an aborted partial run and the context refuses resume;
the fixture does not turn an abort into automatic recovery.

The independent verifier maps each artifact id to its exact
`<artifact-id>/<basename>` path and declared format, then checks that path's
size and SHA-256 content while reading it for stability. It rejects zero-byte,
changing, missing, unexpected-name, format-mismatch, per-artifact mismatch,
wrong-content, and duplicate-count-only results. Duplicate
basenames are valid only in their separate artifact-id directories. Each trial
also records a receipt for differing pre-existing data kept outside the
artifact tree and requires that data to remain unchanged. A complete outcome
requires all 16 expected paths and digests. `--no-recovery` deliberately leaves
the failed item missing so the envelope reports an incomplete outcome.

The routine context contains only relative artifact receipts with expected size
and SHA-256 digest. Its resume decision rechecks those files and reports
missing or changed entries for caller judgment; only still-verified entries
are marked `skip_completed`. The context stores pending exceptions and
historical runtime bindings but never reuses a frame or lease identifier. The
fixture's explicit recovery loop handles its failed item after that decision,
so the first-attempt and eventual outcomes remain separate. This synthetic
context evidence does not measure a controller saving or justify a cost claim.
The `cold-resume` fixture shape round-trips the context through its
caller-selected fixture root before making the resume decision; warm shapes
keep the same contract in memory.

`individual`, `batch`, and `combined` are fixture execution shapes with the same
expected output; they do not simulate CU subprocesses or a model. A comparative
controller run must attach real CU invocation/measurement evidence, use equal
verified outcomes, include failed, aborted, retried, and paid-assistance
attempts, and keep any native chooser or paid-provider result outside this
synthetic fixture.

## Per-round result collection

`tests/result_collector.py` joins a fixture version/seed, run ID, public worker
job/round/attempt identity, and the independent verifier. It keeps operator
intent satisfaction, explicit-brief execution, and fixture/application outcome
as separate fields. Missing timing, tool-call, image/encoded-byte, token,
reported-billing, and quota evidence remains `null` rather than zero.

Backend counters retain their declared semantics, scope, session identity, and
coverage. A cumulative delta is derived only when both readings have compatible
coverage; a bounded read is never subtracted from a complete session. Uncached
input is derived only when the backend explicitly says cached input is a subset
of total input. Reported billing, quota consumption, and estimates are separate;
an estimate requires a named price source and version.

Run a small reproducible exploratory envelope by declaring every comparison
input:

```sh
uv run python tests/comparison_runner.py \
  --fixture-version cu-task-fixture/1 \
  --seed 1 \
  --route local-fixture \
  --verification-policy sha256-artifact-v1 \
  --shape full-start
```

The runner does not contact a provider or claim an economic result. Two reports
are comparable only when their fixture version, seed, verification policy, and
independent complete outcomes agree. A cost-per-completed-task value requires
actual reported billing attached to a verified attempt; no price is inferred.
