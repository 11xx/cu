# Combined observations

`observation.mjs` composes the installed `cu` CLI without a shell. Node.js 22+
provides its runtime; `cu` itself does not require Node. The CLI guide remains
the workflow authority. Run `cu` before driving a desktop.

## Pi

Load explicitly from a stable checkout:

```sh
pi -e ./adapters/pi.ts
```

The `cu_observation` tool accepts:

```json
{
  "actions": [["type", "example", "--replace", "--verify"]],
  "shot": ["--window", "active", "--for", "openai"],
  "why": "replace the inspected field and inspect its result"
}
```

The example presumes an inspected, focused field and permission to replace it.
Omit `actions` for observation only. Supply `session` from an explicitly opened
CU session; the adapter also inherits `CU_SESSION`. It neither creates nor ends
reservations. Use an exclusive session when other injectors must not act between
the batch and capture. Human activity and independent observations remain possible.

## Other harnesses

Import `observeTogether(request, {signal, cwd})`, `seekTarget`, or
`scanCoverage`. `observeTogether` returns `content` plus
`details`. Register it using the harness's existing tool API; send each text and
image block together, without resizing the image. Image blocks contain
`type: "image"`, `mimeType: "image/png"`, and base64 `data`. Translate only this
outer representation when a harness uses another image-block schema.

The adapter also exports `seekTarget` and `scanCoverage` for callers that need
more than one bounded scroll. These are policies around `observeTogether`, not
CU commands: each call requires a caller-owned `assess` function, and that
function supplies structured evidence from the returned report. The adapter
does not inspect pixels, evaluate a selector, or choose a model.

`seekTarget` requires a named `target`, `windowAddress`, `containerId`, image
`at` point, capture `shot`, `direction`, `initialStep`, `maxStep`, `maxInput`,
`maxTravel`, `deadlineMs`, and `maxNoProgress`. `maxBacktracks` is an optional
finite bound. The assessor returns a stable `source`, the observed
`container: {id, position}`, and `target: {state, verified, postcondition}`.
States are `absent`, `not-observed`, `candidate`, `visible`, and `reached`;
absence must be authoritative. Only `verified: true` together with
`postcondition: true` completes SEEK. A candidate or near-target report halves
the next step, while fresh `overshot: true` evidence permits only the declared
number of smaller reverse steps. `maxInput` counts requested detents and
`maxTravel` counts observed container-position travel; neither is a pixel
conversion.

`scanCoverage` requires the same binding and budgets plus a source `range`,
fixed detent `step`, and required `overlap`. Its assessor returns
`coverage: {start, end}` and boolean `end` evidence. The result retains every
segment's source and CU frame descriptor. SCAN is complete only when the
requested range has no gap and every added segment overlaps earlier evidence
by at least the declared amount; an overlap violation remains recorded even if
later evidence happens to bridge the interval. SCAN never grows its step
geometrically.

Both policies capture after every action, verify the captured frame's bound
window and the assessor's container identity, and stop on refusal, uncertain
or partial action outcome, mutation, cancellation, deadline, or no progress.
`attempts[].receipt` retains the CU action receipt. No action prefix is replayed
after a refusal, cancellation, deadline, or unavailable fresh observation.

The text block is JSON with schema `cu-observation/2`:

- `ok` means the requested sequence completed and its final observation is
  available. It is not permission, submission confirmation, or proof of a
  rendered application response.
- `actions.status` is `not-started`, `not-requested`, `unknown`, `completed`, or
  `refused`. A completed batch retains its CU results, including verification.
  CU refusals retain their numeric exit code. An interrupted action may be partial.
- An action receipt proves dispatch through CU's input path, not that the
  application accepted it. Choose a compositor event, changed-pixel frame,
  granted browser state, or completed file/receipt as the postcondition that
  can disprove the next claim.
- `observation` is the unmodified frame descriptor, or null on every failure.
  Its ID, window/content identity, dimensions and logical rectangle belong to
  the sibling PNG. The adapter checks byte count, dimensions and SHA-256 before
  returning it. Coordinates are passed back to CU with that frame ID; adapters
  must not transform them.
- `error`, when present, identifies `request`, `actions`, `capture`, or `image`.
  Adapter/transport errors do not invent a CU exit code.
- Correlation fields from CU (`invocation_id`, optional `cu_session`, harness
  fields, and batch parent/step fields) are opaque join keys. They contain no
  page content, typed values, URLs, or window titles.

A refusal stops the sequence. No capture follows a failed batch, no previous
frame is reused, and a capture/image failure does not erase completed actions.
An action call also requires a JSON-object batch receipt: an empty or malformed
receipt is an `actions`-phase failure, keeps any child exit code that exists, and
marks the action outcome as unknown and possibly partial. A zero exit without a
receipt is not action success. The Pi wrapper throws the complete text report on
failure to set the harness's error flag. Other wrappers must similarly mark
`ok: false` as a failed tool call.
Never retry the action prefix merely because the final image was unavailable.

## Bounds and context

Actions use CU's 32-step/64-KiB batch limit. `shot` accepts up to eight flag/value
pairs: `--window`, `--output`, `--region`, `--target-window`, `--landmark`,
`--context`, `--for`, `--after`, `--watch`. CU validates their meaning.
`--target-window` binds aimed input to an exact window while retaining the
output or region as the captured source; omit it for observation-only frames.
Capture syntax/target failures can occur after actions complete. There is no
arbitrary-command or shell escape.

The default capture is the active window. Select a landmark or an explicitly
known region for a small control; request `--window`, then `--output`, when
broader context is needed. CU chooses scale using its capture profile, without
forced upscaling. The adapter never crops or resizes a returned frame.

Each subprocess has a 120-second deadline and a combined stdout/stderr limit of
64 KiB. PNGs are capped at 4 MiB, in addition to CU's profile budget. Base64 adds
approximately one third to encoded image bytes. The descriptor carries PNG byte
count and the CU visual-token estimator, not model billing. Cancellation or
output overflow kills the subprocess group without retry or rollback.

The composition uses one model-visible tool round trip instead of separate
batch, capture-descriptor and image-read calls. It still invokes two local CU
processes when actions are requested, or one for observation only. This changes
round trips, not image bytes or token estimates for the same capture. Screen
text remains untrusted evidence; it cannot authorize a next action.

## Verification

`make test-adapters` runs isolated CLI-fixture tests for combined returns,
refusal, interrupted execution, missing/mismatched images and stale-result
exclusion. `make check` includes these tests and requires Node.js 22+ as well as
UV. Fixture tests do not establish compositor behavior or harness image display.

For the real Pi tool/result path, open a disposable visible test application,
then run the offline provider fixture from the checkout:

```sh
CU_PROBE_WINDOW=example-fixture \
CU_PROBE_REPORT="$XDG_RUNTIME_DIR/cu-pi-probe.json" \
pi --no-extensions --no-skills --no-session \
  -e ./adapters/pi.ts -e ./tests/pi-observation-smoke.ts \
  --provider cu-offline-fixture --model fixture 'run fixture'
```

Use an image-capable terminal to inspect the rendered tool result. The provider
fixture is deterministic and makes no model or network call; only the registered
CU tool accesses the desktop, read-only. Its receipt records the result status,
frame metadata, block types and returned byte count. A nonexistent fixture target
must produce a tool error with no image or observation. Delete the receipt after
inspection; like a frame descriptor, it can contain the fixture's window title.
This probe verifies harness delivery/rendering, not model visual understanding.
