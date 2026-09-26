# Conditional bounded-executor evaluation

`tests/executor_trial.py` keeps trial eligibility separate from result
accounting. A measured residual controller cost can make a future exploratory
trial eligible, but an incomplete fixture outcome, a failed verifier, zero
expected work, or a mismatched count is never eligible for a completed-task
economic result. Unknown external measurements remain unknown.

The result collector and comparison runner record the contract a future trial
must satisfy:

```sh
uv run python tests/executor_trial.py
```

An eligible run needs an explicit fixture version and seed, selected route,
verification policy, task intent, semantic or validated landmark targets,
destination, deadline, cancellation, exclusive reservation ownership, child-
death cleanup or lifecycle expiry, partial-action receipts without replay, and
the complete-task verifier. The collector records public job, round, and
attempt identities, plus task-version/context when supplied, and requires one
of the four supported round shapes.
Failed, aborted, retried, and paid-assistance attempts remain separate records.
Exit 40 remains a user abort and is never retried. Stale or missing targets
return to the task controller. Worker time, CU/application/transport/model
boundaries, tool/image and token counters, actual billing, quota consumption,
and estimates are separate fields with explicit coverage.

The collector consumes the delegating harness's public round/context fields; it
does not implement worker lifecycle. Exploratory collection may
run without a baseline, while economic conclusions require comparable verified
outcomes and actual reported billing. The trial does not add a supervisor,
queue, daemon, permanent input device, quota retry loop, or autonomous pixel
aiming.
