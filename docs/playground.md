# Explicit native playground

Run `cu playground` in a terminal and open its printed loopback URL in the
browser you intend to drive. No extension or grant is required. The launcher
serves the four declared assets from the packaged `cu.native_playground`
directory, binds only to `127.0.0.1`, and stays in the foreground until
Ctrl-C. `--port 8765` selects a port; the default asks the OS for a free one.
`--json` emits the URL, suite, asset list, extension requirement and foreground
lifecycle for a caller that manages the process. The command does not launch a
browser or create a worker-owned persistent service.

The page has ten independent native tasks: the two baseline Native basics and
Readiness and scroll tasks, plus eight named fixtures—Dispatch desk, Delayed
parcel, Archive elevator, Gallery crew, Queue keeper, Receipt ledger, Recovery
room and Changed desk. The named fixtures use deterministic variants selected
by the visible suite seed, so a seed replay recreates the same brief and layout.
Select a task and press **Start** before interacting with it. A still-unmet
objective remains `not-started` or `running`; only a committed wrong answer, a
forbidden effect or a deadline produces `FAIL`. User abort and runner
cancellation produce `ABORTED` with a cause. A successful objective produces
`PASS`. **Retry**, **Reset task** and **Reset run** are separate operations:
retry starts a new attempt, task reset returns that task to `not-started` while
retaining terminal attempt receipts, and run reset creates a new run while
moving the prior run into run history.

Dispatch desk shows a destination, quantity and notes brief. Replace all three
values and submit once; typing without submission stays pending, while a wrong
or duplicate submission fails. Delayed parcel shows one job ID: start that job
once, wait for its visible `Ready` state, then acknowledge the matching receipt
ID. A wrong or early acknowledgement fails, and the instruction is based on
the observed state rather than a prescribed sleep.

Archive elevator places a known `Bottom` target in a separately scrollable
`INNER` pane inside a nearby outer scroll area. The progress percentage is a
navigation clue only. PASS requires the target's reached receipt and matching
pane/target identity, not a magic numeric scroll offset. Gallery crew displays
representative cards whose controls are revealed by ordinary CSS `:hover` (or
focus within the card). Hover one card, select exactly the named subset, run
the visible group action once, then use only the simulated failed item's
recovery control; successful items remain untouched.

Queue keeper is a local mock player with a Current queue and a broader catalog
that deliberately repeats titles. Play the named object from Current queue,
not a same-title catalog object. PASS records object identity, one playback
advance and an unchanged queue sequence; the catalog control demonstrates the
false-success replacement and fails the attempt.

Receipt ledger requires the displayed filter and sort, an individual inspection
of every matching row, and the specified IDs and total. The **Contrast: seek
one known row** control is a separate target-search path: its receipt says that
it does not count as exhaustive coverage. A missing matching-row inspection,
extra row, wrong view or wrong answer produces an actionable FAIL.

Recovery room submits one synthetic job and then exposes an explicitly labeled
**In-page rename conflict**. Preserve the submitted data, confirm the named
replacement in that page dialog, and resume only the unfinished step. Resuming
does not submit another job. This fixture does not exercise an OS chooser,
filesystem output or a real external service.

Changed desk delays and then replaces the designated control. Wait for the
visible replacement, record a fresh observation and use the current target.
The adjacent preview decoy has a clear non-destructive effect and fails the
attempt without committing data; a stale target is also rejected.

Each terminal receipt records `task_id`, `task_version`, `seed`, `run_id`,
`attempt_id`, outcome, reason, evidence and cost fields. Receipts are frozen
after termination. Attempt history preserves earlier outcomes, failure reasons
and page wall time; controller tokens, monetary cost and subscription usage are
intentionally `null` until an external trial records them. Suite progress counts
tasks with at least one `PASS`, while first-attempt completion remains visible.

The compact task summary, holding suite progress and the run, stays pinned at the
top of the page while it scrolls. Task cards, run controls and capability
availability scroll in normal flow, so the pinned summary never covers a task's
interaction region at any window size; the selected objective and attempt status
lead that region. Capability availability is shown separately: the native input capability is available, while extension
grants and real-file output are unavailable for this synthetic suite. An
unavailable capability is not a task score.

The native suite owns only ordinary browser interaction. Extension diagnostics
remain in
[`browser-extension/fixtures/control.html`](../browser-extension/fixtures/control.html)
and use the separate Firefox extension test contract; they are not required by
native tasks and do not contribute to native-suite scores.
Do not treat the native page's synthetic readiness, mock playback, in-page
dialog, delayed layout or visible outcomes as a real external account, media
service, OS chooser, downloaded file or controller billing receipt.

Use normal `cu observe`, frame capture, native input and outcome verification.
If an external controller or worker is selected, treat the run as an
experimental, separately orchestrated comparison: give it the permitted task,
exact window identity, recent evidence, completion condition, tool/time bounds
and exceptions when the selected interface supports follow-up. CU does not
select or launch that worker implicitly. Record whole-task setup, recovery, wall
time, model/tool time, tokens and money separately; the page's wall-time field
is not an API invoice or a product benchmark.

This is a synthetic usability playground. Real browser/download, OS-dialog,
media-service and model-cost comparisons require separately recorded trials;
the native suite does not claim those capabilities.
