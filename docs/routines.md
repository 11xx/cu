# Task-local checkpoint prototype

`tests/routine_checkpoint.py` models the smallest caller-managed checkpoint
that can resume a verified task without repeating completed effects. It stores
task intent, destination and revision, parameter names, observed affordances,
completed artifact IDs/receipts, pending exceptions, and frame bindings marked
as historical.

Resume validates the exact task identity, returns completed artifact IDs to skip,
returns pending exceptions for a new decision, and marks every old frame for
fresh observation. A source filename is metadata; the artifact ID and content
digest remain the verifier's identity. Coordinates are never rebound by the
prototype, and an abort refuses resume.

The prototype uses ordinary JSON files and the existing batch/recipe/combined
observation primitives. It does not own durable storage, approve recipes,
interpret a generic state machine, run an autonomous coordinate loop, or promote
site facts into `cu know`. The selected production boundary is caller-managed
and session-local: it expires with the named task, validates intent and
destination, and never auto-approves or promotes itself. A durable recipe or
contextual knowledge entry still needs an explicit human-reviewed promotion
with scope, freshness, and invalidation. The B10 cost and savings comparison
remains open.
