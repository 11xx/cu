# Conditional bounded-executor evaluation

The current B12 decision is **no trial**. B10's deterministic fixture and B11's
checkpoint prototype leave residual controller cost, CU timing, harness
transport, model time, quota use, and billing unknown. Starting a delegated
executor without that baseline could not show a saving, so no child process or
paid experiment is launched.

`tests/executor_trial.py` records the decision and the contract a future trial
must satisfy:

```sh
uv run python tests/executor_trial.py
```

An eligible run needs an explicit task intent, semantic or validated landmark
targets, destination, deadline, cancellation, exclusive reservation ownership,
child-death cleanup or lifecycle expiry, partial-action receipts without replay,
and the B10 complete-task verifier. Exit 40 remains a user abort and is never
retried. Stale or missing targets return to the task controller. Any child/lead
usage, failures, recovery, and paid escalation are recorded separately from CU
timings and model time.

The trial may use an existing routed harness through `agent-routing` and
`agent-delegate` only after measured residual cost makes it eligible. It does
not add a supervisor, queue, daemon, permanent input device, or autonomous
pixel aiming.
