# Task-local reusable context

`tests/routine_checkpoint.py` defines the bounded, caller-managed context used
for a task that may need a warm or cold follow-up. The caller chooses where to
serialize it and supplies the current task plus the artifact root when asking
whether completed effects can be reused. CU does not own the context or load
it implicitly.

Each context carries a versioned exact identity: task ID, intent, destination,
revision, document, scope, and parameter values. Resume compares all of those
fields. A changed task, destination, document, scope, or parameter set refuses
reuse before any completed effect is selected.

Completed entries are verified receipts, not membership markers. Every entry
names a relative artifact path, expected size, lowercase SHA-256 digest, and
`verified` status. `resume(..., artifact_root=ROOT)` checks that the file is a
regular file inside the caller's root, remains stable during the read, and
matches both size and digest. Missing, changed, unreadable, out-of-root, or
unverifiable entries are returned as `reconsider`; only entries that pass the
check appear in `skip_completed`. Resume performs no write, retry, or replay.

Pending exceptions remain separate from completed effects. An unresolved
exception wins over a completed membership entry for the same artifact, so a
collision or delayed item returns to the caller's decision boundary. The
context's run record preserves round shape (`full-start`, `prepared-page`,
`warm-follow-up`, or `cold-resume`), attempt identity, first-attempt status and
outcome, failures, and explicit recovery IDs. The complete-task fixture (`tests/benchmark_task.py`) exposes both the
resume decision and eventual outcome without turning a recovery into a new
first-attempt result.

After inspecting that result, the caller may hand a decided action chunk to
the existing `cu batch`, a separately reviewed recipe, or a worker handoff.
The context supplies no execution engine and does not silently retry an
exception.

Frame and lease bindings are historical evidence. They are serialized only as
historical records, and resume always returns empty rebound-frame and
rebound-lease lists. A caller must reacquire a fresh target through CU and
inspect it before acting; a stored coordinate, frame ID, or lease ID cannot
authorize input.

The codec bounds strings, nesting, collections, artifact verification reads,
and the complete JSON document. It rejects body/content/URL fields and URL-like
values, so private source bodies and signed URLs do not become reusable task
data. The context is not a recipe, contextual `cu know` entry, generic state
machine, durable store, or approval mechanism. Recipe review rules and
contextual-knowledge promotion remain separate explicit decisions. Fixture
receipts are evidence for recovery semantics only; they do not establish
controller cost or savings.
