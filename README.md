# ai-agent-computer-use

Computer use for AI coding harnesses on Linux/Wayland, targeting Hyprland.

Gives a multimodal agent the ability to see the screen and drive the pointer,
keyboard, and windows of a real desktop session, through a deterministic CLI
that any harness can call as a shell command. Claude Code and Codex are the
initial consumers; nothing in the interface is specific to either.

## Install

```sh
make install                         # puts `cu` on PATH
```

The complete-task fixture and verifier are documented in
[docs/benchmark.md](docs/benchmark.md). It is a local synthetic task and does
not establish controller speed, model cost, or native chooser behavior.

The disposable headless-output interaction demo is documented in
[docs/publication-demo.md](docs/publication-demo.md); its GIF is local evidence,
not a hosted product claim.

The caller-managed task context is described in [docs/routines.md](docs/routines.md).

The conditional executor decision and future trial contract are in
[docs/executor-trial.md](docs/executor-trial.md); unknown costs keep the current
result at no-trial.

An editable install, so the command on PATH is this checkout rather than a copy
of it — a stale binary is not a failure mode this project should have. It also
means the checkout has to stay put. Run `make install` when declared dependencies
change; editable source tracking does not refresh the installed environment.
Without installing, `bin/cu` is the same
command and needs no knowledge of `uv`; every example below works either way.

## Prerequisites

Run `bin/cu doctor --json` from the checkout before installing, or `cu doctor`
after `make install`. A usable session provides `hyprctl` inside a running
Hyprland session, the Lua functions `hl.dispatch` and
`hl.dsp.cursor.move`, `XDG_RUNTIME_DIR`, writable `/dev/uinput`, and the
required `grim` capture binary. The doctor also checks the optional clipboard
and region-selection binaries (`wl-copy`, `wl-paste`, and `slurp`).

Input commands refuse while a lock screen is running, while an unfiltered
`xremap` process could rewrite events, or while the user's abort flag is set.
Those are live safety checks rather than installation steps. The doctor reports
each check as `ok`, `warn`, or `fail`, with the remedy attached to the result.

## Orientation

```sh
cu                                   # the workflow guide; no arguments needed
```

A bare `cu` prints what a session needs before its first command: observe
before capturing, act through the frame you read, what each exit code obliges,
how hard the driving model should think, and which interlocks exist. `--help`
is the per-command reference and points back at it. There is no skill or prompt
fragment to install alongside — the CLI is the only workflow surface, so a
caller that reaches straight for `cu click` can still find out what it owes.

## Playground

Run `cu playground` and open its printed loopback URL in your browser. It is a
ten-task native suite—two baseline tasks and eight named synthetic fixtures—with
explicit Start/retry/reset controls, immutable terminal attempt receipts and
visible PASS/FAIL/ABORTED results; no extension is required. Ctrl-C stops the
explicitly launched foreground server. See [the playground workflow](docs/playground.md)
for experimental, separately orchestrated controller/worker trials and separate
token/cost, speed and outcome measurements; `cu` never selects or launches a
worker, and trial evidence is not a product claim.

## Usage

```sh
cu doctor                        # verify every environment precondition
cu abort                         # the user's interlock: --set, --clear, or report
cu observe                       # monitors, windows, layer surfaces, focus, cursor
cu shot                          # capture the focused window
cu shot --output <name>          # a whole monitor; `cu observe` names them
cu shot --region '100,100 400x200'         # desktop-absolute
cu shot --output <name> --region '0,0 900x26'  # relative to that output
cu shot --output <name> --target-window <selector> # bind aimed input to one window
cu shot --if-changed             # reuse the last frame if nothing moved
cu shot --after f3 --watch '0,0 200x26' # recapture once pixels change and settle
cu shot --for openai             # capture at another consumer's budget
cu shot --timings --json         # nonpersistent component/cost report on stderr
cu pixel --frame f3 --at 412,300 # exact r,g,b,a and #RRGGBBAA from stored pixels
cu magnify --frame f3 --scale 3  # nearest-neighbour derived frame; no recapture
cu export --frame f3 --json      # bounded evidence envelope; read-only
cu profiles                      # what a capture costs each consumer

cu wait window-open --class zen  # block on a compositor event
cu focus zen                     # give a window keyboard focus
cu present zen --why 'make the target readable' # explicit bounded arrangement
cu restore <token> --why 'return the target'    # restore only unchanged state
cu warp  --frame f3 --at 412,300 # place the compositor pointer exactly
cu move  --frame f3 --at 412,300 # bounded native movement to a frame coordinate
cu move  --frame f3 --at 412,300 --timings --json # native cost evidence
cu hover --frame f3 --at 412,300 --dwell 0.2 --watch full # enter, dwell, observe
cu click --frame f3 --at 412,300 # warp, check the target, press and release
cu scroll --dy -3                # wheel where the pointer already is
cu type 'hello'                  # clipboard by default, --method keys to synthesize
cu type 'hello' --verify         # read the field back and confirm it arrived
cu type 'new value' --replace --verify # whole-field replacement, exact read-back
cu batch steps.json --json       # bounded argv arrays, fail-fast with partial results
cu key --action paste            # a semantic action, resolved per application
cu browser list                  # installed browsers and what each resolves
cu browser products              # explicit Chrome/Chromium product candidates
cu browser focus-urlbar          # a semantic browser action, never a chord
cu recipe save open-url --last 4 # promote a successful run from the log
cu recipe replay open-url --param text1=example.com

cu know set zen urlbar --rect 0.1,0.02,0.6,0.04   # remember where to look
cu know host 'keys pass through a remapping layer'  # a fact about the machine
cu shot --landmark zen/urlbar    # aim a capture, widened as the window drifts
cu sandbox create                # a headless output, when you want one

export CU_SESSION=$(cu session begin --why 'booking a flight')   # tell the user
cu click --frame f3 --at 412,300 --why 'accepting the fare'
cu status                        # a line per driving agent; empty when idle
cu session end
```

Every injecting command takes `--dry-run`, which runs every interlock and
writes the same log entry without emitting a single event.

`cu present` and `cu restore` are explicit compositor arrangements, not capture
side effects. Presentation requires a visible window, records actual
before/after state in a volatile token, and may refuse or leave a typed partial
outcome when read-back disagrees. Restoration refuses after a window identity
or relevant arrangement state changes; it never overwrites that current state.

Receipts and opt-in metrics carry a generated `invocation_id`, the explicit CU
session when named, and optional `CU_HARNESS`/`CU_HARNESS_SESSION` join keys.
Batch children add their parent invocation and step number. These fields are
correlation metadata only; they do not include page content, typed values, URLs,
or window titles.

`cu` acts on the **visible desktop** by default — a bare `cu shot` captures the
focused window on a real monitor, and nothing selects a headless output on its
own. Being watchable is a safety property: `cu sandbox` exists so that working
where nobody can see is a deliberate, named act.

All accept `--json`. `bin/cu` is a plain command — it resolves `uv` itself, so
a caller needs no knowledge of the Python underneath.

Capture budgets are configuration. A **profile** describes a consuming model by
capability — the long edge to downscale to, the patch size it bills in, its own
cap — and every profile inherits `generic`, stating only what it changes. So
teaching `cu` about a new consumer is a stanza under `[capture.profiles]`, and
a field nobody has measured falls back to a conservative default rather than a
guess. `cu profiles` shows what each resolves to; `--for` overrides per command.

Window/landmark capture refuses hidden or geometrically obstructed targets
with exit 27; it never silently focuses or switches workspaces. Explicit output
or region capture remains available without asserting window identity. Add
`--target-window <selector>` to an output or region capture when later aimed
input must be bound to that exact window; the frame keeps `window: null` and
retains a separate target descriptor so an obstructed dialog can be observed.
Input refuses with exit 31 when a top or overlay layer or ambiguous overlap
prevents establishing that recipient. Unbound frames and their action receipts state that
no recipient assertion exists.

`cu observe` reports each window's outer-bounds extent separately from its
active-output reachability. Reachability includes per-output intersections and
scale, reachable/unreachable area, and bounded interior gaps; a point in a
monitor gap is outside the active output union and aimed input refuses with exit
34 before moving the pointer or creating a device. Window-capture exit 27
refusals expose the same bounded obstruction facts in JSON and text: class,
title, address, intersection, containment, and an explicit transient-ownership
state. Geometry does not establish stacking, parenthood, layer event routing,
or input authority; unsupported compositor facts remain marked unsupported.

`shot` writes a PNG plus a **frame descriptor** recording the logical rectangle
the image depicts, its pixel size, and what it costs the consumer it was
captured for. Later commands take coordinates as
`--frame <id> --at x,y` in image pixels, and `cu` maps them back to the screen.
`warp` uses the compositor for instantaneous placement. `move` uses one bounded
uinput device and native relative samples; it requires the starting pointer and
the complete segment to remain inside the inspected frame. Its receipt reports
the requested duration, sampling rate, actual endpoint, and convergence status.
`hover` composes that native entry with a finite dwell and a fresh observation.
`--watch` waits for a bounded pixel box to change and settle; the result is pixel
evidence and keeps application meaning unknown until the caller inspects it.
`pixel` reads an exact stored pixel, or an explicitly requested median box,
without producing another image. `magnify` makes a nearest-neighbour derived
frame for reading small details; it preserves the source capture time and
logical rectangle while recording its larger image size and token cost.
`export` validates one frame and returns a versioned evidence envelope; it can
compare equal-coverage pixels or retain a copy only at an explicit new path.

`cu` publishes what it is doing to `$XDG_RUNTIME_DIR/cu/status.json` — one
small object, replaced atomically, which `cu status --follow --json` turns into
a change-only feed for a status bar. It does not draw an overlay: `grim` captures
layer surfaces, so anything `cu` drew would end up in the frames the model
reads. Entries expire, because nothing writes "finished" for a killed process.

More than one agent may be driving one desktop, so that object is a registry
with an entry per driver rather than a single record, and `cu status` lists
them all. A session is claimed explicitly — `cu session begin` prints an id
that later commands pass through `CU_SESSION` or `--session` — because
inferring it from process ancestry would file one agent's actions under
another's intent.

```sh
export CU_SESSION=$(cu session begin --why 'booking a flight')
cu click --frame f3 --at 412,300 --why 'accepting the fare'
cu session end
```

Add `--exclusive` to `session begin` when the desktop must remain unchanged
between those commands. While that session is live, only injecting commands
that name its id through `CU_SESSION` or `--session` may act; other injectors
get retryable exit 44 naming the reservation. Ordinary sessions reserve
nothing, and observation remains concurrent.

The commands that reach the shared pointer, keyboard focus, and clipboard hold
a host-wide `flock` while they run, so only one agent injects at a time. A
second one waits briefly and then gets exit 42 naming the holder; reading —
`observe`, `shot`, `pixel`, `magnify`, `wait`, `status`, `doctor` — is never
serialized. Without this, two agents warping the same pointer produce a click
on the wrong window at exit 0.

## Combined harness results

[`adapters/`](adapters/README.md) provides a portable action/status/frame/image
composition and a Pi tool wrapper. It calls the existing CLI and uses the
harness's native image return, with no daemon or extra transport. The CLI guide
contains its safety and capture-selection rules. JavaScript tests use pinned npm
dependencies and the Node versions declared in `package.json`. `make check`
installs these dependencies; the Python CLI does not require Node.

## Optional Firefox CU Browser

[`browser-extension/`](browser-extension/README.md) supplies structured page
reads, condition waits, explicitly granted DOM actions, and browser viewport
capture. `cu web setup` registers its browser-owned native host; the extension
popup grants either reads or page interaction for one tab. Read grants never
silently acquire action authority. No all-sites permission, TCP listener, or
persistent page archive is required. Pixel control works without the extension.
Browser evidence is untrusted, CSS rectangles are not mapped to desktop frames,
and iframe/shadow-tree contents are unsupported. Run bare `cu` for the consent,
freshness, action-uncertainty, and refusal contracts before enabling a tab.

## The eww indicator

`eww/cu.yuck` and `eww/cu.scss` are a ready widget: one mark per driving agent,
a tooltip carrying that agent's own `--why` and how long it has been running,
hidden entirely while nothing is happening, and replaced by a stop mark once
the user's abort flag is set. It listens to `cu status --follow --json`, whose
worst-case staleness is its 250 ms derived-state poll interval. Clicking a mark
sets that flag.

They name no path, colour, or monitor, so they are included by relative path
and styled by the configuration that includes them:

```yuck
(include "../../code/ai-agent-computer-use/eww/cu.yuck")
...
(cu-indicator)
```

```scss
@import "../../code/ai-agent-computer-use/eww/cu.scss";
.cu__driver  { color: $yellow; }
.cu__aborted { color: $red; }
```

## Telling an agent it is there

An agent reaches for the desktop only if it knows the desktop is reachable, so
one short section belongs in the context file of any project where that might
come up. [AGENTS.md.example](AGENTS.md.example) is that section, and it is
deliberately five lines: the fact that `cu` exists, what it is for, and that
bare `cu` teaches the rest. Anything more is a copy of the guide that goes stale
without anything failing.

## Status

`doctor`, `observe`, `shot`, `focus`, and the input commands — `move`, `click`,
`scroll`, `type`, `key` — are implemented, with the safety interlocks, as is
`wait`, `browser`, `recipe`, `know`, `sandbox`, and the `status`/`session`
feed a status bar reads, with an eww widget for it. Concurrent agents are
serialized on the desktop and reported individually. Every command in the
design is built — see
[docs/design.md](docs/design.md) for the command surface and build order, and
[docs/environment.md](docs/environment.md) for the verified host facts the
design rests on.

## Development

`make check` runs the complete local gate: Python tests, native-playground Node
tests, adapter/extension Node tests, Ruff, and the package build. The tool
and development requirements are declared in `pyproject.toml`; `uv.lock` and
`package-lock.json` pin the resolved Python and JavaScript dependencies.

```sh
make check
```
