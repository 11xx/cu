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

Import `observeTogether(request, {signal, cwd})`. It returns `content` plus
`details`. Register it using the harness's existing tool API; send each text and
image block together, without resizing the image. Image blocks contain
`type: "image"`, `mimeType: "image/png"`, and base64 `data`. Translate only this
outer representation when a harness uses another image-block schema.

The text block is JSON with schema `cu-observation/1`:

- `ok` means the requested sequence completed and its final observation is
  available. It is not permission, submission confirmation, or proof of a
  rendered application response.
- `actions.status` is `not-started`, `not-requested`, `unknown`, `completed`, or
  `refused`. A completed batch retains its CU results, including verification.
  CU refusals retain their numeric exit code. An interrupted action may be partial.
- `observation` is the unmodified frame descriptor, or null on every failure.
  Its ID, window/content identity, dimensions and logical rectangle belong to
  the sibling PNG. The adapter checks byte count, dimensions and SHA-256 before
  returning it. Coordinates are passed back to CU with that frame ID; adapters
  must not transform them.
- `error`, when present, identifies `request`, `actions`, `capture`, or `image`.
  Adapter/transport errors do not invent a CU exit code.

A refusal stops the sequence. No capture follows a failed batch, no previous
frame is reused, and a capture/image failure does not erase completed actions.
The Pi wrapper throws the complete text report on failure to set the harness's
error flag. Other wrappers must similarly mark `ok: false` as a failed tool call.
Never retry the action prefix merely because the final image was unavailable.

## Bounds and context

Actions use CU's 32-step/64-KiB batch limit. `shot` accepts up to eight flag/value
pairs: `--window`, `--output`, `--region`, `--landmark`, `--context`, `--for`,
`--after`, `--watch`. CU validates their meaning. Capture syntax/target failures
can occur after actions complete. There is no arbitrary-command or shell escape.

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
